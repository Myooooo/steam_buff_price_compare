"""Buff 扫码登录：创建二维码 → 后台轮询状态 → 扫码确认后校验并持久化会话。

状态机：
    idle --start--> wait_scan --被扫码--> wait_confirm --确认--> confirmed
      ^                 |                                   |
      |---cancel/expired-+--error/TIMEOUT(轮换5次耗尽)---------+

TIMEOUT 自动轮换新二维码（上限 5 次）；CONFIRMED 后不轻信轮询结果，
再用 /account/api/login/status 二次确认完整登录态才落盘会话并回调通知。
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import secrets
from typing import Any, Awaitable, Callable, Optional

import qrcode

from .buff import BuffClient, LoginRequiredError, is_login_complete

logger = logging.getLogger("buff_login")

# Buff QR 轮询状态
QR_INIT, QR_WAIT_SCAN, QR_WAIT_CONFIRM, QR_CONFIRMED, QR_ERROR, QR_TIMEOUT = range(6)

StateCallback = Callable[[str, Optional[dict]], Awaitable[None]]


class QRLogin:
    def __init__(
        self,
        client: BuffClient,
        poll_interval_sec: float = 1.5,
        max_rotations: int = 5,
    ):
        self.client = client
        self.poll_interval_sec = poll_interval_sec
        self.max_rotations = max_rotations

        self.state = "idle"  # idle|wait_scan|wait_confirm|confirmed|error|expired
        self.code_id: Optional[str] = None
        self.qr_url: Optional[str] = None
        self.user: Optional[dict] = None
        self.error: Optional[str] = None

        self._task: Optional[asyncio.Task] = None
        self._rotations = 0
        self._cb: Optional[StateCallback] = None
        self._lock = asyncio.Lock()
        self._fingerprint: Optional[str] = None

    # ---------- 对外 ----------

    def set_callback(self, cb: StateCallback) -> None:
        self._cb = cb

    async def start(self) -> dict:
        """创建二维码并启动后台轮询；返回 {code_id, qr_url}。"""
        async with self._lock:
            await self._cancel_locked()
            info = await self._create_qr_locked()
            self.state = "wait_scan"
            self._rotations = 0
            self._task = asyncio.create_task(self._poll_loop())
            await self._notify()
            return info

    async def cancel(self) -> None:
        async with self._lock:
            await self._cancel_locked()

    async def qr_image(self) -> Optional[bytes]:
        """当前二维码 URL 渲染成 PNG bytes；无活跃二维码返回 None。"""
        if not self.qr_url:
            return None
        qr = qrcode.QRCode(border=2, box_size=8)
        qr.add_data(self.qr_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#111111", back_color="#ffffff")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def status(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "code_id": self.code_id,
            "qr_url": self.qr_url,
            "user": self.user,
            "error": self.error,
        }

    # ---------- 内部 ----------

    async def _create_qr_locked(self) -> dict:
        return await self._create_qr_attempt()

    async def _create_qr_attempt(self, _retried: bool = False) -> dict:
        payload = await self.client._request(
            "POST",
            "/account/api/qr_code_create",
            json_body={"code_type": 1, "extra_param": "{}"},
        )
        data = payload.get("data") or {}
        code_id = data.get("code_id")
        qr_url = data.get("url")
        if code_id and qr_url:
            self.code_id = code_id
            self.qr_url = qr_url
            return {"code_id": code_id, "qr_url": qr_url}
        # 登录绑定会轮换 client/CSRF；只对明确的 CSRF 失配重种 Cookie 并重试。
        if not _retried and payload.get("code") == "CSRF Verification Error":
            logger.info("创建二维码失败（%s），重载基础 Cookie 后重试", payload)
            await self.client.refresh_base_cookies()
            return await self._create_qr_attempt(_retried=True)
        raise RuntimeError(f"创建二维码失败: {payload}")

    async def _cancel_locked(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        self.state = "idle"
        self.code_id = None
        self.qr_url = None
        self.user = None
        self.error = None

    async def _notify(self) -> None:
        if self._cb:
            try:
                await self._cb(self.state, self.user)
            except Exception:  # noqa: BLE001
                logger.exception("登录状态回调失败")

    async def _poll_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.poll_interval_sec)
                result = await self._poll_once()
                if result is not None:  # 终结状态
                    return
        except asyncio.CancelledError:
            raise
        except LoginRequiredError as e:
            logger.warning("扫码轮询登录态异常: %s", e)
            async with self._lock:
                self.state = "error"
                self.error = str(e)
                await self._notify()
        except Exception as e:  # noqa: BLE001
            logger.exception("扫码轮询出错")
            async with self._lock:
                self.state = "error"
                self.error = str(e)
                await self._notify()

    async def _poll_once(self) -> Optional[str]:
        """执行一次轮询。返回终结状态字符串（confirmed/error/expired）或 None 继续。"""
        async with self._lock:
            if self.state not in ("wait_scan", "wait_confirm"):
                return None
            payload = await self.client._request(
                "GET",
                "/account/api/qr_code_poll",
                params={"item_id": self.code_id},
            )
            data = payload.get("data") or {}
            st = data.get("state", QR_INIT)

            if st == QR_WAIT_SCAN:
                self.state = "wait_scan"
                await self._notify()
            elif st == QR_WAIT_CONFIRM:
                self.state = "wait_confirm"
                await self._notify()
            elif st == QR_CONFIRMED:
                return await self._handle_confirmed_locked()
            elif st == QR_ERROR:
                self.state = "error"
                self.error = "二维码校验失败，请重新扫码"
                await self._notify()
                return "error"
            elif st == QR_TIMEOUT:
                return await self._handle_timeout_locked()
            else:
                self.state = "wait_scan"
            return None

    async def _handle_timeout_locked(self) -> Optional[str]:
        """二维码过期：自动轮换新码；超过上限则置为 expired 要求重新发起。"""
        self._rotations += 1
        if self._rotations > self.max_rotations:
            self.state = "expired"
            self.error = "二维码多次过期，请点击重新登录"
            await self._notify()
            return "expired"
        await self._create_qr_locked()
        self.state = "wait_scan"
        logger.info("二维码过期，自动轮换（%d/%d）", self._rotations, self.max_rotations)
        await self._notify()
        return None

    async def _handle_confirmed_locked(self) -> str:
        """轮询确认后：POST qr_code_login 正式绑定会话，再二次确认并落盘。

        关键：Buff 前端在轮询到 CONFIRMED 后还会 POST /account/api/qr_code_login
        {item_id, web_device_id}，服务端才真正下发登录会话 Cookie；随后前端
        window.location.reload()（重新 GET /）提交会话并重置基础 Cookie。
        """
        bound_user = await self._bind_login()
        if bound_user is None:
            return await self._fail_confirmed_locked("扫码已确认，但网页会话绑定失败，请刷新二维码重试")

        status = None
        for delay in (0, 0.25, 0.75):
            if delay:
                await asyncio.sleep(delay)
            status = await self.client.login_status()
            if is_login_complete(status):
                break
        if not is_login_complete(status):
            return await self._fail_confirmed_locked("扫码已确认，但未获取到登录会话，请刷新二维码重试")

        self.user = status.get("user") or bound_user
        self.state = "confirmed"
        self.client.persist()  # 把含 session 的 cookie jar 落盘
        logger.info("Buff 登录成功: %s", self.user.get("nickname", self.user.get("id", "?")))
        await self._notify()
        return "confirmed"

    async def _fail_confirmed_locked(self, message: str) -> str:
        """手机端已确认但 Web 会话未建立：终止本轮，避免误按超时换码。"""
        self.state = "error"
        self.error = message
        await self._notify()
        return "error"

    async def _bind_login(self) -> Optional[dict]:
        """POST /account/api/qr_code_login 绑定会话，然后模拟浏览器 reload 提交会话。

        绑定请求会轮换服务端 CSRF；随后必须重新 GET / 才会让新 CSRF 在服务端生效。
        若跳过 reload，后续任何 POST（含轮换新二维码）都会报「页面已过期/CSRF 错误」。
        """
        bound = False
        try:
            payload = await self.client._request(
                "POST",
                "/account/api/qr_code_login",
                json_body={"item_id": self.code_id, "web_device_id": self._web_device_id()},
            )
            code = payload.get("code") if isinstance(payload, dict) else None
            bound = code == "OK"
            user = payload.get("data") if bound and isinstance(payload.get("data"), dict) else {}
            if code != "OK":
                logger.info("qr_code_login 返回: %s", payload)
        except LoginRequiredError:
            pass
        except Exception as e:  # noqa: BLE001
            logger.warning("qr_code_login 请求异常: %s", e)
        if not bound:
            return None

        # 模拟官方前端的 window.location.reload()。
        await self.client.ensure_base_cookies()
        logger.info(
            "qr_code_login bound=True, reload 完成 (csrf=%s)",
            "present" if self.client.cookie_value("csrf_token") else "missing",
        )
        return user

    def _web_device_id(self) -> str:
        """生成与浏览器 Fingerprint2 相同格式的 32 位十六进制设备指纹。

        用稳定且持久化的 Device-Id Cookie 派生，保证同一设备多次登录值一致。
        """
        if self._fingerprint:
            return self._fingerprint
        device = self.client.cookie_value("Device-Id")
        self._fingerprint = (
            hashlib.md5(device.encode("utf-8")).hexdigest()
            if device
            else secrets.token_hex(16)
        )
        return self._fingerprint
