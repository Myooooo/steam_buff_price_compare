"""Buff (buff.163.com) API 客户端。

要点（2026-07 实测）：
- 价格接口已改为必须登录，否则返回 {"code":"Login Required","error":"请先登录"}。
- 基础 Cookie（Device-Id / client_id / csrf_token）由 GET / 种下。
- POST 需带 X-CSRFToken（=当前 csrf_token cookie 值）+ Content-Type: application/json
  + Timezone-Offset-DST 头；GET 用 URL 参数 + Cookie。
- 会话由服务端 Cookie 维持，登录成功后落在本 client 的 cookie jar 里，
  整体持久化到 data/buff_session.json（原子写）。
"""
from __future__ import annotations

import asyncio
import datetime
import http.cookiejar
import logging
import os
from pathlib import Path
from typing import Any, Optional

import httpx

from ..config import DEFAULT_USER_AGENT
from ..db import now_iso

logger = logging.getLogger("buff")

BASE_URL = "https://buff.163.com"
LOGIN_REQUIRED = "Login Required"
LOGIN_COMPLETE_STATE = 2


class LoginRequiredError(Exception):
    """Buff 会话失效或未登录，需要重新扫码。"""


def is_login_complete(status: Optional[dict[str, Any]]) -> bool:
    """BUFF 当前状态 2 表示完整登录；状态 1 仍需进入 Steam 绑定流程。"""
    return bool(status and status.get("state") == LOGIN_COMPLETE_STATE)


def _now_ms() -> str:
    """Timezone-Offset-DST 头：本地时区相对 UTC 的毫秒偏移（如 UTC+8 => "28800000"）。"""
    off = datetime.datetime.now().astimezone().utcoffset()
    return str(int(off.total_seconds() * 1000)) if off else "0"


def _headers(client: httpx.AsyncClient, *, csrf: bool = False, json_body: bool = False) -> dict[str, str]:
    h = {
        "User-Agent": client.headers.get("User-Agent", DEFAULT_USER_AGENT),
        "Timezone-Offset-DST": _now_ms(),
    }
    if json_body:
        h["Content-Type"] = "application/json"
    if csrf:
        token = client.cookies.get("csrf_token")  # 现读，不缓存
        if token:
            h["X-CSRFToken"] = token
    return h


# ---------- cookie jar 持久化 ----------

def save_session(client: httpx.AsyncClient, path: Path) -> None:
    """把整个 cookie jar 原子写入文件（含 HttpOnly / 过期时间 / 域名作用域）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mj = http.cookiejar.MozillaCookieJar()
    for c in client.cookies.jar:
        mj.set_cookie(c)
    tmp = path.with_suffix(".tmp")
    mj.save(str(tmp), ignore_discard=True, ignore_expires=True)
    os.replace(tmp, path)


def load_session(path: Path) -> httpx.Cookies:
    """从文件恢复 cookie jar；不存在或损坏返回空 jar。"""
    path = Path(path)
    if not path.exists():
        return httpx.Cookies()
    mj = http.cookiejar.MozillaCookieJar(str(path))
    try:
        mj.load(ignore_discard=True, ignore_expires=True)
    except (OSError, http.cookiejar.LoadError, ValueError):
        logger.warning("会话文件损坏，忽略: %s", path)
        return httpx.Cookies()
    # httpx.Cookies(mj) 直接复用该 CookieJar（其子类），客户端收发的 Cookie 都会进这个 jar
    return httpx.Cookies(mj)


# ---------- 客户端 ----------

class BuffClient:
    def __init__(self, session_path: Path, *, user_agent: str = DEFAULT_USER_AGENT):
        self.session_path = Path(session_path)
        self.user_agent = user_agent
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("BuffClient 未启动（先调用 start()）")
        return self._client

    async def start(self) -> None:
        jar = load_session(self.session_path)
        self._client = httpx.AsyncClient(
            cookies=jar,
            timeout=15.0,
            follow_redirects=True,
            headers={"User-Agent": self.user_agent},
        )

    async def aclose(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def persist(self) -> None:
        save_session(self.client, self.session_path)

    def cookie_value(self, name: str) -> Optional[str]:
        """读取当前 BUFF Cookie，避免调用方穿透 ``AsyncClient`` 封装。"""
        return self.client.cookies.get(name)

    async def ensure_base_cookies(self) -> None:
        """访问首页，种下 Device-Id / client_id / csrf_token 基础 Cookie。"""
        await self._request("GET", "/", raw=True)

    async def refresh_base_cookies(self) -> None:
        """丢弃失配的 client/CSRF Cookie 后重新种值，保留稳定的设备标识。"""
        refresh_names = {"client_id", "csrf_token"}
        jar = self.client.cookies.jar
        for cookie in list(jar):
            if cookie.name in refresh_names:
                jar.clear(cookie.domain, cookie.path, cookie.name)
        await self.ensure_base_cookies()

    async def login_status(self) -> Optional[dict[str, Any]]:
        """GET /account/api/login/status；完整登录时 data.state==2。"""
        try:
            data = await self._request("GET", "/account/api/login/status")
            return data.get("data") if isinstance(data, dict) else None
        except LoginRequiredError:
            return {"state": 0, "user": {}}

    async def logout(self) -> None:
        """清除登录会话 Cookie，保留基础 Cookie（Device-Id/client_id/csrf_token），
        并重新种基础 Cookie 后持久化。"""
        base = {"Device-Id", "client_id", "csrf_token"}
        jar = self.client.cookies.jar
        for c in list(jar):
            if c.name not in base:
                jar.clear(c.domain, c.path, c.name)
        await self.ensure_base_cookies()
        self.persist()

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
        *,
        raw: bool = False,
        max_retries: int = 3,
    ) -> Any:
        """统一请求入口：注入头、处理 429 退避、识别 Login Required。

        返回解析后的 JSON（raw=True 时返回原始 Response）。
        """
        url = path if path.startswith("http") else BASE_URL + path
        csrf = method.upper() in ("POST", "PUT", "DELETE", "PATCH")
        retry_after = 0
        for attempt in range(max_retries):
            try:
                resp = await self.client.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=_headers(self.client, csrf=csrf, json_body=csrf),
                )
            except httpx.HTTPError as e:
                logger.debug("Buff 请求异常 %s %s: %s", method, path, e)
                await asyncio.sleep(2 ** attempt)
                continue
            if resp.status_code == 429:
                retry_after = _retry_after(resp)
                await asyncio.sleep(max(2 ** (attempt + 1), retry_after))
                continue
            if resp.status_code >= 500:
                await asyncio.sleep(2 ** (attempt + 1))
                continue
            if resp.status_code == 403:
                # 可能被风控/需要验证码；重试一次后放弃
                if attempt < 1:
                    await asyncio.sleep(3)
                    continue
                logger.warning("Buff 403: %s %s -> %s", method, path, resp.text[:200])
                raise httpx.HTTPStatusError(str(resp.status_code), request=resp.request, response=resp)
            if resp.status_code >= 400:
                raise httpx.HTTPStatusError(str(resp.status_code), request=resp.request, response=resp)
            if raw:
                return resp
            try:
                payload = resp.json()
            except ValueError:
                logger.warning("Buff 响应非 JSON: %s %s -> %s", method, path, resp.text[:200])
                raise httpx.HTTPStatusError("non-json", request=resp.request, response=resp)
            if isinstance(payload, dict):
                code = payload.get("code")
                if code == LOGIN_REQUIRED:
                    raise LoginRequiredError(payload.get("error") or "请先登录")
                if code and code != "OK":
                    # 其他业务错误（如参数错误），打日志但不重试
                    logger.warning("Buff 业务错误 %s %s -> %s", method, path, payload.get("error"))
            return payload
        raise httpx.HTTPError(f"Buff 请求失败（重试耗尽）: {method} {path}")

    # ---------- 业务接口 ----------

    async def search_goods(
        self,
        keyword: str,
        game: str = "csgo",
        page_num: int = 1,
        page_size: int = 20,
        sort_by: str = "price.asc",
    ) -> list[dict[str, Any]]:
        """按关键词搜索饰品，返回本页 item 列表。"""
        params = {
            "game": game,
            "page_num": page_num,
            "page_size": page_size,
            "search": keyword,
            "sort_by": sort_by,
        }
        payload = await self._request("GET", "/api/market/goods", params=params)
        data = payload.get("data") or {}
        return data.get("items") or []

    async def browse_market(
        self,
        min_price: float,
        max_price: float,
        game: str = "csgo",
        page_num: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """按价格区间浏览市场饰品（深度扫描用）。"""
        params = {
            "game": game,
            "page_num": page_num,
            "page_size": page_size,
            "min_price": min_price,
            "max_price": max_price,
            "sort_by": "price.asc",
        }
        payload = await self._request("GET", "/api/market/goods", params=params)
        data = payload.get("data") or {}
        return {
            "items": data.get("items") or [],
            "total_page": int(data.get("total_page") or 1),
        }


def _retry_after(resp: httpx.Response) -> int:
    try:
        return int(resp.headers.get("Retry-After", "0") or 0)
    except ValueError:
        return 0


# ---------- 数据标准化 ----------

def to_float(v: Any) -> Optional[float]:
    """把 Buff 返回的价格字符串/数字/null 归一化为 float。"""
    if v is None or v == "" or v == "0.0":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def normalize_item(raw: dict[str, Any], game: str = "csgo", source: str = "keyword") -> dict[str, Any]:
    """把 Buff /api/market/goods 返回的 item 归一化为我们的统一结构。"""
    sell_min = to_float(raw.get("sell_min_price"))
    buy_max = to_float(raw.get("buy_max_price"))
    return {
        "market_hash_name": raw.get("market_hash_name") or raw.get("name") or raw.get("id"),
        "game": game,
        "buff_price": sell_min,
        "buff_sell_num": int(raw.get("sell_num") or 0),
        "buff_buy_num": int(raw.get("buy_num") or 0),
        "buff_buy_max_price": buy_max,
        "source": source,
        "updated_at": now_iso(),
    }
