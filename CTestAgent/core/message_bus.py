# message_bus.py - 消息总线
"""
智能体间通信的核心基础设施。
采用发布/订阅模式，解耦智能体间通信。
进程内同步调用，避免网络开销。
"""

import time
import logging
from collections import defaultdict, Counter
from typing import Any, Callable

from core.message_types import Message

logger = logging.getLogger(__name__)


class MessageBus:
    """
    智能体间通信的消息总线。

    所有智能体通过统一的消息总线通信，采用发布/订阅模式：
    - 智能体通过 subscribe() 订阅感兴趣的主题
    - 智能体通过 publish() 发布消息到某个主题
    - 消息总线将消息同步分发给所有订阅者
    - 全量消息日志供 Reporter Agent 统计分析
    """

    def __init__(self):
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)
        self._message_log: list[Message] = []
        self._publish_hooks: list[Callable[[Message], None]] = []
        self._enabled = True

    def add_publish_hook(self, hook: Callable[[Message], None]):
        """注册发布钩子（用于 WebSocket 广播等扩展能力）"""
        if hook not in self._publish_hooks:
            self._publish_hooks.append(hook)

    def remove_publish_hook(self, hook: Callable[[Message], None]):
        """移除发布钩子"""
        self._publish_hooks = [h for h in self._publish_hooks if h != hook]

    def subscribe(self, topic: str, handler: Callable):
        """
        智能体订阅某个主题。

        Args:
            topic: 消息主题（如 "cmd:generate", "data:test_cases"）
            handler: 消息处理函数，接收 Message 对象
        """
        self._subscribers[topic].append(handler)
        logger.debug(f"订阅主题: {topic} -> {handler.__qualname__}")

    def unsubscribe(self, topic: str, handler: Callable):
        """取消订阅"""
        if topic in self._subscribers:
            self._subscribers[topic] = [
                h for h in self._subscribers[topic] if h != handler
            ]

    def publish(self, topic: str, data: Any, sender: str = ""):
        """
        智能体发布消息到某个主题。

        Args:
            topic: 消息主题
            data: 消息数据
            sender: 发送者名称
        """
        if not self._enabled:
            return

        msg = Message(
            topic=topic,
            data=data,
            sender=sender,
            timestamp=time.time()
        )
        self._message_log.append(msg)

        # 先触发扩展钩子（即使当前主题无订阅者，也能用于外部实时广播）
        for hook in list(self._publish_hooks):
            try:
                hook(msg)
            except Exception as e:
                logger.error(f"publish hook 执行失败: {e}", exc_info=True)

        handlers = self._subscribers.get(topic, [])
        if not handlers:
            logger.warning(f"主题 '{topic}' 没有订阅者 (发送者: {sender})")
            return

        logger.info(f"[MessageBus] {sender} -> {topic} ({len(handlers)} 个订阅者)")

        for handler in handlers:
            try:
                handler(msg)
            except Exception as e:
                logger.error(
                    f"处理消息时出错: topic={topic}, sender={sender}, "
                    f"handler={handler.__qualname__}, error={e}",
                    exc_info=True
                )

    def get_message_log(self) -> list[Message]:
        """获取完整的消息日志"""
        return list(self._message_log)

    def get_interaction_stats(self) -> dict:
        """
        统计智能体间的交互数据（供 Reporter Agent 使用）。

        Returns:
            包含总消息数、按主题和发送者统计的字典
        """
        return {
            "total_messages": len(self._message_log),
            "messages_by_topic": dict(Counter(m.topic for m in self._message_log)),
            "messages_by_sender": dict(Counter(m.sender for m in self._message_log)),
        }

    def get_messages_between(self, sender: str, topic: str) -> list[Message]:
        """获取特定发送者在特定主题上的消息"""
        return [
            m for m in self._message_log
            if m.sender == sender and m.topic == topic
        ]

    def clear_log(self):
        """清空消息日志（谨慎使用）"""
        self._message_log.clear()

    def disable(self):
        """禁用消息总线（用于关闭系统时）"""
        self._enabled = False

    def enable(self):
        """启用消息总线"""
        self._enabled = True

    @property
    def subscriber_count(self) -> int:
        """当前总订阅数"""
        return sum(len(handlers) for handlers in self._subscribers.values())

    @property
    def topic_list(self) -> list[str]:
        """当前所有已注册的主题"""
        return list(self._subscribers.keys())

    def __repr__(self):
        return (
            f"MessageBus(topics={len(self._subscribers)}, "
            f"subscribers={self.subscriber_count}, "
            f"messages_logged={len(self._message_log)})"
        )
