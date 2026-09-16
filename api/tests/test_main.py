import asyncio
import io
import zipfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from api_analytics.fastapi import Analytics, Config
from starlette.requests import Request
from starlette.responses import Response

from app.main import _LOAD_DOC_ID, _FilteredAnalytics, _load_items
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


async def _load_doc(fake_mongo):
    return await fake_mongo["load_state"].find_one({"_id": _LOAD_DOC_ID})


async def test_load_items_noop_when_no_dump_source_configured(fake_redis, fake_mongo):
    await _load_items()

    assert await store.count("", 0) == 0
    assert await _load_doc(fake_mongo) is None


async def test_load_items_skips_when_already_loaded(fake_redis, fake_mongo, monkeypatch, tmp_path):
    dump_path = _write_dump_zip(tmp_path / "dump.zip")
    monkeypatch.setenv("DUMP_PATH", dump_path)
    await fake_mongo["load_state"].update_one(
        {"_id": _LOAD_DOC_ID}, {"$set": {"ready_version": dump_path}}, upsert=True
    )

    await _load_items()

    # Ready version already matched, so load() was never called - store stays
    # empty even though a valid dump was configured.
    assert await store.count("", 0) == 0
    assert "locked_until" not in await _load_doc(fake_mongo)


async def test_load_items_waits_for_other_pod_and_succeeds(fake_redis, fake_mongo, monkeypatch, tmp_path):
    dump_path = _write_dump_zip(tmp_path / "dump.zip")
    monkeypatch.setenv("DUMP_PATH", dump_path)
    load_state = fake_mongo["load_state"]
    # Simulate another pod already holding the lock.
    far_future = datetime.now(timezone.utc) + timedelta(seconds=300)
    await load_state.update_one({"_id": _LOAD_DOC_ID}, {"$set": {"locked_until": far_future}}, upsert=True)

    async def set_ready_shortly_after():
        await asyncio.sleep(0.6)
        await load_state.update_one({"_id": _LOAD_DOC_ID}, {"$set": {"ready_version": dump_path}})

    setter = asyncio.create_task(set_ready_shortly_after())
    await _load_items()
    await setter

    # This pod only waited for the other one - it never loaded anything itself.
    assert await store.count("", 0) == 0


async def test_load_items_times_out_waiting_for_other_pod(fake_redis, fake_mongo, monkeypatch, tmp_path):
    dump_path = _write_dump_zip(tmp_path / "dump.zip")
    monkeypatch.setenv("DUMP_PATH", dump_path)
    monkeypatch.setattr("app.main._LOAD_WAIT_TIMEOUT_SECONDS", 0.5)
    far_future = datetime.now(timezone.utc) + timedelta(seconds=300)
    # Never released, ready version never set.
    await fake_mongo["load_state"].update_one(
        {"_id": _LOAD_DOC_ID}, {"$set": {"locked_until": far_future}}, upsert=True
    )

    await _load_items()  # must return (not hang) once the timeout elapses

    assert await store.count("", 0) == 0


async def test_load_items_race_double_check_returns_without_reloading(fake_redis, fake_mongo, monkeypatch, tmp_path):
    """Covers the "someone else finished between our initial ready-check and
    our own lock-acquire CAS" branch: the ready version becomes set (and the
    lock released) by another pod in between the two."""
    dump_path = _write_dump_zip(tmp_path / "dump.zip")
    monkeypatch.setenv("DUMP_PATH", dump_path)

    # db["load_state"] returns a new collection proxy on every access
    # (verified against mongomock-motor) - _load_items() calls it once and
    # reuses that reference throughout, so patching find_one on a
    # separately-obtained collection instance (like fake_mongo["load_state"]
    # directly) would silently patch the wrong object. Pinning get_mongo()
    # to always hand back this one instance is what makes the patch below
    # actually visible to the code under test.
    real_load_state = fake_mongo["load_state"]

    class _FixedDB:
        def __getitem__(self, _name):
            return real_load_state

    monkeypatch.setattr("app.main.get_mongo", lambda: _FixedDB())

    real_find_one = real_load_state.find_one
    call_count = {"n": 0}

    async def counting_find_one(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return None  # our initial ready-check: not ready yet
        return await real_find_one(*args, **kwargs)

    monkeypatch.setattr(real_load_state, "find_one", counting_find_one)
    # The other pod finishes its load (setting ready_version, releasing its
    # lock) after our initial check but before our own CAS runs.
    await real_load_state.update_one({"_id": _LOAD_DOC_ID}, {"$set": {"ready_version": dump_path}}, upsert=True)

    await _load_items()

    assert call_count["n"] >= 1  # sanity: the patched find_one was actually reached
    assert await store.count("", 0) == 0
    assert "locked_until" not in await real_find_one({"_id": _LOAD_DOC_ID})


async def test_load_items_happy_path_loads_from_dump_path(fake_redis, fake_mongo, monkeypatch, tmp_path):
    dump_path = _write_dump_zip(tmp_path / "dump.zip")
    monkeypatch.setenv("DUMP_PATH", dump_path)

    await _load_items()

    assert await store.count("", 0) == 2
    assert await nano_store.count("", 0, "", None) == 1
    doc = await _load_doc(fake_mongo)
    assert doc["ready_version"] == dump_path
    assert "locked_until" not in doc


async def test_load_items_happy_path_loads_from_dump_url(fake_redis, fake_mongo, monkeypatch):
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
    assert (await _load_doc(fake_mongo))["ready_version"] == dump_url


async def test_load_items_releases_lock_on_exception(fake_redis, fake_mongo, monkeypatch, tmp_path):
    dump_path = _write_dump_zip(tmp_path / "dump.zip")
    monkeypatch.setenv("DUMP_PATH", dump_path)

    async def boom(_items):
        raise RuntimeError("boom")

    monkeypatch.setattr(store, "insert_items", boom)

    with pytest.raises(RuntimeError, match="boom"):
        await _load_items()

    doc = await _load_doc(fake_mongo)
    assert "locked_until" not in doc
    assert "ready_version" not in doc


async def test_load_items_skips_migrations_already_recorded_for_this_version(
    fake_redis, fake_mongo, monkeypatch, tmp_path
):
    """A migration already recorded complete for the current dump version
    (e.g. from a prior run that crashed partway through a later step) must
    not be re-run - this is the whole point of tracking them individually
    instead of one all-or-nothing store.load()/nano_store.load() call."""
    dump_path = _write_dump_zip(tmp_path / "dump.zip")
    monkeypatch.setenv("DUMP_PATH", dump_path)

    async def boom(_items):
        raise AssertionError("load_items must not run - it was already recorded complete")

    monkeypatch.setattr(store, "insert_items", boom)
    await fake_mongo["migrations"].insert_one({"_id": f"{dump_path}:load_items", "version": dump_path})

    await _load_items()

    # Every other migration still ran normally.
    assert await nano_store.count("", 0, "", None) == 1
    assert (await _load_doc(fake_mongo))["ready_version"] == dump_path


async def test_load_items_reruns_migrations_for_a_new_dump_version(fake_redis, fake_mongo, monkeypatch, tmp_path):
    """A migration recorded for a *different* (older) dump version doesn't
    count as done for the current one - each dump version gets its own
    full set of migrations."""
    dump_path = _write_dump_zip(tmp_path / "dump.zip")
    monkeypatch.setenv("DUMP_PATH", dump_path)
    await fake_mongo["migrations"].insert_one({"_id": "some-older-version:load_items", "version": "some-older-version"})

    await _load_items()

    assert await store.count("", 0) == 2
    assert await nano_store.count("", 0, "", None) == 1
    assert (await _load_doc(fake_mongo))["ready_version"] == dump_path


def _make_request(path: str) -> Request:
    return Request({"type": "http", "path": path, "headers": [], "query_string": b""})


@pytest.mark.parametrize(
    "path", ["/healthz", "/robots.txt", "/.well-known/api-catalog", "/static/style.css", "/metrics"]
)
async def test_filtered_analytics_skips_logging_for_excluded_paths(path, monkeypatch):
    logging_dispatch = AsyncMock(side_effect=AssertionError("should not log excluded paths"))
    monkeypatch.setattr(Analytics, "dispatch", logging_dispatch)

    middleware = _FilteredAnalytics(app=AsyncMock(), api_key="test-key", config=Config())
    call_next = AsyncMock(return_value=Response("ok"))

    response = await middleware.dispatch(_make_request(path), call_next)

    call_next.assert_awaited_once()
    logging_dispatch.assert_not_awaited()
    assert response.status_code == 200


@pytest.mark.parametrize("path", ["/", "/items", "/nanos", "/api/items", "/healthz"])
def test_head_request_matches_get_route_with_empty_body(client, path):
    """FastAPI 0.141.1 registers @app.get/@router.get routes with
    methods={"GET"} only, so without HeadMethodMiddleware every one of these
    405s on HEAD instead of following normal HTTP semantics."""
    get_response = client.get(path)
    head_response = client.head(path)

    assert head_response.status_code == get_response.status_code
    assert head_response.content == b""


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


def test_metrics_endpoint_exposes_prometheus_text_format(client):
    resp = client.get("/metrics")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "# HELP" in resp.text


def test_metrics_endpoint_not_in_openapi_schema(client):
    spec = client.get("/api/openapi.json").json()

    assert "/metrics" not in spec["paths"]
