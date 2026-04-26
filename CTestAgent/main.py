# main.py - 系统入口
"""
CTestAgent 多智能体协同软件测试系统 — 主入口

启动流程：
1. 加载配置文件
2. 初始化消息总线
3. 创建并启动 6 个智能体
4. 由 Coordinator Agent 驱动测试循环
5. 输出测试报告
"""

import os
import sys
import yaml
import time
import logging
from pathlib import Path

# 将项目根目录加入 Python 路径
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from core.message_bus import MessageBus
from agents.coordinator import CoordinatorAgent
from agents.analyzer import AnalyzerAgent
from agents.generator import GeneratorAgent
from agents.executor import ExecutorAgent
from agents.reviewer import ReviewerAgent
from agents.reporter import ReporterAgent


def setup_logging(output_dir: str):
    """配置日志系统"""
    log_dir = os.path.join(output_dir, "agent_logs")
    os.makedirs(log_dir, exist_ok=True)

    log_file = os.path.join(log_dir, f"test_run_{time.strftime('%Y%m%d_%H%M%S')}.log")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)-20s] %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding='utf-8'),
        ]
    )

    # 降低 matplotlib 等库的日志级别
    logging.getLogger("matplotlib").setLevel(logging.WARNING)


def load_config(config_path: str = "config.yaml") -> dict:
    """加载配置文件"""
    config_file = os.path.join(PROJECT_ROOT, config_path)

    if not os.path.exists(config_file):
        print(f"警告: 配置文件 {config_file} 不存在，使用默认配置")
        return get_default_config()

    with open(config_file, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    return config


def get_default_config() -> dict:
    """默认配置"""
    return {
        "project": {
            "name": "cJSON Multi-Agent Testing",
            "source_dir": "./targets/cjson",
            "entry_function": "cJSON_Parse",
        },
        "agents": {
            "coordinator": {
                "explore_to_deep_threshold": 0.6,
                "stagnation_limit": 500,
                "max_rounds": 500,
                "max_rounds_hard_cap": 800,
                "max_time_seconds": 1800,
                "coverage_goal_line": 1.0,
                "coverage_goal_branch": 1.0,
                "stop_on_stagnation": False,
                "no_progress_round_limit": 30,
                "no_progress_stop_enabled": False,
                "no_progress_min_rounds_before_stop": 240,
                "no_progress_min_seconds_before_stop": 900,
                "no_progress_rescue_enabled": True,
                "no_progress_rescue_max_attempts": 0,
                "no_progress_rescue_cooldown_rounds": 30,
                "max_batch_size": 240,
                "rescue_batch_multiplier": 2.0,
                "stagnation_batch_multiplier": 1.5,
                "api_round_interval": 3,
            },
            "generator": {
                "batch_size": 60,
                "api_round_interval": 3,
            },
            "executor": {
                "timeout_per_case": 5,
                "enable_asan": True,
                "enable_gcov": True,
                "api_max_requests_per_batch": 1,
            },
            "reviewer": {
                "approval_threshold": 0.4,
                "crash_verify_count": 3,
                "max_new_crashes_per_round": 10,
                "api_review_interval": 6,
                "diversity_weight": 0.3,
                "coverage_weight": 0.4,
                "validity_weight": 0.3,
            },
            "reporter": {
                "report_format": "html",
            },
        },
        "output": {
            "dir": "./output",
        },
    }


def resolve_paths(config: dict) -> dict:
    """将配置中的相对路径转为绝对路径"""
    source_dir = config.get("project", {}).get("source_dir", "./targets/cjson")
    output_dir = config.get("output", {}).get("dir", "./output")

    if not os.path.isabs(source_dir):
        source_dir = os.path.join(PROJECT_ROOT, source_dir)
    if not os.path.isabs(output_dir):
        output_dir = os.path.join(PROJECT_ROOT, output_dir)

    config.setdefault("project", {})["source_dir"] = source_dir
    config.setdefault("output", {})["dir"] = output_dir

    return config


def ensure_mingw_in_path():
    """确保 MinGW gcc 在 PATH 中（Windows）"""
    if os.name == 'nt':
        mingw_bin = r"C:\msys64\mingw64\bin"
        if os.path.exists(mingw_bin) and mingw_bin not in os.environ.get("PATH", ""):
            os.environ["PATH"] = mingw_bin + os.pathsep + os.environ.get("PATH", "")
            print(f"  [INFO] 已将 MinGW 加入 PATH: {mingw_bin}")


def main():
    """主函数"""
    print("=" * 60)
    print("  CTestAgent - 多智能体协同软件测试系统")
    print("=" * 60)

    # 0. 确保 gcc 可用
    ensure_mingw_in_path()

    # 1. 加载配置
    config = load_config()
    config = resolve_paths(config)

    source_dir = config["project"]["source_dir"]
    output_dir = config["output"]["dir"]

    # 2. 配置日志
    setup_logging(output_dir)
    logger = logging.getLogger("main")
    logger.info("系统启动")
    logger.info(f"被测项目: {config['project'].get('name', 'unknown')}")
    logger.info(f"源码目录: {source_dir}")
    logger.info(f"输出目录: {output_dir}")

    # 检查源码目录
    if not os.path.exists(source_dir):
        logger.error(f"源码目录不存在: {source_dir}")
        sys.exit(1)

    # 3. 创建消息总线
    bus = MessageBus()
    logger.info("消息总线已创建")

    # 4. 创建 6 个智能体
    agent_configs = config.get("agents", {})

    # Coordinator 的配置合并全局配置
    coordinator_config = {
        **agent_configs.get("coordinator", {}),
        "batch_size": agent_configs.get("generator", {}).get("batch_size", 50),
        "explore_weights": agent_configs.get("generator", {}).get("explore_weights", {}),
        "deep_weights": agent_configs.get("generator", {}).get("deep_weights", {}),
        "source_dir": source_dir,
        "entry_function": config.get("project", {}).get("entry_function", ""),
        "initial_phase": config.get("project", {}).get("initial_phase", "EXPLORE"),
    }

    # Executor 的配置需要源码目录和输出目录
    executor_config = {
        **agent_configs.get("executor", {}),
        "source_dir": source_dir,
        "output_dir": output_dir,
    }

    # Analyzer 配置
    analyzer_config = {
        **agent_configs.get("analyzer", {}),
        "source_dir": source_dir,
    }

    # Reporter 配置
    reporter_config = {
        **agent_configs.get("reporter", {}),
        "output_dir": output_dir,
        "source_dir": source_dir,
        "entry_function": config.get("project", {}).get("entry_function", ""),
        "project_name": config.get("project", {}).get("name", "Multi-Agent Testing"),
    }

    # Reviewer 配置
    reviewer_config = {
        **agent_configs.get("reviewer", {}),
        "source_dir": source_dir,
        "entry_function": config.get("project", {}).get("entry_function", ""),
    }

    # 创建智能体实例
    coordinator = CoordinatorAgent(bus, coordinator_config)
    analyzer = AnalyzerAgent(bus, analyzer_config)
    generator_config = {
        **agent_configs.get("generator", {}),
        "source_dir": source_dir,
    }

    generator = GeneratorAgent(bus, generator_config)
    executor = ExecutorAgent(bus, executor_config)
    reviewer = ReviewerAgent(bus, reviewer_config)
    reporter = ReporterAgent(bus, reporter_config)

    # 5. 启动所有智能体（注册消息订阅）
    agents = [coordinator, analyzer, generator, executor, reviewer, reporter]
    for agent in agents:
        agent.start()
        logger.info(f"  ✓ {agent.name} Agent 已启动")

    logger.info(f"消息总线状态: {bus}")
    logger.info("")

    # 6. 由 Coordinator 驱动测试循环
    start_time = time.time()

    try:
        coordinator.run_testing_loop()
    except KeyboardInterrupt:
        logger.info("\n用户中断，正在生成报告...")
        coordinator._finalize()
    except Exception as e:
        logger.error(f"测试过程发生异常: {e}", exc_info=True)
        # 仍然尝试生成报告
        try:
            coordinator._finalize()
        except Exception:
            pass

    elapsed = time.time() - start_time

    # 7. 停止所有智能体
    for agent in agents:
        agent.stop()

    # 8. 输出最终统计
    logger.info("")
    logger.info("=" * 60)
    logger.info("  测试完成！")
    logger.info(f"  总耗时: {elapsed:.1f} 秒")
    logger.info(f"  消息总线统计: {bus.get_interaction_stats()}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
