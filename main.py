from __future__ import annotations

import asyncio
import time
import unicodedata
from typing import Any

import aiohttp

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register


HEADLESS_TRIGGER_PATTERN = (
    r"(?i)^\s*[a-z][a-z0-9_-]*\s*卡了\s*[。.!！?？]?\s*$"
)


@register(
    "spt_headless_controller",
    "Mochix2Neko",
    "监听群聊中的 Headless 故障消息并通过 Fika API 发送重启请求",
    "1.4.0",
)
class HeadlessController(Star):
    HEADLESS_PATH = "fika/api/headless"
    RESTART_PATH = "fika/api/restartheadless"

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._session: aiohttp.ClientSession | None = None
        self._node_locks: dict[str, asyncio.Lock] = {}
        self._last_requests: dict[str, float] = {}

    async def initialize(self):
        self._session = self._create_session()
        node_names = [
            str(node.get("name", "")).strip()
            for node in self.config.get("nodes", [])
            if isinstance(node, dict) and node.get("enabled", True)
        ]
        logger.info(
            "SPT Headless Controller loaded: server_configured=%s, nodes=%s",
            bool(str(self.config.get("fika_server_url", "")).strip()),
            node_names,
        )

    @filter.command("headless_status", alias={"无头状态"})
    async def headless_status(self, event: AstrMessageEvent):
        """检查 Fika API、节点和当前会话的插件配置状态。"""
        server_configured = bool(
            str(self.config.get("fika_server_url", "")).strip()
        )
        api_key_configured = bool(
            str(self.config.get("fika_api_key", "")).strip()
        )
        nodes = [
            node
            for node in self.config.get("nodes", [])
            if isinstance(node, dict) and node.get("enabled", True)
        ]
        node_states = [
            f"{str(node.get('name', '')).strip() or '未命名'}"
            f"（profileId{'已配置' if str(node.get('profile_id', '')).strip() else '未配置'}）"
            for node in nodes
        ]
        group_id = str(event.get_group_id() or "私聊")
        allowed = self._is_allowed(event)
        node_summary = "、".join(node_states) if node_states else "未配置任何节点"

        yield event.plain_result(
            "SPT Headless Controller 已加载\n"
            f"Fika Server：{'已配置' if server_configured else '未配置'}\n"
            f"API Key：{'已配置' if api_key_configured else '未配置'}\n"
            f"Headless 节点：{node_summary}\n"
            f"当前会话：{group_id}，{'允许' if allowed else '不允许'}触发\n"
            "调用格式：A1卡了"
        )

    @filter.regex(HEADLESS_TRIGGER_PATTERN)
    async def on_message(self, event: AstrMessageEvent):
        """识别“节点名卡了”消息并调用 Fika Headless 重启接口。"""
        trigger_identifier = self._extract_trigger_identifier(event.message_str)
        if trigger_identifier is None:
            return

        if not self._is_allowed(event):
            logger.warning(
                "Ignored Headless restart trigger from sender=%s group=%s: not allowed",
                event.get_sender_id(),
                event.get_group_id(),
            )
            return

        event.stop_event()
        node = self._find_node(trigger_identifier)
        if node is None:
            yield event.plain_result(
                f"识别到节点 {trigger_identifier.upper()} 的重启消息，"
                "但插件中没有配置此节点。请先在插件设置中添加节点，"
                "或发送 /headless_status 检查配置。"
            )
            return

        node_name = str(node["name"]).strip()
        node_key = node_name.casefold()
        lock = self._node_locks.setdefault(node_key, asyncio.Lock())

        if lock.locked():
            yield event.plain_result(f"{node_name} 的重启请求正在处理中，请稍候。")
            return

        cooldown_seconds = max(0, int(self.config.get("cooldown_seconds", 60)))
        elapsed = time.monotonic() - self._last_requests.get(node_key, 0.0)
        if elapsed < cooldown_seconds:
            remaining = max(1, int(cooldown_seconds - elapsed))
            yield event.plain_result(
                f"{node_name} 刚刚已经请求过重启，请 {remaining} 秒后再试。"
            )
            return

        async with lock:
            self._last_requests[node_key] = time.monotonic()
            yield event.plain_result(f"收到，正在确认 {node_name} 状态并请求重启……")
            success, detail = await self._restart_headless(node)

        if success:
            yield event.plain_result(f"{node_name} 重启请求已成功提交。{detail}")
        else:
            yield event.plain_result(f"{node_name} 重启请求失败：{detail}")

    def _extract_trigger_identifier(self, message: str) -> str | None:
        normalized_message = self._normalize_text(message)
        suffix = self._normalize_text(str(self.config.get("trigger_suffix", "卡了")))
        if (
            not normalized_message
            or not suffix
            or not normalized_message.endswith(suffix)
            or len(normalized_message) <= len(suffix)
        ):
            return None
        return normalized_message[: -len(suffix)]

    def _find_node(self, trigger_identifier: str) -> dict[str, Any] | None:
        normalized_trigger = self._normalize_text(trigger_identifier)
        for node in self.config.get("nodes", []):
            if not isinstance(node, dict) or not node.get("enabled", True):
                continue

            aliases = node.get("aliases") or []
            identifiers = [node.get("name", ""), *aliases]
            for identifier in identifiers:
                normalized_identifier = self._normalize_text(str(identifier))
                if normalized_identifier and normalized_trigger == normalized_identifier:
                    return node
        return None

    def _is_allowed(self, event: AstrMessageEvent) -> bool:
        group_id = str(event.get_group_id() or "")
        sender_id = str(event.get_sender_id() or "")

        if not group_id and not self.config.get("allow_private_messages", False):
            return False

        allowed_groups = {
            str(value).strip()
            for value in self.config.get("allowed_group_ids", [])
            if str(value).strip()
        }
        if allowed_groups and group_id not in allowed_groups:
            return False

        allowed_senders = {
            str(value).strip()
            for value in self.config.get("allowed_sender_ids", [])
            if str(value).strip()
        }
        return not allowed_senders or sender_id in allowed_senders

    async def _restart_headless(
        self, node: dict[str, Any]
    ) -> tuple[bool, str]:
        base_url = str(self.config.get("fika_server_url", "")).strip().rstrip("/")
        api_key = str(self.config.get("fika_api_key", "")).strip()
        profile_id = str(node.get("profile_id", "")).strip()

        if not base_url:
            return False, "未配置 Fika Server 地址。"
        if not api_key:
            return False, "未配置 Fika API Key。"
        if not profile_id:
            return False, "此节点未配置 Headless profileId。"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "requestcompressed": "0",
        }

        try:
            if self.config.get("verify_headless_online", True):
                online, detail = await self._verify_headless(
                    base_url, headers, profile_id
                )
                if not online:
                    return False, detail

            session = await self._get_session()
            restart_url = f"{base_url}/{self.RESTART_PATH}"
            verify_ssl = bool(self.config.get("verify_ssl", True))
            async with session.post(
                restart_url,
                headers=headers,
                json={"profileId": profile_id},
                ssl=None if verify_ssl else False,
            ) as response:
                logger.info(
                    "Fika restart response for Headless %s: HTTP %s",
                    node.get("name", "unknown"),
                    response.status,
                )
                if response.status == 200:
                    return True, "（Fika API HTTP 200）"
                return False, self._http_error(response.status)
        except asyncio.TimeoutError:
            return False, "连接 Fika Server 超时。"
        except aiohttp.ClientError as exc:
            logger.warning(
                "Fika restart request for Headless %s failed: %s",
                node.get("name", "unknown"),
                exc,
            )
            return False, f"无法连接 Fika Server（{type(exc).__name__}）。"
        except Exception:
            logger.exception(
                "Unexpected error while restarting Headless %s",
                node.get("name", "unknown"),
            )
            return False, "插件处理请求时发生异常，请检查 AstrBot 日志。"

    async def _verify_headless(
        self,
        base_url: str,
        headers: dict[str, str],
        profile_id: str,
    ) -> tuple[bool, str]:
        session = await self._get_session()
        headless_url = f"{base_url}/{self.HEADLESS_PATH}"
        verify_ssl = bool(self.config.get("verify_ssl", True))

        async with session.get(
            headless_url,
            headers=headers,
            ssl=None if verify_ssl else False,
        ) as response:
            if response.status != 200:
                return False, self._http_error(response.status)

            try:
                payload = await response.json(content_type=None)
            except (ValueError, TypeError):
                return False, "Fika Headless 列表响应不是有效 JSON。"

        clients = self._json_value(payload, "headlessClients")
        if not isinstance(clients, list):
            return False, "Fika Headless 列表响应缺少 headlessClients。"

        for client in clients:
            if not isinstance(client, dict):
                continue
            current_profile_id = str(
                self._json_value(client, "profileId") or ""
            ).strip()
            if current_profile_id == profile_id:
                return True, ""

        return False, "目标 profileId 当前不在 Fika Headless 列表中。"

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = self._create_session()
        return self._session

    def _create_session(self) -> aiohttp.ClientSession:
        timeout_seconds = max(1, int(self.config.get("request_timeout_seconds", 10)))
        return aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout_seconds)
        )

    @staticmethod
    def _normalize_text(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).casefold()
        normalized = "".join(normalized.split())
        return normalized.rstrip("。.!！?？")

    @staticmethod
    def _json_value(data: Any, key: str) -> Any:
        if not isinstance(data, dict):
            return None
        for current_key, value in data.items():
            if str(current_key).casefold() == key.casefold():
                return value
        return None

    @staticmethod
    def _http_error(status: int) -> str:
        if status == 401:
            return "Fika API 返回 HTTP 401，请检查 Authorization 配置。"
        if status == 403:
            return "Fika API 返回 HTTP 403，API Key 不正确。"
        if status == 404:
            return "Fika API 返回 HTTP 404，Headless 不在线或接口不可用。"
        return f"Fika API 返回 HTTP {status}。"

    async def terminate(self):
        if self._session is not None and not self._session.closed:
            await self._session.close()
