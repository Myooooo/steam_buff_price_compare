"""Pydantic 请求/响应模型（配置更新用），其余返回 dict。"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class DeepScanIn(BaseModel):
    enabled: Optional[bool] = None
    interval_minutes: Optional[int] = Field(None, ge=1)


class ConfigIn(BaseModel):
    host: Optional[str] = None
    port: Optional[int] = Field(None, ge=1, le=65535)
    game: Optional[str] = None
    steam_appid: Optional[int] = None
    currency: Optional[int] = None
    keywords: Optional[list[str]] = Field(None, min_length=1)
    page_size: Optional[int] = Field(None, ge=1, le=200)
    deep_scan: Optional[DeepScanIn] = None
    scan_interval_minutes: Optional[int] = Field(None, ge=1)
    auto_scan: Optional[bool] = None
    steam_fee_steam_pct: Optional[float] = Field(None, ge=0, le=50)
    steam_fee_game_pct: Optional[float] = Field(None, ge=0, le=50)
    fee_min: Optional[float] = Field(None, ge=0)
    fee_round: Optional[Literal["cent", "yuan"]] = None
    request_delay_sec: Optional[float] = Field(None, ge=0)
    steam_delay_sec: Optional[float] = Field(None, ge=0)
    steam_buff_fallback: Optional[bool] = None
    steam_rate_limit_mode: Optional[Literal["wait_retry", "buff_fallback"]] = None
    history_keep_days: Optional[int] = Field(None, ge=0)
    user_agent: Optional[str] = None
    log_level: Optional[Literal["DEBUG", "INFO", "WARNING", "ERROR"]] = None


class ScanRequest(BaseModel):
    mode: Literal["keyword", "deepscan"] = "keyword"
