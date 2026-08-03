"""Buff 扫码登录和登录态测试。"""
import asyncio
import hashlib

from backend.services.buff import is_login_complete
from backend.services.buff_login import QRLogin
from backend.state import AppState


class FakeClient:
    def __init__(self, poll_states=None, login_status_result=None, bind_code="OK", create_payloads=None):
        self.poll_states = iter(poll_states or [])
        self.login_status_results = iter(login_status_result) if isinstance(login_status_result, list) else None
        self.login_status_result = login_status_result
        self.persisted = False
        self.bind_code = bind_code
        self.create_payloads = iter(create_payloads) if create_payloads else None
        self.bind_calls = []
        self.reload_count = 0
        self.refresh_count = 0
        self.cookies = {"Device-Id": "test-device-id", "csrf_token": "test-csrf"}

    async def _request(self, method, path, params=None, json_body=None):
        if path == "/":
            self.reload_count += 1
            return {"reload": True}
        if path == "/account/api/qr_code_create":
            if self.create_payloads is not None:
                return next(self.create_payloads)
            return {
                "code": "OK",
                "data": {
                    "code_id": "abc123",
                    "url": "https://buff.163.com/account/qr_code/view/abc123",
                },
            }
        if path == "/account/api/qr_code_poll":
            return {"code": "OK", "data": {"state": next(self.poll_states)}}
        if path == "/account/api/qr_code_login":
            self.bind_calls.append(json_body)
            return {"code": self.bind_code, "data": {"nickname": "绑定响应用户"}}
        raise AssertionError(f"unexpected request {path}")

    async def login_status(self):
        if self.login_status_results is not None:
            return next(self.login_status_results)
        return self.login_status_result

    async def ensure_base_cookies(self):
        self.reload_count += 1

    async def refresh_base_cookies(self):
        self.refresh_count += 1

    def cookie_value(self, name):
        return self.cookies.get(name)

    def persist(self):
        self.persisted = True


async def wait_for_terminal_state(login: QRLogin) -> None:
    for _ in range(200):
        if login.state in ("confirmed", "error", "expired"):
            return
        await asyncio.sleep(0.02)


def test_qr_login_full_confirm():
    async def scenario():
        client = FakeClient(
            poll_states=[1, 1, 2, 3],
            login_status_result={"state": 2, "user": {"nickname": "张三"}},
        )
        login = QRLogin(client, poll_interval_sec=0.01)
        states = []

        async def callback(state, user):
            states.append(state)

        login.set_callback(callback)
        info = await login.start()
        await wait_for_terminal_state(login)

        assert info["code_id"] == "abc123"
        assert login.state == "confirmed"
        assert login.user["nickname"] == "张三"
        assert client.persisted is True
        assert states[-1] == "confirmed"
        assert len(client.bind_calls) == 1
        assert client.bind_calls[0]["item_id"] == "abc123"
        assert client.bind_calls[0]["web_device_id"] == hashlib.md5(b"test-device-id").hexdigest()
        assert client.reload_count >= 1
        await login.cancel()

    asyncio.run(scenario())


def test_qr_login_timeout_rotates_then_expires():
    async def scenario():
        client = FakeClient(poll_states=[5] * 10, login_status_result={"state": 0})
        login = QRLogin(client, poll_interval_sec=0.01, max_rotations=5)
        await login.start()
        await wait_for_terminal_state(login)

        assert login.state == "expired"
        await login.cancel()

    asyncio.run(scenario())


def test_qr_login_confirmed_but_session_invalid():
    async def scenario():
        client = FakeClient(poll_states=[3], login_status_result={"state": 0})
        login = QRLogin(client, poll_interval_sec=0.01)
        await login.start()
        await wait_for_terminal_state(login)

        assert login.state == "error"
        assert "未获取到登录会话" in login.error
        assert client.persisted is False
        await login.cancel()

    asyncio.run(scenario())


def test_qr_login_retries_login_status_after_bind():
    async def scenario():
        client = FakeClient(
            poll_states=[3],
            login_status_result=[{"state": 0}, {"state": 2, "user": {"nickname": "张三"}}],
        )
        login = QRLogin(client, poll_interval_sec=0.01)
        await login.start()
        await wait_for_terminal_state(login)

        assert login.state == "confirmed"
        assert client.persisted is True
        await login.cancel()

    asyncio.run(scenario())


def test_qr_login_never_reuses_global_fallback_fingerprint():
    client = FakeClient()
    client.cookies.pop("Device-Id")
    login = QRLogin(client)

    fingerprint = login._web_device_id()

    assert len(fingerprint) == 32
    assert fingerprint != hashlib.md5(b"unknown-device").hexdigest()
    assert login._web_device_id() == fingerprint


def test_buff_login_status_enum():
    assert is_login_complete({"state": 2, "user": {"nickname": "张三"}})
    assert not is_login_complete({"state": 1, "user": {}})
    assert not is_login_complete({"state": 0, "user": {}})


def test_restored_session_is_logged_in_without_user_profile():
    class StatusClient:
        async def login_status(self):
            return {"state": 2, "user": {}}

    async def scenario():
        state = AppState()
        state.buff = StatusClient()

        assert await state.refresh_login() is None
        assert state.buff_logged_in is True

    asyncio.run(scenario())


def test_qr_create_refreshes_cookies_only_for_csrf_error():
    async def scenario():
        client = FakeClient(
            create_payloads=[
                {"code": "CSRF Verification Error", "error": "页面已过期"},
                {
                    "code": "OK",
                    "data": {
                        "code_id": "new-code",
                        "url": "https://buff.163.com/account/qr_code/view/new-code",
                    },
                },
            ]
        )
        login = QRLogin(client, poll_interval_sec=60)
        info = await login.start()

        assert info["code_id"] == "new-code"
        assert client.refresh_count == 1
        await login.cancel()

    asyncio.run(scenario())


def test_cancel_clears_user_data():
    async def scenario():
        client = FakeClient()
        login = QRLogin(client)
        login.user = {"nickname": "旧用户"}

        await login.cancel()

        assert login.user is None

    asyncio.run(scenario())
