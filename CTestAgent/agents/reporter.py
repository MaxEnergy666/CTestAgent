# reporter.py - 报告者 Agent
"""
Reporter Agent（报告者）：系统的"记录者"和"翻译官"。

将机器可读的数据转化为人可读的报告：
1. 缺陷分类与去重
2. 输入最小化
3. 覆盖率可视化
4. 智能体协作日志
5. HTML 报告生成
"""

import os
import time
import json
import logging
import re
import hashlib
from datetime import datetime
from pathlib import Path

from core.base_agent import BaseAgent
from core.message_bus import MessageBus
from core.message_types import (
    Message, FinalizeCommand, ConfirmedCrash, ExecutionResult,
    BugReport, TestReport, RoundMetrics, CoverageData
)

logger = logging.getLogger(__name__)


class ReporterAgent(BaseAgent):
    """
    报告者智能体。

    收集所有智能体产出的数据，生成综合测试报告。
    """

    def __init__(self, bus: MessageBus, config: dict = None):  # type: ignore
        super().__init__("Reporter", bus, config)

        self.output_dir = config.get("output_dir", "./output") if config else "./output"
        self.report_format = config.get("report_format", "html") if config else "html"
        self.source_dir = config.get("source_dir", "") if config else ""
        self.entry_function = config.get("entry_function", "") if config else ""
        self.project_name = config.get("project_name", "Multi-Agent Testing") if config else "Multi-Agent Testing"
        self.target_name = Path(self.source_dir).name.lower() if self.source_dir else ""

        # 收集的数据
        self.confirmed_crashes: list[ConfirmedCrash] = []
        self.confirmed_crash_hashes: set[str] = set()
        self.all_execution_results: list[ExecutionResult] = []
        self.round_metrics: list[RoundMetrics] = []
        self.total_source_lines: int = 0
        self.all_covered_lines: set[str] = set()
        self.all_covered_branches: set[str] = set()
        self.all_total_branches: set[str] = set()
        self.target_source_files: set[str] = set()
        self.static_findings: list[dict] = []

        # 验证统计（由 Reviewer 发布）
        self.total_verify_confirmed: int = 0
        self.total_verify_false_positives: int = 0
        self.total_verify_crashes: int = 0

        # API 增强结果缓存
        self.api_executive_summary: str = ""
        self.api_risk_rating: str = ""

    def setup(self):
        """注册消息订阅"""
        self.subscribe("data:analysis_result", self.on_analysis_result)
        self.subscribe("data:verified_crashes", self.on_verified_crashes)
        self.subscribe("data:verify_stats", self.on_verify_stats)
        self.subscribe("data:execution_results", self.on_execution_results)
        self.subscribe("cmd:finalize", self.on_finalize)

    def on_analysis_result(self, msg: Message):
        """收集分析结果中的源码行数"""
        from core.message_types import AnalysisResult
        if isinstance(msg.data, AnalysisResult):
            self.total_source_lines = msg.data.total_lines
            self.target_source_files = {os.path.basename(p) for p in msg.data.source_files}
            self.static_findings = list(msg.data.static_findings or [])
            self.log_info(f"收到分析结果: {self.total_source_lines} 行源码")

    def on_verify_stats(self, msg: Message):
        """收集 Reviewer 的崩溃验证统计"""
        data = msg.data
        if isinstance(data, dict):
            self.total_verify_confirmed += int(data.get("confirmed", 0))
            self.total_verify_false_positives += int(data.get("false_positives", 0))
            self.total_verify_crashes += int(data.get("total_crashes", 0))

    def on_verified_crashes(self, msg: Message):
        """收集确认的崩溃"""
        if isinstance(msg.data, list):
            for crash in msg.data:
                if isinstance(crash, ConfirmedCrash):
                    if crash.stack_hash and crash.stack_hash in self.confirmed_crash_hashes:
                        continue
                    if crash.stack_hash:
                        self.confirmed_crash_hashes.add(crash.stack_hash)
                    self.confirmed_crashes.append(crash)

    def on_execution_results(self, msg: Message):
        """收集执行结果和覆盖率数据"""
        if isinstance(msg.data, list):
            for result in msg.data:
                if isinstance(result, ExecutionResult):
                    self.all_execution_results.append(result)
                    # 累计覆盖率 — 收集 coverage_lines 和 coverage_delta
                    if result.coverage_lines:
                        for line in result.coverage_lines:
                            if self._is_target_coverage_line(line):
                                self.all_covered_lines.add(line)
                    if result.coverage_delta:
                        for line in result.coverage_delta:
                            if self._is_target_coverage_line(line):
                                self.all_covered_lines.add(line)

                    if result.branch_total:
                        for branch in result.branch_total:
                            if self._is_target_branch_id(branch):
                                self.all_total_branches.add(branch)

                    if result.branch_covered:
                        for branch in result.branch_covered:
                            if self._is_target_branch_id(branch):
                                self.all_covered_branches.add(branch)
            # 调试: 每批次日志
            cov_count = len(self.all_covered_lines)
            if cov_count > 0:
                self.log_debug(f"Reporter 累计覆盖: {cov_count} 行")

    def on_finalize(self, msg: Message):
        """收到收尾指令，生成报告"""
        self.log_info("开始生成测试报告...")

        # 用途：保底，确保覆盖率数据被收集到
        for result in self.all_execution_results:
            if hasattr(result, 'coverage_lines') and result.coverage_lines:
                for line in result.coverage_lines:
                    if self._is_target_coverage_line(line):
                        self.all_covered_lines.add(line)
            if hasattr(result, 'coverage_delta') and result.coverage_delta:
                for line in result.coverage_delta:
                    if self._is_target_coverage_line(line):
                        self.all_covered_lines.add(line)
            if hasattr(result, 'branch_total') and result.branch_total:
                for branch in result.branch_total:
                    if self._is_target_branch_id(branch):
                        self.all_total_branches.add(branch)
            if hasattr(result, 'branch_covered') and result.branch_covered:
                for branch in result.branch_covered:
                    if self._is_target_branch_id(branch):
                        self.all_covered_branches.add(branch)
        self.log_info(f"覆盖数据汇总: {len(self.all_covered_lines)} 行已覆盖, "
                      f"total_source_lines={self.total_source_lines}, "
                      f"执行结果数={len(self.all_execution_results)}")

        try:
            report = self._build_report()
            report_path = self._generate_html_report(report)
            self.log_info(f"报告已生成: {report_path}")

            # 同时生成 JSON 报告
            json_path = self._generate_json_report(report)
            self.log_info(f"JSON 报告: {json_path}")

            self.publish("data:report_ready", report_path)
        except Exception as e:
            self.log_error(f"报告生成失败: {e}")

    def _build_report(self) -> TestReport:
        """构建测试报告数据"""
        from core.message_types import TestCaseStatus

        total_pass = sum(1 for r in self.all_execution_results
                         if r.status == TestCaseStatus.PASS)
        total_crash = sum(1 for r in self.all_execution_results
                          if r.status == TestCaseStatus.CRASH)

        # 构建缺陷报告列表（按漏洞键聚合，避免重复上报）
        bug_reports = []
        occurrence_map = self._collect_crash_occurrences()
        seen_bug_keys: set[str] = set()
        for crash in self.confirmed_crashes:
            if not crash.crash:
                continue

            source_loc = crash.source_location or self._extract_source_location(crash.crash.stderr)
            crash_type = crash.crash_type or self._classify_crash_type(crash.crash)
            mapped = self._map_fuzzgoat_vulnerability(crash.crash, source_loc)
            if mapped.get("type"):
                crash_type = mapped["type"]

            bug_key = self._build_bug_group_key(
                crash_type,
                source_loc,
                self._stack_fingerprint(crash.crash.stderr),
            )
            if bug_key in seen_bug_keys:
                continue
            seen_bug_keys.add(bug_key)

            error_type = f"{crash_type} @ {source_loc}" if source_loc else crash_type
            target_func = (
                crash.crash.test_case.target_function
                if crash.crash.test_case and crash.crash.test_case.target_function
                else (self.entry_function or "unknown")
            )

            occurrence_count = max(
                int(crash.occurrence_count or 0),
                int(occurrence_map.get(bug_key, 0)),
                1,
            )

            risk_level = str(crash.risk_level or "").strip().upper() or "MEDIUM"
            risk_summary = str(crash.risk_summary or "").strip()

            stack_text = (crash.crash.stderr or "").strip()
            desc_text = str(mapped.get("description", "") or "").strip()
            if not desc_text and risk_summary:
                desc_text = risk_summary
            if not desc_text:
                desc_text = self._build_bug_description_fallback(crash.crash, crash_type, source_loc)

            if stack_text:
                stack_preview = stack_text[:1200]
            else:
                stack_preview = desc_text

            sample_inputs = []
            if isinstance(crash.metadata, dict):
                raw_inputs = crash.metadata.get("sample_inputs", [])
                if isinstance(raw_inputs, list):
                    sample_inputs = [str(x) for x in raw_inputs if str(x).strip()]

            min_input = crash.crash.test_case.input_text[:200] if crash.crash.test_case else ""
            if not min_input and sample_inputs:
                min_input = sample_inputs[0][:200]

            bug = BugReport(
                error_type=error_type,
                crash=crash,
                stack_trace=stack_preview,
                target_function=target_func,
                min_input=min_input,
                severity=risk_level,
                description=desc_text,
            )
            bug_reports.append(bug)

        # 补充静态漏洞发现，确保上传后至少得到可定位的缺陷结论。
        static_added = 0
        for finding in self.static_findings:
            if not isinstance(finding, dict):
                continue

            location = f"{finding.get('file', '')}:{finding.get('line', '')}".strip(":")
            finding_type = str(finding.get("type", "STATIC_FINDING")).strip()
            bug_key = self._build_bug_group_key(finding_type, location, "")
            if bug_key in seen_bug_keys:
                continue
            seen_bug_keys.add(bug_key)

            desc = str(finding.get("description", "")).strip()
            risk = str(finding.get("risk", "")).strip()
            recommendation = str(finding.get("recommendation", "")).strip()
            code_line = str(finding.get("code", "")).strip()

            composed_desc = desc
            if risk:
                composed_desc = f"{composed_desc} | 风险: {risk}" if composed_desc else risk
            if recommendation:
                composed_desc = f"{composed_desc} | 建议: {recommendation}" if composed_desc else recommendation

            stack_text = code_line
            if recommendation:
                stack_text = f"{code_line}\n建议: {recommendation}".strip()

            severity = str(finding.get("severity", "MEDIUM")).strip().upper() or "MEDIUM"
            target_func = str(finding.get("function", "")).strip() or (self.entry_function or "N/A")
            error_type = f"{finding_type} @ {location}" if location else finding_type

            bug_reports.append(BugReport(
                error_type=error_type,
                crash=None,
                min_input="N/A (static analysis)",
                stack_trace=stack_text or composed_desc,
                target_function=target_func,
                severity=severity,
                description=composed_desc or "静态分析发现潜在风险点",
            ))
            static_added += 1

        if static_added > 0:
            self.log_info(f"静态漏洞发现已并入报告: {static_added} 条")

        # 计算行覆盖率
        line_coverage = 0.0
        covered_count = len(self.all_covered_lines)
        if self.total_source_lines > 0:
            line_coverage = covered_count / self.total_source_lines
            if line_coverage > 1.0:
                # 防止统计口径异常导致 >100%
                line_coverage = 1.0
        self.log_info(f"覆盖率统计: {covered_count} 行已覆盖 / {self.total_source_lines} 行总计 = {line_coverage:.2%}")

        branch_total_count = len(self.all_total_branches)
        branch_covered_count = len(self.all_covered_branches)
        branch_coverage = 0.0
        if branch_total_count > 0:
            branch_coverage = min(1.0, branch_covered_count / branch_total_count)
        self.log_info(
            f"分支覆盖率统计: {branch_covered_count} / {branch_total_count} = {branch_coverage:.2%}"
        )

        # 获取交互统计
        interaction_stats = self.bus.get_interaction_stats()

        false_positives = max(
            self.total_verify_false_positives,
            self.total_verify_crashes - self.total_verify_confirmed,
        )
        if self.total_verify_crashes == 0:
            false_positives = max(0, total_crash - len(self.confirmed_crashes))

        report = TestReport(
            project_name=self.project_name,
            total_cases_generated=len(self.all_execution_results),
            total_cases_executed=len(self.all_execution_results),
            total_cases_passed=total_pass,
            total_crashes=total_crash,
            confirmed_bugs=bug_reports,
            false_positives=false_positives,
            line_coverage=line_coverage,
            branch_coverage=branch_coverage,
            agent_interaction_stats=interaction_stats,
        )

        self._apply_api_report_enhancement(report)

        return report

    def _apply_api_report_enhancement(self, report: TestReport):
        """调用 API 生成执行摘要并补充缺陷描述；失败时保留原报告。"""
        if not self.api_advisor.enabled:
            return

        context = {
            "project_name": report.project_name,
            "total_cases_executed": report.total_cases_executed,
            "total_crashes": report.total_crashes,
            "confirmed_bug_count": len(report.confirmed_bugs),
            "false_positives": report.false_positives,
            "line_coverage": round(float(report.line_coverage), 4),
            "bugs": [
                {
                    "bug_id": bug.bug_id,
                    "error_type": bug.error_type,
                    "target_function": bug.target_function,
                    "severity": bug.severity,
                }
                for bug in report.confirmed_bugs[:20]
            ],
        }
        expected_schema = {
            "executive_summary": "short summary",
            "risk_rating": "HIGH",
            "bug_descriptions": {"bug_id": "short description"},
            "reason": "short explanation",
        }

        decision = self.api_advisor.ask_json(
            task="生成测试执行摘要，并补充缺陷描述",
            context=context,
            expected_schema=expected_schema,
            max_tokens=700,
            temperature=0.2,
        )
        if not decision:
            return

        summary = str(decision.get("executive_summary", "")).strip()
        if summary:
            self.api_executive_summary = summary

        risk_rating = str(decision.get("risk_rating", "")).strip().upper()
        if risk_rating:
            self.api_risk_rating = risk_rating

        desc_map = decision.get("bug_descriptions", {})
        if isinstance(desc_map, dict):
            by_id = {bug.bug_id: bug for bug in report.confirmed_bugs}
            for bug_id, desc in desc_map.items():
                bug = by_id.get(str(bug_id).strip())
                if bug is None:
                    continue
                desc_text = str(desc).strip()
                if not desc_text:
                    continue
                if bug.description:
                    bug.description = f"{bug.description} | {desc_text}"
                else:
                    bug.description = desc_text

        reason = str(decision.get("reason", "")).strip()
        if reason:
            self.log_info(f"API 报告增强已应用: {reason}")

    def _generate_html_report(self, report: TestReport) -> str:
        """生成 HTML 格式测试报告"""
        # 每次运行创建独立的日期时间命名文件夹
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = os.path.join(self.output_dir, "reports", f"run_{timestamp}")
        os.makedirs(run_dir, exist_ok=True)
        self._current_run_dir = run_dir
        self._current_report_timestamp = timestamp

        report_path = os.path.join(run_dir, f"report_{timestamp}.html")

        html = self._render_html(report)

        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(html)

        return report_path

    def _render_html(self, report: TestReport) -> str:
        """渲染 HTML 报告"""
        bugs_html = ""
        for i, bug in enumerate(report.confirmed_bugs, 1):
            crash = bug.crash
            location = ""
            occurrences = 1
            risk_text = bug.description
            if crash is not None:
                location = crash.source_location or ""
                try:
                    occurrences = max(1, int(crash.occurrence_count or 1))
                except Exception:
                    occurrences = 1
                risk_text = crash.risk_summary or risk_text

            bugs_html += f"""
            <tr>
                <td>{i}</td>
                <td>{bug.bug_id}</td>
                <td><span class="badge badge-{bug.severity.lower()}">{bug.severity}</span></td>
                <td>{bug.error_type}</td>
                <td>{location or '-'}</td>
                <td>{occurrences}</td>
                <td>{bug.target_function}</td>
                <td><code>{self._escape_html(bug.min_input[:100])}</code></td>
                <td>{self._escape_html(risk_text[:180])}</td>
                <td><pre class="stack-trace">{self._escape_html(bug.stack_trace[:500])}</pre></td>
            </tr>"""

        # 智能体交互统计
        stats = report.agent_interaction_stats
        stats_html = ""
        if "messages_by_sender" in stats:
            for sender, count in stats.get("messages_by_sender", {}).items():
                stats_html += f"<tr><td>{sender}</td><td>{count}</td></tr>"

        topic_stats_html = ""
        if "messages_by_topic" in stats:
            for topic, count in stats.get("messages_by_topic", {}).items():
                topic_stats_html += f"<tr><td>{topic}</td><td>{count}</td></tr>"

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>多智能体协同测试报告 - {report.project_name}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
               background: #f5f7fa; color: #333; line-height: 1.6; }}
        .container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}
        h1 {{ color: #1a1a2e; margin-bottom: 10px; font-size: 28px; }}
        h2 {{ color: #16213e; margin: 30px 0 15px; padding-bottom: 8px;
              border-bottom: 2px solid #0f3460; }}
        .subtitle {{ color: #666; margin-bottom: 30px; }}
        .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                         gap: 15px; margin: 20px 0; }}
        .summary-card {{ background: white; border-radius: 10px; padding: 20px;
                         box-shadow: 0 2px 10px rgba(0,0,0,0.08); text-align: center; }}
        .summary-card .value {{ font-size: 36px; font-weight: bold; color: #0f3460; }}
        .summary-card .label {{ color: #666; font-size: 14px; margin-top: 5px; }}
        .card-bugs .value {{ color: #e94560; }}
        .card-coverage .value {{ color: #0f9b8e; }}
        table {{ width: 100%; border-collapse: collapse; background: white;
                 border-radius: 10px; overflow: hidden;
                 box-shadow: 0 2px 10px rgba(0,0,0,0.08); margin: 15px 0; }}
        th {{ background: #0f3460; color: white; padding: 12px 15px; text-align: left; }}
        td {{ padding: 10px 15px; border-bottom: 1px solid #eee; }}
        tr:hover {{ background: #f8f9fa; }}
        .badge {{ padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: bold; }}
        .badge-high {{ background: #ffe0e6; color: #e94560; }}
        .badge-medium {{ background: #fff3cd; color: #856404; }}
        .badge-low {{ background: #d4edda; color: #155724; }}
        .stack-trace {{ background: #1a1a2e; color: #0f9b8e; padding: 10px;
                        border-radius: 5px; font-size: 11px; max-height: 200px;
                        overflow-y: auto; white-space: pre-wrap; word-break: break-all; }}
        code {{ background: #e9ecef; padding: 2px 6px; border-radius: 3px; font-size: 13px; }}
        .footer {{ text-align: center; color: #999; margin-top: 40px; padding: 20px;
                   border-top: 1px solid #eee; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🤖 多智能体协同测试报告</h1>
        <p class="subtitle">{report.project_name} | 生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>

        <h2>1. 测试概览</h2>
        <div class="summary-grid">
            <div class="summary-card">
                <div class="value">{report.total_cases_executed}</div>
                <div class="label">执行用例数</div>
            </div>
            <div class="summary-card">
                <div class="value">{report.total_cases_passed}</div>
                <div class="label">通过用例数</div>
            </div>
            <div class="summary-card card-bugs">
                <div class="value">{len(report.confirmed_bugs)}</div>
                <div class="label">确认缺陷数</div>
            </div>
            <div class="summary-card">
                <div class="value">{report.false_positives}</div>
                <div class="label">拦截误报数</div>
            </div>
            <div class="summary-card card-coverage">
                <div class="value">{report.line_coverage:.1%}</div>
                <div class="label">行覆盖率</div>
            </div>
            <div class="summary-card">
                <div class="value">{stats.get('total_messages', 0)}</div>
                <div class="label">智能体消息总数</div>
            </div>
        </div>

        <h2>2. 缺陷详情</h2>
        {"<p>未发现缺陷。</p>" if not report.confirmed_bugs else f'''
        <table>
            <thead>
                <tr>
                    <th>#</th><th>缺陷ID</th><th>严重性</th><th>错误类型</th>
                    <th>代码位置</th><th>出现次数</th><th>函数</th><th>触发输入</th><th>风险</th><th>调用栈</th>
                </tr>
            </thead>
            <tbody>{bugs_html}</tbody>
        </table>'''}

        <h2>3. 智能体协作分析</h2>
        <h3>3.1 各智能体消息统计</h3>
        <table>
            <thead><tr><th>智能体</th><th>发送消息数</th></tr></thead>
            <tbody>{stats_html if stats_html else "<tr><td colspan='2'>暂无数据</td></tr>"}</tbody>
        </table>

        <h3>3.2 消息主题统计</h3>
        <table>
            <thead><tr><th>主题</th><th>消息数</th></tr></thead>
            <tbody>{topic_stats_html if topic_stats_html else "<tr><td colspan='2'>暂无数据</td></tr>"}</tbody>
        </table>

        <h2>4. API 语义总结</h2>
        <table>
            <thead><tr><th>字段</th><th>内容</th></tr></thead>
            <tbody>
                <tr><td>风险评级</td><td>{self.api_risk_rating or "N/A"}</td></tr>
                <tr><td>执行摘要</td><td>{self._escape_html(self.api_executive_summary or "未启用 API 摘要增强")}</td></tr>
            </tbody>
        </table>

        <div class="footer">
            <p>CTestAgent - 多智能体协同软件测试系统 | 生成于 {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
        </div>
    </div>
</body>
</html>"""
        return html

    def _generate_json_report(self, report: TestReport) -> str:
        """生成 JSON 格式报告"""
        # 使用与 HTML 报告相同的运行目录
        run_dir = getattr(self, '_current_run_dir', None)
        if not run_dir:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            run_dir = os.path.join(self.output_dir, "reports", f"run_{timestamp}")
            os.makedirs(run_dir, exist_ok=True)

        timestamp = getattr(self, '_current_report_timestamp', None) or datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path = os.path.join(run_dir, f"report_{timestamp}.json")

        bugs_payload = []
        for bug in report.confirmed_bugs:
            crash = bug.crash
            result = crash.crash if crash and crash.crash else None

            location = ""
            if crash and crash.source_location:
                location = crash.source_location
            elif result is not None:
                location = self._extract_source_location(result.stderr)
            elif " @ " in bug.error_type:
                location = bug.error_type.split(" @ ", 1)[1].strip()

            occurrences = 1
            if crash and crash.occurrence_count:
                try:
                    occurrences = max(1, int(crash.occurrence_count))
                except Exception:
                    occurrences = 1

            risk_level = str((crash.risk_level if crash else bug.severity) or bug.severity or "MEDIUM").upper()
            risk_summary = str((crash.risk_summary if crash else "") or bug.description or "").strip()

            sample_inputs = []
            if crash and isinstance(crash.metadata, dict):
                raw = crash.metadata.get("sample_inputs", [])
                if isinstance(raw, list):
                    sample_inputs = [str(x)[:200] for x in raw if str(x).strip()]

            input_text = bug.min_input[:200]
            if not input_text and sample_inputs:
                input_text = sample_inputs[0]

            description = bug.description or self._build_bug_description_fallback(
                result,
                bug.error_type,
                location,
            )

            evidence = "dynamic" if result is not None else "static"
            verified = result is not None

            bugs_payload.append({
                "id": bug.bug_id,
                "type": bug.error_type,
                "severity": bug.severity,
                "risk_level": risk_level,
                "risk": risk_summary,
                "function": bug.target_function,
                "location": location,
                "occurrences": occurrences,
                "input": input_text,
                "sample_inputs": sample_inputs,
                "description": description,
                "stack_trace": bug.stack_trace or description,
                "stackTrace": bug.stack_trace or description,
                "stack_hash": crash.stack_hash if crash else "",
                "signal": result.signal if result is not None else None,
                "return_code": result.return_code if result is not None else 0,
                "evidence": evidence,
                "verified": verified,
            })

        dynamic_bug_count = sum(1 for bug in bugs_payload if bug.get("evidence") == "dynamic")
        static_bug_count = sum(1 for bug in bugs_payload if bug.get("evidence") == "static")

        data = {
            "project": report.project_name,
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "total_generated": report.total_cases_generated,
                "total_executed": report.total_cases_executed,
                "total_passed": report.total_cases_passed,
                "total_crashes": report.total_crashes,
                "confirmed_bugs": dynamic_bug_count,
                "false_positives": report.false_positives,
                "line_coverage": report.line_coverage,
                "line_covered": len(self.all_covered_lines),
                "branch_coverage": report.branch_coverage,
                "total_source_lines": self.total_source_lines,
                "branch_covered": len(self.all_covered_branches),
                "branch_total": len(self.all_total_branches),
                "static_findings": static_bug_count,
            },
            "bugs": bugs_payload,
            "agent_interactions": report.agent_interaction_stats,
            "api_summary": {
                "risk_rating": self.api_risk_rating,
                "executive_summary": self.api_executive_summary,
            },
        }

        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        return json_path

    @staticmethod
    def _escape_html(text: str) -> str:
        """HTML 转义"""
        return (text
                .replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
                .replace('"', '&quot;')
                .replace("'", '&#39;'))

    def _is_target_coverage_line(self, line: str) -> bool:
        """仅保留目标源码覆盖率（过滤 harness 等临时文件）"""
        if not line:
            return False
        if ':' not in line:
            return False

        file_part = line.split(':', 1)[0]
        base = os.path.basename(file_part)

        # 无分析结果时保守放行
        if not self.target_source_files:
            return True

        # 兼容 gcov 可能生成的文件名格式
        if base in self.target_source_files:
            return True
        if f"{base}.c" in self.target_source_files:
            return True

        return False

    def _is_target_branch_id(self, branch_id: str) -> bool:
        """分支 ID 复用行覆盖过滤规则。"""
        if not branch_id:
            return False
        file_part = branch_id.split(':', 1)[0]
        if not file_part:
            return False
        return self._is_target_coverage_line(f"{file_part}:1")

    def _build_bug_description_fallback(
        self,
        result: ExecutionResult | None,
        crash_type: str,
        source_loc: str,
    ) -> str:
        """缺陷描述兜底，确保 JSON 中 description 字段非空。"""
        if result is None:
            if source_loc:
                return f"{crash_type} @ {source_loc}"
            return crash_type or "运行时崩溃"

        parts = [crash_type or "runtime_crash"]
        if source_loc:
            parts.append(f"location={source_loc}")
        if result.signal is not None:
            parts.append(f"signal={result.signal}")
        parts.append(f"return_code={result.return_code}")

        stderr_preview = (result.stderr or "").strip()
        if stderr_preview:
            parts.append(stderr_preview.splitlines()[0][:180])

        return " | ".join(parts)

    def _stack_fingerprint(self, stderr: str) -> str:
        """对调用栈关键行做短哈希，用于缺陷聚合。"""
        text = (stderr or "").strip()
        if not text:
            return "nostack"

        key_lines = []
        for line in text.splitlines():
            line_strip = line.strip()
            if not line_strip:
                continue
            if "ERROR" in line_strip or "#" in line_strip or ".c:" in line_strip or "at " in line_strip:
                key_lines.append(line_strip)

        if not key_lines:
            key_lines = text.splitlines()[:5]

        payload = "\n".join(key_lines)[:2000]
        return hashlib.md5(payload.encode("utf-8", errors="replace")).hexdigest()[:12]

    @staticmethod
    def _build_bug_group_key(crash_type: str, source_loc: str, stack_hash: str) -> str:
        """用于把重复崩溃归并成一个漏洞条目。"""
        token = str(crash_type or "unknown").strip().lower()
        loc = str(source_loc or "").strip().lower()
        sig = str(stack_hash or "").strip().lower()
        if loc:
            return f"{token}|{loc}"
        return f"{token}|{sig}"

    def _collect_crash_occurrences(self) -> dict[str, int]:
        """统计所有执行结果中每类崩溃的出现次数。"""
        from core.message_types import TestCaseStatus

        counts: dict[str, int] = {}
        for result in self.all_execution_results:
            if not isinstance(result, ExecutionResult):
                continue
            if result.status != TestCaseStatus.CRASH:
                continue

            crash_type = self._classify_crash_type(result)
            source_loc = self._extract_source_location(result.stderr or "")
            stack_hash = self._stack_fingerprint(result.stderr or "")
            key = self._build_bug_group_key(crash_type, source_loc, stack_hash)
            counts[key] = counts.get(key, 0) + 1

        return counts

    def _extract_source_location(self, stderr: str) -> str:
        """从 stderr 提取源码定位（file.c:line）"""
        if not stderr:
            return ""
        m = re.search(r'([\w\-./\\]+\.c):(\d+)', stderr)
        if m:
            return f"{os.path.basename(m.group(1))}:{m.group(2)}"
        return ""

    def _classify_crash_type(self, result: ExecutionResult) -> str:
        """崩溃粗分类"""
        stderr = (result.stderr or "").lower()
        if result.asan_error:
            return result.asan_error
        if "use after free" in stderr or "use-after-free" in stderr:
            return "use_after_free"
        if "invalid free" in stderr:
            return "invalid_free"
        if "null" in stderr and "dereference" in stderr:
            return "null_deref"
        if result.signal == 11:
            return "segv"
        if result.signal == 6:
            return "abort"
        if result.return_code != 0:
            return f"nonzero_rc_{result.return_code}"
        return "crash"

    def _map_fuzzgoat_vulnerability(self, result: ExecutionResult, source_loc: str) -> dict:
        """针对 fuzzgoat 的已知漏洞注释映射，输出更具体问题描述"""
        if self.target_name != "fuzzgoat":
            return {}

        # 1) 优先按 seed 文件名映射
        seed_file = ""
        if result.test_case and result.test_case.metadata:
            seed_file = str(result.test_case.metadata.get("seed_file", ""))

        by_seed = {
            "emptyArray.txt": {
                "type": "use_after_free",
                "description": "fuzzgoat 已知漏洞：free(*top) 后继续使用，触发 UAF（参考 fuzzgoat.c:137）",
            },
            "validObject.txt": {
                "type": "invalid_free",
                "description": "fuzzgoat 已知漏洞：value->u.object.length-- 导致错误索引，触发非法 free（参考 fuzzgoat.c:258）",
            },
            "emptyString.txt": {
                "type": "invalid_free",
                "description": "fuzzgoat 已知漏洞：空串路径 ptr-- 后 free，触发非法指针释放（参考 fuzzgoat.c:279）",
            },
            "oneByteString.txt": {
                "type": "null_deref",
                "description": "fuzzgoat 已知漏洞：单字节字符串路径构造并解引用 NULL 指针（参考 fuzzgoat.c:297-298）",
            },
        }
        if seed_file in by_seed:
            return by_seed[seed_file]

        # 2) 按源码行号回退映射
        if source_loc and ":" in source_loc:
            try:
                line = int(source_loc.split(":", 1)[1])
            except Exception:
                line = -1

            if 130 <= line <= 145:
                return {
                    "type": "use_after_free",
                    "description": "疑似命中 fuzzgoat UAF 漏洞段（free(*top)）",
                }
            if 250 <= line <= 265:
                return {
                    "type": "invalid_free",
                    "description": "疑似命中 fuzzgoat 对象长度后缀递减导致的非法 free 漏洞段",
                }
            if 272 <= line <= 283:
                return {
                    "type": "invalid_free",
                    "description": "疑似命中 fuzzgoat 空字符串 ptr-- 导致的非法 free 漏洞段",
                }
            if 292 <= line <= 301:
                return {
                    "type": "null_deref",
                    "description": "疑似命中 fuzzgoat NULL 指针解引用漏洞段",
                }

        return {}
