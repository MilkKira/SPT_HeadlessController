from __future__ import annotations

import asyncio
import time
import unicodedata
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register


PLUGIN_VERSION = "1.5.2"
DEFAULT_TRIGGER_SUFFIX = "重启"
TRAILING_PUNCTUATION = "。.!！?？"


def normalize_text(value: str) -> str:
    """统一全半角、大小写、空白和句末标点，供触发词匹配复用。"""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = "".join(normalized.split())
    return normalized.rstrip(TRAILING_PUNCTUATION)


def extract_trigger_identifier(message: str, suffix: str) -> str | None:
    """从“节点名 + 后缀”消息中提取节点标识，不接受只有后缀的消息。"""
    normalized_message = normalize_text(message)
    normalized_suffix = normalize_text(suffix)
    if (
        not normalized_message
        or not normalized_suffix
        or not normalized_message.endswith(normalized_suffix)
        or len(normalized_message) <= len(normalized_suffix)
    ):
        return None
    return normalized_message[: -len(normalized_suffix)]


class HeadlessTriggerFilter(filter.CustomFilter):
    """在 AstrBot 唤醒阶段按插件当前配置匹配动态触发后缀。"""

    _plugin_config: AstrBotConfig | None = None

    @classmethod
    def bind_config(cls, config: AstrBotConfig) -> None:
        cls._plugin_config = config

    @classmethod
    def unbind_config(cls, config: AstrBotConfig) -> None:
        if cls._plugin_config is config:
            cls._plugin_config = None

    def filter(self, event: AstrMessageEvent, _: AstrBotConfig) -> bool:
        config = self._plugin_config
        raw_suffix = (
            config.get("trigger_suffix", DEFAULT_TRIGGER_SUFFIX)
            if config is not None
            else DEFAULT_TRIGGER_SUFFIX
        )
        suffix = "" if raw_suffix is None else str(raw_suffix)
        # 必须在过滤器阶段匹配配置后缀，避免通用消息监听器唤醒无关群消息。
        return extract_trigger_identifier(event.get_message_str(), suffix) is not None


@register(
    "spt_headless_controller",
    "Mochix2Milk",
    "监听群聊中的 Headless 故障消息并通过 Fika API 发送重启请求",
    PLUGIN_VERSION,
)
class HeadlessController(Star):
    HEADLESS_PATH = "fika/api/headless"
    RESTART_PATH = "fika/api/restartheadless"

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        HeadlessTriggerFilter.bind_config(config)
        self._session: aiohttp.ClientSession | None = None
        self._node_locks: dict[str, asyncio.Lock] = {}
        self._last_requests: dict[str, float] = {}

    async def initialize(self):
        self._session = self._create_session()
        node_names = [
            str(node.get("name", "")).strip()
            for node in self._enabled_nodes()
        ]
        logger.info(
            "SPT Headless Controller loaded: server_configured=%s, nodes=%s",
            bool(str(self.config.get("fika_server_url", "")).strip()),
            node_names,
        )
        for warning in self._configuration_warnings():
            logger.warning("SPT Headless Controller config: %s", warning)

    @filter.command("无头配置", alias={"无头配置"})
    async def headless_status(self, event: AstrMessageEvent):
        """检查 Fika API、节点和当前会话的插件配置状态。"""
        server_configured = bool(
            str(self.config.get("fika_server_url", "")).strip()
        )
        api_key_configured = bool(
            str(self.config.get("fika_api_key", "")).strip()
        )
        nodes = self._enabled_nodes()
        node_states = [
            f"{str(node.get('name', '')).strip() or '未命名'}"
            f"（profileId{'已配置' if str(node.get('profile_id', '')).strip() else '未配置'}）"
            for node in nodes
        ]
        group_id = str(event.get_group_id() or "私聊")
        allowed = self._is_allowed(event)
        node_summary = "、".join(node_states) if node_states else "未配置任何节点"
        raw_suffix = self.config.get("trigger_suffix", DEFAULT_TRIGGER_SUFFIX)
        suffix = "" if raw_suffix is None else str(raw_suffix).strip()
        verify_online = bool(self.config.get("verify_headless_online", True))
        verify_ssl = bool(self.config.get("verify_ssl", False))
        warnings = self._configuration_warnings()
        warning_summary = (
            "\n配置警告：\n- " + "\n- ".join(warnings)
            if warnings
            else "\n配置检查：未发现明显问题"
        )

        yield event.plain_result(
            "SPT Headless Controller 已加载\n"
            f"Fika Server：{'已配置' if server_configured else '未配置'}\n"
            f"有效地址：{self._server_base_url() or '未配置'}\n"
            f"API Key：{'已配置' if api_key_configured else '未配置'}\n"
            f"Headless 节点：{node_summary}\n"
            f"触发格式：<节点名>{suffix or '（后缀未配置）'}\n"
            f"重启前在线检查：{'启用' if verify_online else '关闭'}\n"
            f"HTTPS 证书验证：{'启用' if verify_ssl else '关闭'}\n"
            f"当前会话：{group_id}，{'允许' if allowed else '不允许'}触发\n"
            f"调用示例：A1{suffix or DEFAULT_TRIGGER_SUFFIX}"
            f"{warning_summary}"
        )

    @filter.custom_filter(HeadlessTriggerFilter, False)
    async def on_message(self, event: AstrMessageEvent):
        """识别“节点名 + 配置后缀”消息并调用 Fika Headless 重启接口。"""
        trigger_identifier = self._extract_trigger_identifier(event.message_str)
        if trigger_identifier is None:
            return

        if not self._is_allowed(event):
            logger.warning(
                "Ignored Headless restart trigger from sender=%s group=%s: not allowed",
                event.get_sender_id(),
                event.get_group_id(),
            )
            # 权限不足时直接终止事件，避免消息继续进入其他插件或大模型处理。
            event.stop_event()
            yield event.plain_result("您的权限不足，无法重启")
            return

        event.stop_event()
        node = self._find_node(trigger_identifier)
        if node is None:
            yield event.plain_result(
                f"识别到节点 {trigger_identifier.upper()} 的重启消息，"
                "但插件中没有配置此节点。请先在插件设置中添加节点，"
                "或发送<无头配置>检查配置。"
            )
            return

        node_name = str(node.get("name", "")).strip() or trigger_identifier
        node_key = node_name.casefold()
        configuration_error = self._request_configuration_error(node)
        if configuration_error:
            yield event.plain_result(
                f"{node_name} 重启请求失败：{configuration_error}"
            )
            return

        lock = self._node_locks.setdefault(node_key, asyncio.Lock())

        if lock.locked():
            yield event.plain_result(f"{node_name} 的重启请求正在处理中，请稍候。")
            return

        cooldown_seconds = self._config_int("cooldown_seconds", 60, minimum=0)
        elapsed = time.monotonic() - self._last_requests.get(node_key, 0.0)
        if elapsed < cooldown_seconds:
            remaining = max(1, int(cooldown_seconds - elapsed))
            yield event.plain_result(
                f"{node_name} 刚刚已经请求过重启，请 {remaining} 秒后再试。"
            )
            return

        async with lock:
            # 网络超时不代表服务端一定未收到请求，因此从首次网络检查开始计入冷却。
            self._last_requests[node_key] = time.monotonic()
            # 进度消息主动发送，最终结果作为本分支唯一的 yield，避免结果被中途吞掉。
            await event.send(
                event.plain_result(
                    f"收到，正在确认 {node_name} 状态并请求重启……"
                )
            )
            success, detail = await self._restart_with_deadline(node)

        if success:
            yield event.plain_result(f"{node_name} 重启请求已成功提交。{detail}")
        else:
            yield event.plain_result(f"{node_name} 重启请求失败：{detail}")

    def _extract_trigger_identifier(self, message: str) -> str | None:
        raw_suffix = self.config.get("trigger_suffix", DEFAULT_TRIGGER_SUFFIX)
        suffix = "" if raw_suffix is None else str(raw_suffix)
        return extract_trigger_identifier(message, suffix)

    def _find_node(self, trigger_identifier: str) -> dict[str, Any] | None:
        normalized_trigger = self._normalize_text(trigger_identifier)
        for node in self._enabled_nodes():
            aliases = self._config_sequence(node.get("aliases"))
            identifiers = [node.get("name", ""), *aliases]
            for identifier in identifiers:
                normalized_identifier = self._normalize_text(str(identifier))
                if (
                    normalized_identifier
                    and normalized_trigger == normalized_identifier
                ):
                    return node
        return None

    def _is_allowed(self, event: AstrMessageEvent) -> bool:
        group_id = str(event.get_group_id() or "")
        sender_id = str(event.get_sender_id() or "")

        if not group_id and not self.config.get("allow_private_messages", False):
            return False

        allowed_groups = {
            str(value).strip()
            for value in self._config_sequence(
                self.config.get("allowed_group_ids")
            )
            if str(value).strip()
        }
        # 私聊没有群号；开启私聊后不应再被群白名单误伤。
        if group_id and allowed_groups and group_id not in allowed_groups:
            return False

        allowed_senders = {
            str(value).strip()
            for value in self._config_sequence(
                self.config.get("allowed_sender_ids")
            )
            if str(value).strip()
        }
        return not allowed_senders or sender_id in allowed_senders

    async def _restart_headless(
        self, node: dict[str, Any]
    ) -> tuple[bool, str]:
        base_url = self._server_base_url()
        api_key = str(self.config.get("fika_api_key", "")).strip()
        profile_id = str(node.get("profile_id", "")).strip()

        configuration_error = self._request_configuration_error(node)
        if configuration_error:
            return False, configuration_error

        # Fika API 要求 Bearer 鉴权，并关闭 SPT 的压缩响应封装。
        headers = {
            "Authorization": f"Bearer {api_key}",
            "requestcompressed": "0",
        }

        try:
            if self.config.get("verify_headless_online", True):
                logger.info(
                    "Checking Fika Headless list before restart: url=%s/%s",
                    base_url,
                    self.HEADLESS_PATH,
                )
                online, detail = await self._verify_headless(
                    base_url, headers, profile_id
                )
                if not online:
                    return False, detail

            session = await self._get_session()
            restart_url = f"{base_url}/{self.RESTART_PATH}"
            verify_ssl = bool(self.config.get("verify_ssl", False))
            logger.info(
                "Submitting Fika Headless restart: url=%s, verify_ssl=%s",
                restart_url,
                verify_ssl,
            )
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
                    return True, "（API HTTP 200）"
                return False, self._http_error(response.status)
        except TimeoutError:
            logger.warning(
                "Fika request timed out for Headless %s",
                node.get("name", "unknown"),
            )
            return False, "连接 Fika Server 超时。"
        except aiohttp.ClientConnectorCertificateError:
            logger.warning(
                "Fika HTTPS certificate validation failed for %s",
                base_url,
            )
            return (
                False,
                "HTTPS 证书验证失败。SPT 默认使用自签名证书，"
                "请在插件设置中关闭“验证 HTTPS 证书”。",
            )
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

    async def _restart_with_deadline(
        self, node: dict[str, Any]
    ) -> tuple[bool, str]:
        """在 aiohttp 超时之外增加硬截止时间，保证消息处理一定能返回。"""
        timeout_seconds = self._config_int(
            "request_timeout_seconds", 10, minimum=1
        )
        try:
            return await asyncio.wait_for(
                self._restart_headless(node),
                timeout=timeout_seconds + 2,
            )
        except TimeoutError:
            logger.warning(
                "Headless restart exceeded hard deadline: node=%s, timeout=%ss",
                node.get("name", "unknown"),
                timeout_seconds + 2,
            )
            return False, "等待 Fika API 响应超时，请检查服务器地址、防火墙和日志。"
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Unexpected error escaped Headless restart: node=%s",
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
        verify_ssl = bool(self.config.get("verify_ssl", False))

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
        timeout_seconds = self._config_int(
            "request_timeout_seconds", 10, minimum=1
        )
        return aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(
                total=timeout_seconds,
                connect=min(5, timeout_seconds),
                sock_connect=min(5, timeout_seconds),
                sock_read=timeout_seconds,
            )
        )

    def _enabled_nodes(self) -> list[dict[str, Any]]:
        nodes = self._config_sequence(self.config.get("nodes"))
        return [
            node
            for node in nodes
            if isinstance(node, dict) and node.get("enabled", True)
        ]

    def _config_int(self, key: str, default: int, minimum: int) -> int:
        try:
            value = int(self.config.get(key, default))
        except (TypeError, ValueError):
            return default
        return max(minimum, value)

    def _configuration_warnings(self) -> list[str]:
        warnings: list[str] = []
        base_url = self._server_base_url()
        raw_suffix = self.config.get("trigger_suffix", DEFAULT_TRIGGER_SUFFIX)
        suffix = "" if raw_suffix is None else str(raw_suffix).strip()

        if not base_url:
            warnings.append("未配置 Fika Server 地址")
        elif not self._is_valid_server_url(base_url):
            warnings.append("Fika Server 地址必须是完整的 HTTP/HTTPS 地址")
        elif urlsplit(base_url).scheme.casefold() == "http":
            warnings.append(
                "SPT/Fika 默认使用 HTTPS；仅在明确配置 HTTP 反向代理时使用 HTTP"
            )
        elif bool(self.config.get("verify_ssl", False)):
            warnings.append(
                "若服务器仍使用 SPT 默认自签名证书，请关闭 HTTPS 证书验证"
            )
        if not str(self.config.get("fika_api_key", "")).strip():
            warnings.append("未配置 Fika API Key")
        if not normalize_text(suffix):
            warnings.append("触发消息后缀不能为空")

        nodes = self._enabled_nodes()
        if not nodes:
            warnings.append("未配置任何已启用的 Headless 节点")

        known_identifiers: dict[str, str] = {}
        for index, node in enumerate(nodes, start=1):
            name = str(node.get("name", "")).strip()
            profile_id = str(node.get("profile_id", "")).strip()
            display_name = name or f"第 {index} 个节点"
            if not name:
                warnings.append(f"{display_name}未填写节点名")
            if not profile_id:
                warnings.append(f"{display_name}未填写 profileId")

            identifiers = [name, *self._config_sequence(node.get("aliases"))]
            for identifier in identifiers:
                normalized = normalize_text(str(identifier))
                if not normalized:
                    continue
                previous = known_identifiers.get(normalized)
                if previous and previous != display_name:
                    warnings.append(
                        f"节点标识“{identifier}”与 {previous} 重复，将优先匹配前者"
                    )
                else:
                    known_identifiers[normalized] = display_name

        for key, label, minimum in (
            ("request_timeout_seconds", "请求超时秒数", 1),
            ("cooldown_seconds", "节点冷却秒数", 0),
        ):
            try:
                value = int(self.config.get(key, minimum))
            except (TypeError, ValueError):
                warnings.append(f"{label}不是有效整数，将使用默认值")
                continue
            if value < minimum:
                warnings.append(f"{label}不能小于 {minimum}，运行时会自动修正")

        return warnings

    def _request_configuration_error(
        self, node: dict[str, Any]
    ) -> str | None:
        base_url = self._server_base_url()
        if not base_url:
            return "未配置 Fika Server 地址。"
        if not str(self.config.get("fika_api_key", "")).strip():
            return "未配置 Fika API Key。"
        if not str(node.get("profile_id", "")).strip():
            return "此节点未配置 Headless profileId。"
        if not self._is_valid_server_url(base_url):
            return "Fika Server 地址无效，必须是完整的 HTTP/HTTPS 地址。"
        return None

    def _server_base_url(self) -> str:
        raw_url = str(self.config.get("fika_server_url", "")).strip().rstrip("/")
        if not raw_url:
            return ""
        # SPT/Fika 默认提供 HTTPS；只填写主机和端口时自动补全安全协议。
        if "://" not in raw_url:
            raw_url = f"https://{raw_url}"
        return raw_url

    @staticmethod
    def _config_sequence(value: Any) -> list[Any]:
        if value is None or value == "":
            return []
        if isinstance(value, (list, tuple, set)):
            return list(value)
        # 对损坏或旧版配置做单值兼容，避免把字符串按字符拆成多个别名/ID。
        return [value]

    @staticmethod
    def _is_valid_server_url(value: str) -> bool:
        try:
            parsed = urlsplit(value)
            # 访问 port 会主动识别非法端口和格式错误的 IPv6 地址。
            _ = parsed.port
        except ValueError:
            return False
        return parsed.scheme.casefold() in {"http", "https"} and bool(
            parsed.hostname
        )

    @staticmethod
    def _normalize_text(value: str) -> str:
        return normalize_text(value)

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
        HeadlessTriggerFilter.unbind_config(self.config)
        if self._session is not None and not self._session.closed:
            await self._session.close()
