# base_agent.py - Agent 基类
"""
所有智能体的抽象基类。
提供消息总线接入、日志、生命周期管理等公共能力。
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

from core.message_bus import MessageBus
from core.agent_api import AgentAPIAdvisor


class BaseAgent(ABC):
    """
    智能体基类。

    所有智能体继承此类，获得以下能力：
    - 消息总线接入（发布/订阅）
    - 统一日志记录
    - 生命周期管理（初始化/启动/停止）
    - 配置管理
    """

    def __init__(self, name: str, bus: MessageBus, config: Optional[dict] = None):
        """
        初始化智能体。

        Args:
            name: 智能体名称（如 "Coordinator", "Analyzer"）
            bus: 消息总线实例
            config: 智能体配置字典
        """
        self.name = name
        self.bus = bus
        self.config = config or {}
        self.logger = logging.getLogger(f"agent.{name}")
        self._running = False
        self.api_advisor = AgentAPIAdvisor.from_config(name, self.config, self.logger)

    def publish(self, topic: str, data: Any):
        """发布消息到消息总线"""
        self.bus.publish(topic, data, sender=self.name)

    def subscribe(self, topic: str, handler):
        """订阅消息主题"""
        self.bus.subscribe(topic, handler)

    @abstractmethod
    def setup(self):
        """
        智能体初始化：注册消息订阅。
        子类必须实现此方法，在其中调用 self.subscribe() 注册消息处理器。
        """
        pass

    def start(self):
        """启动智能体"""
        self._running = True
        self.setup()
        self.logger.info(f"[{self.name}] 智能体已启动")

    def stop(self):
        """停止智能体"""
        self._running = False
        self.logger.info(f"[{self.name}] 智能体已停止")

    @property
    def is_running(self) -> bool:
        return self._running

    def log_info(self, message: str):
        """记录信息级别日志"""
        self.logger.info(f"[{self.name}] {message}")

    def log_warning(self, message: str):
        """记录警告级别日志"""
        self.logger.warning(f"[{self.name}] {message}")

    def log_error(self, message: str):
        """记录错误级别日志"""
        self.logger.error(f"[{self.name}] {message}")

    def log_debug(self, message: str):
        """记录调试级别日志"""
        self.logger.debug(f"[{self.name}] {message}")

    def __repr__(self):
        status = "运行中" if self._running else "已停止"
        return f"{self.__class__.__name__}(name={self.name}, status={status})"
