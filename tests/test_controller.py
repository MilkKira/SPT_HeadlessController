from __future__ import annotations

import json
import sys
import types
import unittest
from pathlib import Path
from typing import Any


class _Logger:
    def info(self, *_: Any, **__: Any) -> None:
        pass

    def warning(self, *_: Any, **__: Any) -> None:
        pass

    def exception(self, *_: Any, **__: Any) -> None:
        pass


class _CustomFilter:
    def __init__(self, raise_error: bool = True) -> None:
        self.raise_error = raise_error


def _decorator(*_: Any, **__: Any):
    def decorate(target: Any) -> Any:
        return target

    return decorate


def _install_dependency_stubs() -> None:
    """只替代框架边界，让纯配置逻辑可在未安装 AstrBot 时测试。"""
    aiohttp = types.ModuleType("aiohttp")

    class ClientError(Exception):
        pass

    class ClientConnectorCertificateError(ClientError):
        pass

    class ClientTimeout:
        def __init__(self, total: int, **kwargs: Any) -> None:
            self.total = total
            self.options = kwargs

    class ClientSession:
        def __init__(self, **kwargs: Any) -> None:
            self.closed = False
            self.kwargs = kwargs

        async def close(self) -> None:
            self.closed = True

    aiohttp.ClientError = ClientError
    aiohttp.ClientConnectorCertificateError = ClientConnectorCertificateError
    aiohttp.ClientTimeout = ClientTimeout
    aiohttp.ClientSession = ClientSession
    sys.modules.setdefault("aiohttp", aiohttp)

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event = types.ModuleType("astrbot.api.event")
    star = types.ModuleType("astrbot.api.star")

    class AstrMessageEvent:
        pass

    class Star:
        def __init__(self, context: Any) -> None:
            self.context = context

    filter_api = types.SimpleNamespace(
        CustomFilter=_CustomFilter,
        command=_decorator,
        custom_filter=_decorator,
    )

    api.AstrBotConfig = dict
    api.logger = _Logger()
    event.AstrMessageEvent = AstrMessageEvent
    event.filter = filter_api
    star.Context = object
    star.Star = Star
    star.register = _decorator

    sys.modules.setdefault("astrbot", astrbot)
    sys.modules.setdefault("astrbot.api", api)
    sys.modules.setdefault("astrbot.api.event", event)
    sys.modules.setdefault("astrbot.api.star", star)


_install_dependency_stubs()

import main  # noqa: E402


class _Event:
    def __init__(
        self,
        message: str = "",
        group_id: str | None = None,
        sender_id: str | None = None,
    ) -> None:
        self.message_str = message
        self._group_id = group_id
        self._sender_id = sender_id
        self.stopped = False
        self.sent: list[str] = []

    def get_message_str(self) -> str:
        return self.message_str

    def get_group_id(self) -> str | None:
        return self._group_id

    def get_sender_id(self) -> str | None:
        return self._sender_id

    def stop_event(self) -> None:
        self.stopped = True

    def plain_result(self, value: str) -> str:
        return value

    async def send(self, value: str) -> None:
        self.sent.append(value)


class _Response:
    def __init__(self, status: int, payload: Any = None) -> None:
        self.status = status
        self._payload = payload

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass

    async def json(self, **_: Any) -> Any:
        return self._payload


class _Session:
    def __init__(self, get_response: _Response, post_response: _Response) -> None:
        self.closed = False
        self.get_response = get_response
        self.post_response = post_response
        self.get_call: dict[str, Any] | None = None
        self.post_call: dict[str, Any] | None = None

    def get(self, url: str, **kwargs: Any) -> _Response:
        self.get_call = {"url": url, **kwargs}
        return self.get_response

    def post(self, url: str, **kwargs: Any) -> _Response:
        self.post_call = {"url": url, **kwargs}
        return self.post_response


def _config(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "fika_server_url": "127.0.0.1:6969",
        "fika_api_key": "secret",
        "verify_headless_online": True,
        "verify_ssl": False,
        "trigger_suffix": "卡了",
        "allowed_group_ids": [],
        "allowed_sender_ids": [],
        "allow_private_messages": False,
        "request_timeout_seconds": 10,
        "cooldown_seconds": 60,
        "nodes": [
            {
                "name": "A1",
                "aliases": ["一号节点"],
                "profile_id": "profile-a1",
                "enabled": True,
            }
        ],
    }
    config.update(overrides)
    return config


class TriggerAndAccessTests(unittest.TestCase):
    def test_dynamic_suffix_is_used_during_wake_filtering(self) -> None:
        config = _config(trigger_suffix="掉线")
        main.HeadlessTriggerFilter.bind_config(config)
        trigger_filter = main.HeadlessTriggerFilter(False)

        self.assertTrue(trigger_filter.filter(_Event("一号节点 掉线！"), {}))
        self.assertFalse(trigger_filter.filter(_Event("A1卡了"), {}))

    def test_unicode_alias_and_scalar_legacy_alias_are_supported(self) -> None:
        node = {
            "name": "A1",
            "aliases": "中文节点",
            "profile_id": "profile-a1",
            "enabled": True,
        }
        controller = main.HeadlessController(None, _config(nodes=[node]))

        self.assertIs(controller._find_node("中文 节点"), node)

    def test_private_message_ignores_group_whitelist_when_enabled(self) -> None:
        controller = main.HeadlessController(
            None,
            _config(
                allow_private_messages=True,
                allowed_group_ids=["10001"],
                allowed_sender_ids=["20002"],
            ),
        )

        self.assertTrue(controller._is_allowed(_Event(sender_id="20002")))
        self.assertFalse(controller._is_allowed(_Event(sender_id="other")))

    def test_group_and_disabled_node_filters_still_apply(self) -> None:
        disabled = {
            "name": "W3",
            "profile_id": "profile-w3",
            "enabled": False,
        }
        controller = main.HeadlessController(
            None,
            _config(nodes=[*_config()["nodes"], disabled], allowed_group_ids=["1"]),
        )

        self.assertTrue(controller._is_allowed(_Event(group_id="1", sender_id="2")))
        self.assertFalse(controller._is_allowed(_Event(group_id="9", sender_id="2")))
        self.assertIsNone(controller._find_node("W3"))

    def test_invalid_integer_config_uses_safe_values(self) -> None:
        controller = main.HeadlessController(
            None,
            _config(request_timeout_seconds="bad", cooldown_seconds=-2),
        )

        self.assertEqual(controller._config_int("request_timeout_seconds", 10, 1), 10)
        self.assertEqual(controller._config_int("cooldown_seconds", 60, 0), 0)
        warnings = controller._configuration_warnings()
        self.assertTrue(any("请求超时秒数不是有效整数" in item for item in warnings))
        self.assertTrue(any("节点冷却秒数不能小于 0" in item for item in warnings))

    def test_timeout_config_is_applied_to_new_session(self) -> None:
        controller = main.HeadlessController(
            None,
            _config(request_timeout_seconds=27),
        )

        session = controller._create_session()

        self.assertEqual(session.kwargs["timeout"].total, 27)
        self.assertEqual(session.kwargs["timeout"].options["connect"], 5)

    def test_missing_scheme_defaults_to_https(self) -> None:
        controller = main.HeadlessController(None, _config())

        self.assertEqual(
            controller._server_base_url(),
            "https://127.0.0.1:6969",
        )

    def test_explicit_http_is_preserved(self) -> None:
        controller = main.HeadlessController(
            None,
            _config(fika_server_url="http://127.0.0.1:6969"),
        )

        self.assertEqual(
            controller._server_base_url(),
            "http://127.0.0.1:6969",
        )


class ApiRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_sender_outside_whitelist_gets_permission_denied_reply(self) -> None:
        controller = main.HeadlessController(
            None,
            _config(allowed_group_ids=["1"], allowed_sender_ids=["allowed"]),
        )
        event = _Event("A1卡了", group_id="1", sender_id="denied")

        results = [result async for result in controller.on_message(event)]

        self.assertEqual(results, ["您的权限不足，无法重启"])
        self.assertTrue(event.stopped)
        self.assertEqual(controller._last_requests, {})

    async def test_group_outside_whitelist_gets_permission_denied_reply(self) -> None:
        controller = main.HeadlessController(
            None,
            _config(allowed_group_ids=["1"], allowed_sender_ids=["allowed"]),
        )
        event = _Event("A1卡了", group_id="9", sender_id="allowed")

        results = [result async for result in controller.on_message(event)]

        self.assertEqual(results, ["您的权限不足，无法重启"])
        self.assertTrue(event.stopped)
        self.assertEqual(controller._last_requests, {})

    async def test_static_config_error_does_not_start_cooldown(self) -> None:
        controller = main.HeadlessController(None, _config(fika_api_key=""))
        event = _Event("A1卡了", group_id="1", sender_id="2")

        results = [result async for result in controller.on_message(event)]

        self.assertTrue(event.stopped)
        self.assertTrue(any("未配置 Fika API Key" in result for result in results))
        self.assertEqual(controller._last_requests, {})

    async def test_all_api_related_options_reach_the_request(self) -> None:
        payload = {"HeadlessClients": [{"ProfileID": "profile-a1"}]}
        session = _Session(_Response(200, payload), _Response(200))
        controller = main.HeadlessController(
            None,
            _config(verify_headless_online=True, verify_ssl=False),
        )
        controller._session = session

        success, detail = await controller._restart_headless(_config()["nodes"][0])

        self.assertTrue(success)
        self.assertIn("HTTP 200", detail)
        self.assertEqual(
            session.get_call["url"],
            "https://127.0.0.1:6969/fika/api/headless",
        )
        self.assertIs(session.get_call["ssl"], False)
        self.assertEqual(
            session.post_call["json"],
            {"profileId": "profile-a1"},
        )
        self.assertEqual(
            session.post_call["headers"]["Authorization"],
            "Bearer secret",
        )
        self.assertEqual(
            session.post_call["headers"]["requestcompressed"],
            "0",
        )

    async def test_online_check_can_be_disabled(self) -> None:
        session = _Session(_Response(500), _Response(200))
        controller = main.HeadlessController(
            None,
            _config(verify_headless_online=False),
        )
        controller._session = session

        success, _ = await controller._restart_headless(_config()["nodes"][0])

        self.assertTrue(success)
        self.assertIsNone(session.get_call)
        self.assertIsNotNone(session.post_call)

    async def test_invalid_server_url_stops_before_network(self) -> None:
        session = _Session(_Response(200), _Response(200))
        controller = main.HeadlessController(
            None,
            _config(fika_server_url="ftp://127.0.0.1:6969"),
        )
        controller._session = session

        success, detail = await controller._restart_headless(_config()["nodes"][0])

        self.assertFalse(success)
        self.assertIn("HTTP/HTTPS", detail)
        self.assertIsNone(session.get_call)
        self.assertIsNone(session.post_call)

    async def test_malformed_port_is_reported_as_invalid_url(self) -> None:
        controller = main.HeadlessController(
            None,
            _config(fika_server_url="http://127.0.0.1:not-a-port"),
        )

        success, detail = await controller._restart_headless(_config()["nodes"][0])

        self.assertFalse(success)
        self.assertIn("HTTP/HTTPS", detail)

    async def test_handler_always_yields_final_result_after_progress(self) -> None:
        payload = {"headlessClients": [{"profileId": "profile-a1"}]}
        session = _Session(_Response(200, payload), _Response(200))
        controller = main.HeadlessController(None, _config(cooldown_seconds=0))
        controller._session = session
        event = _Event("A1卡了", group_id="1", sender_id="2")

        results = [result async for result in controller.on_message(event)]

        self.assertEqual(len(event.sent), 1)
        self.assertIn("正在确认 A1", event.sent[0])
        self.assertEqual(len(results), 1)
        self.assertIn("重启请求已成功提交", results[0])


class RepositoryConsistencyTests(unittest.TestCase):
    def test_schema_has_every_runtime_option(self) -> None:
        root = Path(__file__).resolve().parents[1]
        schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8"))
        expected = {
            "fika_server_url",
            "fika_api_key",
            "verify_headless_online",
            "verify_ssl",
            "trigger_suffix",
            "allowed_group_ids",
            "allowed_sender_ids",
            "allow_private_messages",
            "request_timeout_seconds",
            "cooldown_seconds",
            "nodes",
        }

        self.assertEqual(set(schema), expected)
        self.assertEqual(schema["trigger_suffix"]["default"], "卡了")
        self.assertIs(schema["verify_ssl"]["default"], False)
        self.assertEqual(schema["request_timeout_seconds"]["default"], 10)
        self.assertEqual(schema["cooldown_seconds"]["default"], 60)


if __name__ == "__main__":
    unittest.main()
