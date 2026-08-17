import asyncio
import io
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from api_analytics.fastapi import Analytics, Config
from starlette.requests import Request
from starlette.responses import Response

from app.main import _LOAD_LOCK_KEY, _LOAD_READY_KEY, _FilteredAnalytics, _load_items
from app.store import nano_store, store

XML_TEXT = """<?xml version="1.0"?>
<aodb>
  <item aoid="21601" patch="110000" metatype="i">
    <name>Flamethrower Ammunition</name>
    <ql>1</ql>
    <icon>32168</icon>
  </item>
  <item aoid="25980" patch="110000" metatype="n">
    <name>Death's Gaze</name>
    <description>Attempts to hold the target in place.</description>
    <ql>142</ql>
    <icon>16248</icon>
    <nanodata crystalid="26017" nanocost="265" ncu="44" />
    <nanoclass school="Combat" strain="147" />
  </item>
</aodb>
"""


def _write_dump_zip(path) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("dump.xml", XML_TEXT)
    path.write_bytes(buf.getvalue())
    return str(path)


async def test_load_items_noop_when_no_dump_source_configured(fake_redis):
    await _load_items()

    assert await store.count("", 0) == 0
    assert await fake_redis.get(_LOAD_LOCK_KEY) is None
    assert await fake_redis.get(_LOAD_READY_KEY) is None


async def test_load_items_skips_when_already_loaded(fake_redis, monkeypatch, tmp_path):
    dump_path = _write_dump_zip(tmp_path / "dump.zip")
    monkeypatch.setenv("DUMP_PATH", dump_path)
    await fake_redis.set(_LOAD_READY_KEY, dump_path)

    await _load_items()

    # Ready key already matched the version, so load() was never called -
    # store stays empty even though a valid dump was configured.
    assert await store.count("", 0) == 0
    assert await fake_redis.get(_LOAD_LOCK_KEY) is None


async def test_load_items_waits_for_other_pod_and_succeeds(fake_redis, monkeypatch, tmp_path):
    dump_path = _write_dump_zip(tmp_path / "dump.zip")
    monkeypatch.setenv("DUMP_PATH", dump_path)
    # Simulate another pod already holding the lock.
    await fake_redis.set(_LOAD_LOCK_KEY, "1")

    async def set_ready_shortly_after():
        await asyncio.sleep(0.6)
        await fake_redis.set(_LOAD_READY_KEY, dump_path)

    setter = asyncio.create_task(set_ready_shortly_after())
    await _load_items()
    await setter

    # This pod only waited for the other one - it never loaded anything itself.
    assert await store.count("", 0) == 0


async def test_load_items_times_out_waiting_for_other_pod(fake_redis, monkeypatch, tmp_path):
    dump_path = _write_dump_zip(tmp_path / "dump.zip")
    monkeypatch.setenv("DUMP_PATH", dump_path)
    monkeypatch.setattr("app.main._LOAD_WAIT_TIMEOUT_SECONDS", 0.5)
    await fake_redis.set(_LOAD_LOCK_KEY, "1")  # never released, ready key never set

    await _load_items()  # must return (not hang) once the timeout elapses

    assert await store.count("", 0) == 0


async def test_load_items_race_double_check_returns_without_reloading(fake_redis, monkeypatch, tmp_path):
    """Covers the "someone else finished between our GET and our SET NX"
    branch: the ready key becomes set in between the two checks."""
    dump_path = _write_dump_zip(tmp_path / "dump.zip")
    monkeypatch.setenv("DUMP_PATH", dump_path)

    real_get = fake_redis.get
    call_count = {"n": 0}

    async def counting_get(key):
        if key == _LOAD_READY_KEY:
            call_count["n"] += 1
            if call_count["n"] >= 2:
                return dump_path
        return await real_get(key)

    monkeypatch.setattr(fake_redis, "get", counting_get)

    await _load_items()

    assert await store.count("", 0) == 0
    assert await real_get(_LOAD_LOCK_KEY) is None  # released in the finally block


async def test_load_items_happy_path_loads_from_dump_path(fake_redis, monkeypatch, tmp_path):
    dump_path = _write_dump_zip(tmp_path / "dump.zip")
    monkeypatch.setenv("DUMP_PATH", dump_path)

    await _load_items()

    assert await store.count("", 0) == 2
    assert await nano_store.count("", 0, "", None) == 1
    assert await fake_redis.get(_LOAD_READY_KEY) == dump_path
    assert await fake_redis.get(_LOAD_LOCK_KEY) is None


async def test_load_items_happy_path_loads_from_dump_url(fake_redis, monkeypatch):
    dump_url = "https://example.invalid/171003.xml.zip"
    monkeypatch.setenv("DUMP_URL", dump_url)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("dump.xml", XML_TEXT)
    mock_resp = MagicMock()
    mock_resp.read.return_value = buf.getvalue()
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        await _load_items()

    assert await store.count("", 0) == 2
    assert await nano_store.count("", 0, "", None) == 1
    assert await fake_redis.get(_LOAD_READY_KEY) == dump_url


async def test_load_items_releases_lock_on_exception(fake_redis, monkeypatch, tmp_path):
    dump_path = _write_dump_zip(tmp_path / "dump.zip")
    monkeypatch.setenv("DUMP_PATH", dump_path)

    async def boom(_items):
        raise RuntimeError("boom")

    monkeypatch.setattr(store, "load", boom)

    with pytest.raises(RuntimeError, match="boom"):
        await _load_items()

    # The lock must still be released even though loading blew up.
    assert await fake_redis.get(_LOAD_LOCK_KEY) is None
    assert await fake_redis.get(_LOAD_READY_KEY) is None


def _make_request(path: str) -> Request:
    return Request({"type": "http", "path": path, "headers": [], "query_string": b""})


@pytest.mark.parametrize("path", ["/healthz", "/robots.txt", "/.well-known/api-catalog", "/static/style.css"])
async def test_filtered_analytics_skips_logging_for_excluded_paths(path, monkeypatch):
    logging_dispatch = AsyncMock(side_effect=AssertionError("should not log excluded paths"))
    monkeypatch.setattr(Analytics, "dispatch", logging_dispatch)

    middleware = _FilteredAnalytics(app=AsyncMock(), api_key="test-key", config=Config())
    call_next = AsyncMock(return_value=Response("ok"))

    response = await middleware.dispatch(_make_request(path), call_next)

    call_next.assert_awaited_once()
    logging_dispatch.assert_not_awaited()
    assert response.status_code == 200


async def test_filtered_analytics_logs_non_excluded_paths(monkeypatch):
    logging_dispatch = AsyncMock(return_value=Response("ok"))
    monkeypatch.setattr(Analytics, "dispatch", logging_dispatch)

    middleware = _FilteredAnalytics(app=AsyncMock(), api_key="test-key", config=Config())
    call_next = AsyncMock(return_value=Response("ok"))
    request = _make_request("/api/items")

    response = await middleware.dispatch(request, call_next)

    # AsyncMock replaces Analytics.dispatch as a plain (non-descriptor)
    # attribute, so super().dispatch(...) calls it unbound - no `self` arg.
    logging_dispatch.assert_awaited_once_with(request, call_next)
    assert response.status_code == 200
