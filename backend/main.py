"""FastAPI 应用：lifespan 装配 + 路由 + 静态前端挂载。"""
from __future__ import annotations

import base64
import logging
import sqlite3
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import db
from .config import FRONTEND_DIR, load_config
from .routers import auth, config as config_router, items, scan, ws
from .services.buff import BuffClient
from .services.buff_login import QRLogin
from .services.scheduler import Scheduler
from .services.scanner import Scanner
from .services.steam import create_steam_session
from .state import app_state

logger = logging.getLogger("main")


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # ---------- 启动装配 ----------
    cfg = load_config()
    setup_logging(cfg.log_level)
    app_state.config = cfg

    app_state.db_conn = sqlite3.connect(cfg.db_path, check_same_thread=False)
    app_state.db_conn.execute("PRAGMA journal_mode=WAL")
    db.init_db(app_state.db_conn)

    # Buff 客户端（恢复持久化会话）
    buff = BuffClient(cfg.session_path, user_agent=cfg.user_agent)
    await buff.start()
    await buff.ensure_base_cookies()
    app_state.buff = buff

    # Steam 市场搜索会话（稳定浏览器请求特征）
    app_state.steam_session = create_steam_session()

    # 扫码登录 + 状态回调
    qr = QRLogin(buff)
    app_state.qr = qr

    async def on_login_state(state: str, _user: dict | None) -> None:
        payload: dict = {"type": "qr", **qr.status()}
        img = await qr.qr_image()
        if img:
            payload["qr_image"] = "data:image/png;base64," + base64.b64encode(img).decode()
        await app_state.broadcast(payload)
        if state == "confirmed":
            await app_state.refresh_login()
            if app_state.config.auto_scan and app_state.scanner:
                await app_state.scanner.request_scan("keyword")
            await app_state.broadcast(app_state.snapshot())

    qr.set_callback(on_login_state)

    # 扫描器
    async def on_scan_update(event: str, _payload: dict) -> None:
        if event == "login_required":
            await app_state.refresh_login()
        if event == "item_updated":
            await app_state.broadcast({"type": "item_updated", **_payload})
        await app_state.broadcast(app_state.snapshot())

    scanner = Scanner(
        buff=buff,
        steam_session=app_state.steam_session,
        db_conn=app_state.db_conn,
        db_lock=app_state.db_lock,
        get_config=lambda: app_state.config,
        on_update=on_scan_update,
    )
    app_state.scanner = scanner

    # 调度器（间隔从配置实时读取）
    def get_intervals() -> tuple[int, int | None]:
        c = app_state.config
        deep = c.deep_scan.interval_minutes if c.deep_scan.enabled else None
        return c.scan_interval_minutes, deep

    scheduler = Scheduler(lambda mode: scanner.request_scan(mode), get_intervals)
    app_state.scheduler = scheduler
    if cfg.auto_scan:
        scheduler.start()

    # 初始登录态探测（恢复持久化会话）
    await app_state.refresh_login()
    if app_state.buff_logged_in:
        logger.info("Buff 会话已恢复: %s", (app_state.login_user or {}).get("nickname", "?"))
    else:
        logger.info("Buff 未登录，请在页面扫码登录")

    yield

    # ---------- 关闭清理 ----------
    await scheduler.stop()
    await scanner.stop()
    await qr.cancel()
    if app_state.steam_session is not None:
        try:
            await app_state.steam_session.close()
        except Exception:  # noqa: BLE001
            pass
    await buff.aclose()
    if app_state.db_conn is not None:
        app_state.db_conn.close()


app = FastAPI(title="Steam × Buff 倒余额折价对比", lifespan=lifespan)


@app.get("/api/status")
async def api_status() -> dict:
    """聚合状态快照（商品排名由分页接口单独获取）。"""
    await app_state.refresh_login()
    return app_state.snapshot()


# 业务路由（先注册，保证 /api/* 优先于静态挂载匹配）
app.include_router(auth.router)
app.include_router(items.router)
app.include_router(scan.router)
app.include_router(config_router.router)
app.include_router(ws.router)

# 静态前端（最后挂载，兜底所有未匹配路径）
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
