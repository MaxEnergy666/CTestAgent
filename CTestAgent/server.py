"""
FastAPI 后端：连接前端 UI 与 CTestAgent 多智能体测试系统。

启动命令：
    uvicorn server:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# 兼容两种启动方式：
# 1) 在 CTestAgent 目录下运行：python -m uvicorn server:app ...
# 2) 在工作区根目录运行：python -m uvicorn CTestAgent.server:app ...
THIS_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = THIS_DIR.parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))
if str(WORKSPACE_DIR) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_DIR))

from core.message_bus import MessageBus
from core.message_types import Message, Phase, AnalysisResult, ExecutionResult
from agents.coordinator import CoordinatorAgent
from agents.analyzer import AnalyzerAgent
from agents.generator import GeneratorAgent
from agents.executor import ExecutorAgent
from agents.reviewer import ReviewerAgent
from agents.reporter import ReporterAgent
from main import load_config, resolve_paths, setup_logging, ensure_mingw_in_path


logger = logging.getLogger("server")

PROJECT_ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = PROJECT_ROOT.parent
TARGETS_DIR = PROJECT_ROOT / "targets"
REPORTS_DIR = PROJECT_ROOT / "output" / "reports"
FRONTEND_DIST = WORKSPACE_ROOT / "ctestagent-ui" / "dist"


def _ensure_jsonable(obj: Any) -> Any:
    """将复杂对象（dataclass/enum/set/bytes）递归转换为可 JSON 化结构。"""
    if obj is None:
        return None

    if isinstance(obj, (str, int, float, bool)):
        return obj

    if isinstance(obj, Enum):
        return getattr(obj, "value", obj.name)

    if isinstance(obj, bytes):
        try:
            return obj.decode("utf-8", errors="replace")
        except Exception:
            return repr(obj)

    if isinstance(obj, Path):
        return str(obj)

    if isinstance(obj, dict):
        return {str(k): _ensure_jsonable(v) for k, v in obj.items()}

    if isinstance(obj, (list, tuple, set)):
        return [_ensure_jsonable(v) for v in obj]

    if is_dataclass(obj) and not isinstance(obj, type):
        return _ensure_jsonable(asdict(obj))

    if hasattr(obj, "__dict__"):
        data = {
            k: v for k, v in vars(obj).items()
            if not k.startswith("_")
        }
        return _ensure_jsonable(data)

    return str(obj)


TOPIC_TO_MAP = {
    "cmd:start_analysis": "Analyzer",
    "data:analysis_result": "Broadcast",
    "cmd:generate": "Generator",
    "data:test_cases": "Reviewer",
    "data:approved_cases": "Executor",
    "data:execution_results": "Broadcast",
    "data:coverage_update": "Broadcast",
    "data:executor_error": "Coordinator",
    "feedback:review": "Generator",
    "data:verified_crashes": "Broadcast",
    "feedback:strategy": "Coordinator",
    "cmd:phase_change": "Broadcast",
    "cmd:finalize": "Reporter",
    "data:report_ready": "Coordinator",
}


class WSConnectionManager:
    """WebSocket 客户端连接管理。"""

    def __init__(self):
        self.active: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self.active.add(websocket)

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            if websocket in self.active:
                self.active.remove(websocket)

    async def broadcast(self, payload: dict[str, Any]):
        async with self._lock:
            websockets = list(self.active)

        if not websockets:
            return

        dead = []
        for ws in websockets:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    self.active.discard(ws)


class TestRuntime:
    """测试运行时控制器（线程 + 状态 + 消息桥接）。"""

    def __init__(self, ws_manager: WSConnectionManager):
        self.ws_manager = ws_manager
        self.loop: asyncio.AbstractEventLoop | None = None

        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.lock = threading.RLock()

        self.status = "idle"
        self.phase = "INIT"
        self.current_round = 0
        self.last_error = ""
        self.last_stop_reason = ""
        self.started_at = 0.0
        self.finished_at = 0.0
        self.run_id_seq = 0
        self.active_run_id = 0
        self.last_upload_dir = ""
        self.last_upload_files: list[str] = []

        self.metrics = {
            "totalCases": 0,
            "approved": 0,
            "rejected": 0,
            "falsePositives": 0,
            "uniqueCrashes": 0,
        }

        self.total_source_lines = 0
        self.covered_lines: set[str] = set()
        self.total_branches: set[str] = set()
        self.covered_branches: set[str] = set()
        self.line_coverage_pct = 0.0
        self.branch_coverage_pct = 0.0
        self.coverage_updated_at = 0.0
        self.coverage_refresh_interval_seconds = 5.0

        self.latest_report_html = ""
        self.latest_report_json = ""
        self.max_rounds_limit = 0
        self.max_time_seconds_limit = 0
        self.coverage_goal_line = 1.0
        self.coverage_goal_branch = 1.0

        self.bus: MessageBus | None = None
        self.coordinator: CoordinatorAgent | None = None
        self.agents: list[Any] = []

    def bind_loop(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop

    def _emit_ws(self, payload: dict[str, Any]):
        if not self.loop:
            return

        coro = self.ws_manager.broadcast(payload)
        if threading.current_thread() is threading.main_thread():
            asyncio.create_task(coro)
        else:
            asyncio.run_coroutine_threadsafe(coro, self.loop)

    def _current_run_id(self) -> int:
        with self.lock:
            return int(self.active_run_id or 0)

    def _set_status(self, status: str, error: str = ""):
        with self.lock:
            self.status = status
            if error:
                self.last_error = error

            if status == "running":
                self.finished_at = 0.0
                if not self.started_at:
                    self.started_at = time.time()
            elif status in {"finished", "error", "paused"}:
                if self.started_at and self.finished_at <= 0:
                    self.finished_at = time.time()

    def _recompute_coverage_locked(self):
        line_total = int(self.total_source_lines or 0)
        line_covered = len(self.covered_lines)
        branch_total = len(self.total_branches)
        branch_covered = len(self.covered_branches)

        if line_total > 0:
            self.line_coverage_pct = min(100.0, (line_covered / line_total) * 100)
        else:
            self.line_coverage_pct = 0.0

        if branch_total > 0:
            self.branch_coverage_pct = min(100.0, (branch_covered / branch_total) * 100)
        else:
            self.branch_coverage_pct = 0.0

    def _emit_coverage_update(self, force: bool = False):
        with self.lock:
            now_ts = time.time()
            if not force and (now_ts - float(self.coverage_updated_at or 0.0)) < self.coverage_refresh_interval_seconds:
                return

            self._recompute_coverage_locked()
            self.coverage_updated_at = now_ts

            payload = {
                "current": round(self.line_coverage_pct, 2),
                "lineCoverage": round(self.line_coverage_pct, 2),
                "branchCoverage": round(self.branch_coverage_pct, 2),
                "lineCovered": len(self.covered_lines),
                "lineTotal": int(self.total_source_lines or 0),
                "branchCovered": len(self.covered_branches),
                "branchTotal": len(self.total_branches),
                "updatedAt": int(now_ts * 1000),
                "round": self.current_round,
                "runId": self._current_run_id(),
            }

        self._emit_ws({"event": "coverage_update", "data": payload})

    def _on_bus_publish(self, msg: Message):
        """MessageBus 发布钩子：同步转发到前端 WebSocket。"""
        topic = msg.topic
        sender = msg.sender or "System"
        target = TOPIC_TO_MAP.get(topic, "Broadcast")
        data = _ensure_jsonable(msg.data)

        # 通用消息事件（供前端消息流/动效使用）
        self._emit_ws({
            "event": "message_event",
            "data": {
                "id": getattr(msg, "message_id", ""),
                "type": topic,
                "from": sender,
                "to": target,
                "data": data,
                "runId": self._current_run_id(),
                "timestamp": datetime.fromtimestamp(msg.timestamp).isoformat(),
                "summary": f"{sender} 发布 {topic}",
            },
        })

        # 对前端已有事件模型做补充映射
        if topic == "cmd:generate":
            with self.lock:
                self.current_round += 1

        elif topic == "cmd:phase_change":
            phase_name = "INIT"
            if isinstance(msg.data, dict):
                phase_name = str(msg.data.get("new_phase", "INIT"))
            else:
                new_phase = getattr(msg.data, "new_phase", None)
                if isinstance(new_phase, Phase):
                    phase_name = new_phase.name
                elif new_phase is not None:
                    phase_name = str(new_phase)

            self.phase = phase_name
            self._emit_ws({
                "event": "phase_change",
                "data": {"phase": phase_name, "runId": self._current_run_id()},
            })

        elif topic == "data:analysis_result":
            total_lines = 0
            if isinstance(msg.data, AnalysisResult):
                total_lines = int(msg.data.total_lines or 0)
            elif isinstance(msg.data, dict):
                total_lines = int(msg.data.get("total_lines") or 0)

            if total_lines > 0:
                self.total_source_lines = total_lines
                with self.lock:
                    self._recompute_coverage_locked()

        elif topic == "data:executor_error" and isinstance(data, dict):
            detail = str(data.get("detail") or data.get("message") or "执行器发生错误").strip()
            fatal = bool(data.get("fatal", False))
            error_type = str(data.get("type") or "EXECUTOR_ERROR").strip() or "EXECUTOR_ERROR"
            hint = str(data.get("hint") or "").strip()

            with self.lock:
                if detail:
                    self.last_error = detail
                if fatal:
                    self.last_stop_reason = "executor_compile_failed"
                    self.stop_event.set()

            self._emit_ws({
                "event": "runtime_error",
                "data": {
                    "type": error_type,
                    "fatal": fatal,
                    "message": detail,
                    "hint": hint,
                    "runId": self._current_run_id(),
                },
            })

        elif topic == "data:execution_results":
            if isinstance(msg.data, list):
                for item in msg.data:
                    if not isinstance(item, ExecutionResult):
                        continue
                    self.metrics["totalCases"] += 1
                    self.metrics["approved"] += 1
                    if item.coverage_lines:
                        self.covered_lines.update(item.coverage_lines)
                    if item.coverage_delta:
                        self.covered_lines.update(item.coverage_delta)
                    if item.branch_total:
                        self.total_branches.update(item.branch_total)
                    if item.branch_covered:
                        self.covered_branches.update(item.branch_covered)
                    if item.branch_delta:
                        self.covered_branches.update(item.branch_delta)

            self._emit_coverage_update(force=True)

            self._emit_ws({
                "event": "round_complete",
                "data": {
                    "currentRound": self.current_round,
                    "metrics": self.metrics,
                    "runId": self._current_run_id(),
                },
            })

        elif topic == "data:verify_stats" and isinstance(msg.data, dict):
            self.metrics["falsePositives"] += int(msg.data.get("false_positives", 0))

        elif topic == "data:verified_crashes" and isinstance(msg.data, list):
            for crash in msg.data:
                crash_data = getattr(crash, "crash", None)
                error_type = "unknown"
                stack = ""
                input_text = ""
                source_loc = str(getattr(crash, "source_location", "") or "")
                risk_level = str(getattr(crash, "risk_level", "") or "MEDIUM")
                risk_summary = str(getattr(crash, "risk_summary", "") or "")
                occurrences = int(getattr(crash, "occurrence_count", 1) or 1)
                if crash_data is not None:
                    error_type = (
                        str(getattr(crash, "crash_type", "") or "").strip()
                        or getattr(crash_data, "asan_error", None)
                        or f"signal_{getattr(crash_data, 'signal', '')}"
                        or "crash"
                    )
                    stack = getattr(crash_data, "stderr", "")
                    if getattr(crash_data, "test_case", None):
                        input_text = getattr(crash_data.test_case, "input_text", "")

                if not source_loc and crash_data is not None:
                    stderr = str(getattr(crash_data, "stderr", "") or "")
                    match = re.search(r'([\w\-./\\]+\.c):(\d+)', stderr)
                    if match:
                        source_loc = f"{Path(match.group(1)).name}:{match.group(2)}"

                if not risk_summary:
                    risk_summary = f"{error_type} 可能导致进程崩溃或拒绝服务"

                self.metrics["uniqueCrashes"] += 1
                self._emit_ws({
                    "event": "crash_found",
                    "data": {
                        "id": getattr(crash, "stack_hash", "") or f"crash_{int(time.time()*1000)}",
                        "type": error_type,
                        "input": input_text,
                        "stackTrace": stack,
                        "description": risk_summary,
                        "location": source_loc,
                        "risk": risk_summary,
                        "riskLevel": risk_level,
                        "occurrences": occurrences,
                        "verified": True,
                        "severity": str(risk_level or "HIGH").lower(),
                        "runId": self._current_run_id(),
                    },
                })

        elif topic == "data:report_ready":
            if isinstance(msg.data, str):
                self.latest_report_html = msg.data
                html_path = Path(msg.data)
                json_path = html_path.with_suffix(".json")
                if json_path.exists():
                    self.latest_report_json = str(json_path)

            self._set_status("finished")
            self._emit_ws({
                "event": "test_finished",
                "data": {"status": "finished", "runId": self._current_run_id()},
            })

    def _apply_config_overrides(self, config: dict[str, Any], payload: dict[str, Any]):
        project = config.setdefault("project", {})
        agents = config.setdefault("agents", {})
        coordinator_cfg = agents.setdefault("coordinator", {})
        generator_cfg = agents.setdefault("generator", {})
        executor_cfg = agents.setdefault("executor", {})
        reviewer_cfg = agents.setdefault("reviewer", {})

        def _normalize_weights(raw: dict[str, float]) -> dict[str, float]:
            cleaned = {k: max(0.0, float(v)) for k, v in raw.items()}
            total = sum(cleaned.values())
            if total <= 0:
                return cleaned
            return {k: v / total for k, v in cleaned.items()}

        # 前端可不传入口函数：通用 C 场景下由后端/分析器自行推断
        project["entry_function"] = str(payload.get("entryFunction", "") or "").strip()
        project["initial_phase"] = str(payload.get("initialPhase", "EXPLORE") or "EXPLORE").upper()

        if payload.get("sourceDir"):
            project["source_dir"] = payload["sourceDir"]
        elif self.last_upload_dir:
            project["source_dir"] = self.last_upload_dir

        source_dir = str(project.get("source_dir", "") or "")
        if source_dir and (source_dir == self.last_upload_dir or Path(source_dir).name.startswith("upload_")):
            display_name = "Uploaded C Project"
            if self.last_upload_files:
                first_stem = Path(self.last_upload_files[0]).stem
                display_name = f"Uploaded C Project ({first_stem})"
            project["name"] = display_name

        if payload.get("maxRounds") is not None:
            coordinator_cfg["max_rounds"] = int(payload["maxRounds"])

        if payload.get("maxRoundsHardCap") is not None:
            coordinator_cfg["max_rounds_hard_cap"] = int(payload["maxRoundsHardCap"])
        elif coordinator_cfg.get("max_rounds") is not None:
            coordinator_cfg["max_rounds_hard_cap"] = max(
                int(coordinator_cfg.get("max_rounds_hard_cap", 0) or 0),
                int(coordinator_cfg.get("max_rounds", 0) or 0),
            )

        if payload.get("maxTime") is not None:
            coordinator_cfg["max_time_seconds"] = int(float(payload["maxTime"]) * 60)

        if payload.get("noProgressStopEnabled") is not None:
            coordinator_cfg["no_progress_stop_enabled"] = bool(payload["noProgressStopEnabled"])

        if payload.get("noProgressRoundLimit") is not None:
            coordinator_cfg["no_progress_round_limit"] = int(payload["noProgressRoundLimit"])

        if payload.get("noProgressMinRoundsBeforeStop") is not None:
            coordinator_cfg["no_progress_min_rounds_before_stop"] = int(payload["noProgressMinRoundsBeforeStop"])

        if payload.get("noProgressMinSecondsBeforeStop") is not None:
            coordinator_cfg["no_progress_min_seconds_before_stop"] = int(payload["noProgressMinSecondsBeforeStop"])

        if payload.get("noProgressRescueMaxAttempts") is not None:
            coordinator_cfg["no_progress_rescue_max_attempts"] = int(payload["noProgressRescueMaxAttempts"])

        if payload.get("noProgressRescueCooldownRounds") is not None:
            coordinator_cfg["no_progress_rescue_cooldown_rounds"] = int(payload["noProgressRescueCooldownRounds"])

        if payload.get("maxBatchSize") is not None:
            coordinator_cfg["max_batch_size"] = int(payload["maxBatchSize"])

        if payload.get("rescueBatchMultiplier") is not None:
            coordinator_cfg["rescue_batch_multiplier"] = float(payload["rescueBatchMultiplier"])

        if payload.get("stagnationBatchMultiplier") is not None:
            coordinator_cfg["stagnation_batch_multiplier"] = float(payload["stagnationBatchMultiplier"])

        if payload.get("coverageGoalLine") is not None:
            coordinator_cfg["coverage_goal_line"] = float(payload["coverageGoalLine"]) / 100.0

        if payload.get("coverageGoalBranch") is not None:
            coordinator_cfg["coverage_goal_branch"] = float(payload["coverageGoalBranch"]) / 100.0

        if payload.get("autoPhaseCoverageThreshold") is not None:
            coordinator_cfg["explore_to_deep_threshold"] = float(payload["autoPhaseCoverageThreshold"]) / 100.0

        if payload.get("batchSize") is not None:
            generator_cfg["batch_size"] = int(payload["batchSize"])

        strategy_weights = payload.get("strategyWeights") or {}
        if isinstance(strategy_weights, dict) and strategy_weights:
            random_weight = float(strategy_weights.get("random", 0.25))
            boundary_weight = float(strategy_weights.get("boundary", 0.25))
            grammar_weight = float(strategy_weights.get("grammar", 0.25))
            coverage_guided_weight = float(strategy_weights.get("coverageGuided", 0.25))

            generator_cfg["explore_weights"] = _normalize_weights({
                "equivalence": grammar_weight,
                "boundary": boundary_weight,
                "random_error_guess": random_weight,
                "error_guess": max(coverage_guided_weight, 0.05),
            })
            generator_cfg["deep_weights"] = _normalize_weights({
                "decision_table": grammar_weight,
                "state_sequence": boundary_weight,
                "whitebox_guided": coverage_guided_weight,
                "error_guess": max(random_weight, 0.05),
            })

        if payload.get("timeout") is not None:
            executor_cfg["timeout_per_case"] = int(payload["timeout"])

        if payload.get("reviewerThreshold") is not None:
            reviewer_cfg["approval_threshold"] = float(payload["reviewerThreshold"])

        if payload.get("crashVerifyCount") is not None:
            reviewer_cfg["crash_verify_count"] = int(payload["crashVerifyCount"])

        rw = payload.get("reviewerWeights") or {}
        if isinstance(rw, dict):
            if rw.get("diversity") is not None:
                reviewer_cfg["diversity_weight"] = float(rw["diversity"])
            if rw.get("coverage") is not None:
                reviewer_cfg["coverage_weight"] = float(rw["coverage"])
            if rw.get("validity") is not None:
                reviewer_cfg["validity_weight"] = float(rw["validity"])

    def _run_pipeline(self, payload: dict[str, Any], run_id: int):
        self.started_at = time.time()
        self.finished_at = 0.0
        self.active_run_id = int(run_id)
        self._set_status("running")
        self.phase = "INIT"
        self.current_round = 0
        self.last_error = ""
        self.last_stop_reason = ""
        self.covered_lines.clear()
        self.total_branches.clear()
        self.covered_branches.clear()
        self.total_source_lines = 0
        self.line_coverage_pct = 0.0
        self.branch_coverage_pct = 0.0
        self.coverage_updated_at = 0.0
        self.latest_report_html = ""
        self.latest_report_json = ""
        self.metrics.update({
            "totalCases": 0,
            "approved": 0,
            "rejected": 0,
            "falsePositives": 0,
            "uniqueCrashes": 0,
        })

        bus = None
        agents: list[Any] = []

        try:
            ensure_mingw_in_path()
            config = load_config()
            self._apply_config_overrides(config, payload)
            config = resolve_paths(config)

            coordinator_cfg = config.get("agents", {}).get("coordinator", {})
            self.max_rounds_limit = int(
                coordinator_cfg.get("max_rounds_hard_cap", coordinator_cfg.get("max_rounds", 0)) or 0
            )
            self.max_time_seconds_limit = int(coordinator_cfg.get("max_time_seconds", 0) or 0)
            self.coverage_goal_line = float(coordinator_cfg.get("coverage_goal_line", 1.0) or 1.0)
            self.coverage_goal_branch = float(coordinator_cfg.get("coverage_goal_branch", 1.0) or 1.0)

            output_dir = config.get("output", {}).get("dir", str(PROJECT_ROOT / "output"))
            setup_logging(output_dir)

            source_dir = config.get("project", {}).get("source_dir", "")
            if not source_dir or not os.path.exists(source_dir):
                raise RuntimeError(f"源码目录不存在: {source_dir}")

            bus = MessageBus()
            bus.add_publish_hook(self._on_bus_publish)
            self.bus = bus

            agent_configs = config.get("agents", {})

            coordinator_config = {
                **agent_configs.get("coordinator", {}),
                "batch_size": agent_configs.get("generator", {}).get("batch_size", 50),
                "explore_weights": agent_configs.get("generator", {}).get("explore_weights", {}),
                "deep_weights": agent_configs.get("generator", {}).get("deep_weights", {}),
                "source_dir": source_dir,
                "entry_function": config.get("project", {}).get("entry_function", ""),
                "initial_phase": config.get("project", {}).get("initial_phase", "EXPLORE"),
            }
            executor_config = {
                **agent_configs.get("executor", {}),
                "source_dir": source_dir,
                "output_dir": output_dir,
            }
            analyzer_config = {
                **agent_configs.get("analyzer", {}),
                "source_dir": source_dir,
            }
            reporter_config = {
                **agent_configs.get("reporter", {}),
                "output_dir": output_dir,
                "source_dir": source_dir,
                "entry_function": config.get("project", {}).get("entry_function", ""),
                "project_name": config.get("project", {}).get("name", "Multi-Agent Testing"),
            }
            reviewer_config = {
                **agent_configs.get("reviewer", {}),
                "source_dir": source_dir,
                "entry_function": config.get("project", {}).get("entry_function", ""),
            }
            generator_config = {
                **agent_configs.get("generator", {}),
                "source_dir": source_dir,
            }

            coordinator = CoordinatorAgent(bus, coordinator_config)
            analyzer = AnalyzerAgent(bus, analyzer_config)
            generator = GeneratorAgent(bus, generator_config)
            executor = ExecutorAgent(bus, executor_config)
            reviewer = ReviewerAgent(bus, reviewer_config)
            reporter = ReporterAgent(bus, reporter_config)

            agents = [coordinator, analyzer, generator, executor, reviewer, reporter]
            self.coordinator = coordinator
            self.agents = agents

            # 注入外部停止条件
            origin_should_stop = coordinator.global_state.should_stop

            def _patched_should_stop():
                return self.stop_event.is_set() or origin_should_stop()

            coordinator.global_state.should_stop = _patched_should_stop  # type: ignore

            for agent in agents:
                agent.start()

            coordinator.run_testing_loop()

            coordinator_stop_reason = str(getattr(coordinator.global_state, "last_stop_reason", "") or "")
            if coordinator_stop_reason:
                self.last_stop_reason = coordinator_stop_reason

            if self.stop_event.is_set():
                self._set_status("finished")
            else:
                self._set_status("finished")

            self._emit_coverage_update(force=True)

            self._emit_ws({
                "event": "test_finished",
                "data": {"status": self.status, "runId": self._current_run_id()},
            })

        except Exception as e:
            logger.error("测试运行异常", exc_info=True)
            self._set_status("error", str(e))
            self.last_stop_reason = "runtime_exception"
            self._emit_ws({
                "event": "message_event",
                "data": {
                    "type": "data:error",
                    "from": "Server",
                    "to": "Frontend",
                    "data": {"error": str(e)},
                    "runId": self._current_run_id(),
                    "timestamp": datetime.now().isoformat(),
                    "summary": "后端运行异常",
                },
            })
        finally:
            for agent in agents:
                try:
                    agent.stop()
                except Exception:
                    pass

            if bus is not None:
                try:
                    bus.remove_publish_hook(self._on_bus_publish)
                except Exception:
                    pass

            self.bus = None
            self.coordinator = None
            self.agents = []

    def start(self, payload: dict[str, Any]) -> int:
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise RuntimeError("测试正在运行中")

            self.stop_event.clear()
            self.run_id_seq += 1
            run_id = int(self.run_id_seq)
            self.active_run_id = run_id
            self.thread = threading.Thread(
                target=self._run_pipeline,
                args=(payload, run_id),
                daemon=True,
                name="CTestAgentRunner",
            )
            self.thread.start()
            return run_id

    def stop(self):
        with self.lock:
            self.stop_event.set()
            self.last_stop_reason = "manual_stop"

            if self.coordinator is not None:
                try:
                    self.coordinator.global_state.max_rounds = self.coordinator.global_state.round_number
                    self.coordinator.global_state.max_rounds_hard_cap = self.coordinator.global_state.round_number
                    self.coordinator.global_state.max_time_seconds = 0
                except Exception:
                    pass

            if self.status == "running":
                self._set_status("paused")

    def get_status(self) -> dict[str, Any]:
        should_emit_heartbeat = False
        with self.lock:
            running = bool(self.thread and self.thread.is_alive())
            if running:
                self._recompute_coverage_locked()
                if (time.time() - float(self.coverage_updated_at or 0.0)) >= self.coverage_refresh_interval_seconds:
                    should_emit_heartbeat = True

            if self.started_at:
                if self.finished_at > 0:
                    elapsed_anchor = self.finished_at
                else:
                    elapsed_anchor = time.time()
                elapsed = max(0, int(elapsed_anchor - self.started_at))
            else:
                elapsed = 0

            global_state = getattr(self.coordinator, "global_state", None)
            stop_reason = self.last_stop_reason
            if not stop_reason and global_state is not None:
                stop_reason = getattr(global_state, "last_stop_reason", "")

            status_data = {
                "status": self.status if running or self.status in {"finished", "error", "paused"} else "idle",
                "runId": int(self.active_run_id or 0),
                "phase": self.phase,
                "currentRound": self.current_round,
                "maxRounds": self.max_rounds_limit,
                "maxTimeSeconds": self.max_time_seconds_limit,
                "coverageGoalLine": round(float(self.coverage_goal_line or 0.0) * 100, 2),
                "coverageGoalBranch": round(float(self.coverage_goal_branch or 0.0) * 100, 2),
                "totalSourceLines": int(self.total_source_lines or 0),
                "metrics": dict(self.metrics),
                "lastError": self.last_error,
                "elapsedSeconds": elapsed,
                "running": running,
                "lineCoverage": round(float(self.line_coverage_pct or 0.0), 2),
                "branchCoverage": round(float(self.branch_coverage_pct or 0.0), 2),
                "lineCovered": len(self.covered_lines),
                "lineTotal": int(self.total_source_lines or 0),
                "branchCovered": len(self.covered_branches),
                "branchTotal": len(self.total_branches),
                "coverageUpdatedAt": int(float(self.coverage_updated_at or 0.0) * 1000),
                "stopReason": stop_reason,
                "latestReportHtml": self.latest_report_html,
                "latestReportJson": self.latest_report_json,
            }

        if should_emit_heartbeat:
            self._emit_coverage_update(force=True)

        return status_data

    def get_latest_report(self) -> dict[str, Any]:
        report_json = ""

        if self.latest_report_json and Path(self.latest_report_json).exists():
            report_json = self.latest_report_json
        else:
            json_files = sorted(REPORTS_DIR.glob("run_*/report_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            if json_files:
                report_json = str(json_files[0])

        if not report_json:
            raise FileNotFoundError("暂无可用测试报告")

        with open(report_json, "r", encoding="utf-8") as f:
            data = json.load(f)

        html_path = str(Path(report_json).with_suffix(".html"))
        return {
            "report": data,
            "jsonPath": report_json,
            "htmlPath": html_path if Path(html_path).exists() else "",
        }


app = FastAPI(title="CTestAgent API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ws_manager = WSConnectionManager()
runtime = TestRuntime(ws_manager)


@app.on_event("startup")
async def _on_startup():
    runtime.bind_loop(asyncio.get_running_loop())
    TARGETS_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)


@app.post("/api/upload")
async def upload_files(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="未上传文件")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    save_dir = TARGETS_DIR / f"upload_{ts}"
    save_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    for f in files:
        filename = f.filename or ""
        suffix = Path(filename).suffix.lower()
        if suffix not in {".c", ".h"}:
            continue

        dst = save_dir / Path(filename).name
        content = await f.read()
        with open(dst, "wb") as wf:
            wf.write(content)
        saved.append(str(dst))

    if not saved:
        raise HTTPException(status_code=400, detail="仅支持上传 .c/.h 文件")

    runtime.last_upload_dir = str(save_dir)
    runtime.last_upload_files = [Path(path).name for path in saved]
    return {"ok": True, "saved": saved, "sourceDir": str(save_dir)}


@app.post("/api/start")
async def start_test(payload: dict[str, Any] = Body(default_factory=dict)):
    try:
        run_id = runtime.start(payload)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    return {"ok": True, "message": "测试任务已启动", "runId": run_id}


@app.post("/api/stop")
async def stop_test():
    runtime.stop()
    return {"ok": True, "message": "已请求停止测试"}


@app.get("/api/status")
async def get_status():
    return runtime.get_status()


@app.get("/api/report")
async def get_report():
    try:
        return runtime.get_latest_report()
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket):
    await ws_manager.connect(websocket)
    await websocket.send_json({"event": "status", "data": runtime.get_status()})

    try:
        while True:
            # 当前后端是广播型 WS，客户端可不发送任何数据。
            await websocket.receive_text()
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)
    except Exception:
        await ws_manager.disconnect(websocket)


if FRONTEND_DIST.exists():
    # 生产模式：直接托管前端静态资源
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
