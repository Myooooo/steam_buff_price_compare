"""配置加载、持久化和调度开关测试。"""
import asyncio
import json
from unittest import mock

import pytest
from pydantic import ValidationError

from backend.config import Config, load_config, save_config
from backend.models import ConfigIn
from backend.routers.config import update_config
from backend.state import app_state


def test_load_config_ignores_unknown_fields(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
                {
                    "keywords": ["ak-47"],
                    "deep_scan": {"enabled": True, "max_pages": 3, "removed_field": "ignored"},
                "removed_field": "ignored",
            }
        ),
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.keywords == ["ak-47"]
    assert config.deep_scan.enabled is True
    assert not hasattr(config.deep_scan, "max_pages")
    assert not hasattr(config, "removed_field")


def test_save_config_round_trip(tmp_path):
    path = tmp_path / "config.json"
    original = Config(keywords=["usp"], deep_scan={"enabled": True, "interval_minutes": 120})

    save_config(original, path)
    restored = load_config(path)

    assert restored == original
    assert not path.with_suffix(".json.tmp").exists()


def test_config_input_validation():
    with pytest.raises(ValidationError):
        ConfigIn(port=70000)
    with pytest.raises(ValidationError):
        ConfigIn(keywords=[])
    with pytest.raises(ValidationError):
        ConfigIn(deep_scan={"interval_minutes": 0})


def test_disabling_auto_scan_stops_scheduler():
    class FakeScheduler:
        def __init__(self):
            self.stopped = False

        async def stop(self):
            self.stopped = True

    async def scenario():
        previous_config = app_state.config
        previous_scheduler = app_state.scheduler
        scheduler = FakeScheduler()
        app_state.config = Config(auto_scan=True)
        app_state.scheduler = scheduler
        try:
            with (
                mock.patch("backend.routers.config.save_config"),
                mock.patch.object(app_state, "broadcast", new=mock.AsyncMock()),
            ):
                await update_config(ConfigIn(auto_scan=False))
            assert scheduler.stopped is True
        finally:
            app_state.config = previous_config
            app_state.scheduler = previous_scheduler

    asyncio.run(scenario())
