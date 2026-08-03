#!/usr/bin/env python3
"""Steam × Buff 倒余额折价对比 Web 应用 — 启动入口。

用法:
    python run.py [--no-browser] [--port 8080]
"""
import argparse
import logging
import threading
import time
import webbrowser

import uvicorn


def _open_browser(url: str, delay: float = 1.2) -> None:
    """延迟打开浏览器，等 uvicorn 真正开始监听后再访问。"""

    def _do():
        time.sleep(delay)
        webbrowser.open(url)

    threading.Thread(target=_do, daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser(description="Steam × Buff 倒余额折价对比")
    parser.add_argument("--host", default=None, help="监听地址（默认取 data/config.json 或 127.0.0.1）")
    parser.add_argument("--port", type=int, default=None, help="监听端口（默认取 data/config.json 或 8080）")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    parser.add_argument("--reload", action="store_true", help="开发模式：代码变更自动重载")
    args = parser.parse_args()

    # 尽量先读配置里的 host/port（配置在 backend/config 首次加载时生成）
    host, port = args.host, args.port
    try:
        from backend.config import load_config
        cfg = load_config()
        if host is None:
            host = cfg.host
        if port is None:
            port = cfg.port
    except Exception:  # pragma: no cover - 配置损坏时用默认值
        logging.getLogger("run").warning("无法读取配置，使用默认 host/port", exc_info=True)
    host = host or "127.0.0.1"
    port = port or 8080

    url = f"http://{host}:{port}"
    if not args.no_browser:
        _open_browser(url)

    # 关键：单 worker。多 worker 会各自带一套内存状态（登录会话/扫描任务），
    # 且会互相重复扫描同一批饰品，违反 Buff 的限流要求。
    uvicorn.run(
        "backend.main:app",
        host=host,
        port=port,
        workers=1,
        reload=args.reload,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
