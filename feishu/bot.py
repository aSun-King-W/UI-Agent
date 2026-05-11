"""
飞书机器人客户端 - 长连接模式
基于 lark-oapi SDK 实现长连接接收事件
"""

import os
import json
import logging
from typing import Callable

from lark_oapi import FEISHU_DOMAIN
from lark_oapi.ws import Client as WSClient
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

logger = logging.getLogger(__name__)


class FeishuBot:
    """飞书机器人，通过长连接接收消息事件"""

    def __init__(self, app_id: str = None, app_secret: str = None):
        self.app_id = app_id or os.getenv("FEISHU_APP_ID")
        self.app_secret = app_secret or os.getenv("FEISHU_APP_SECRET")
        self._message_handler: Callable = None
        self._ws_client: WSClient = None

    def on_message(self, handler: Callable):
        """注册消息处理函数

        handler 签名: (message_text: str, sender_id: dict, chat_id: str) -> str
        返回值将作为回复消息发送
        """
        self._message_handler = handler
        return handler

    def _handle_event(self, event: P2ImMessageReceiveV1):
        """处理收到的飞书消息事件"""
        if not self._message_handler:
            return

        message = event.event.message
        chat_id = message.chat_id
        sender_id = event.event.sender.sender_id

        # 提取消息内容（飞书长连接中字段名为 msg_type 但映射后变为 message_type）
        msg_type = getattr(message, "message_type", None) or "text"
        content_str = message.content or "{}"

        # 默认当做文本解析
        try:
            content = json.loads(content_str)
            text = content.get("text", content_str)
        except json.JSONDecodeError:
            text = content_str

        logger.info(f"收到消息: {text} | chat: {chat_id}")

        try:
            reply = self._message_handler(text, sender_id, chat_id)
            if reply:
                self.send_text(chat_id, reply)
        except Exception as e:
            logger.error(f"处理消息异常: {e}")
            self.send_text(chat_id, f"处理出错: {str(e)}")

    def send_text(self, chat_id: str, text: str):
        """发送文本消息到指定会话"""
        from lark_oapi import Client
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        client = Client.builder().app_id(self.app_id).app_secret(self.app_secret).domain(FEISHU_DOMAIN).build()

        content = json.dumps({"text": text})
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(content)
                .build()
            )
            .build()
        )
        client.im.v1.message.create(request)

    def start(self):
        """启动长连接，开始监听消息"""
        # 使用 builder 模式注册事件
        handler = (
            EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._handle_event)
            .build()
        )

        # 创建并启动 WSClient
        self._ws_client = WSClient(
            app_id=self.app_id,
            app_secret=self.app_secret,
            event_handler=handler,
            domain=FEISHU_DOMAIN,
            auto_reconnect=True,
        )

        logger.info("飞书机器人长连接已启动，等待消息...")
        self._ws_client.start()

    def stop(self):
        """停止长连接"""
        if self._ws_client:
            self._ws_client.stop()
