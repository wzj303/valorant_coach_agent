"""Web frontend: index, health, config, samples and analyze endpoints (offline-safe)."""
from __future__ import annotations

import base64
import json
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import webapp.server as webapp_server
from webapp.server import start_server

from tests.agent.helpers import make_png


def request(url, payload=None, timeout=30):
    if payload is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


@pytest.fixture(scope="module")
def base_url():
    """Server running in this process, with LLM credentials removed (demo mode)."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="valorant-webapp-test-"))
    mp = pytest.MonkeyPatch()
    mp.delenv("VALORANT_LLM_API_KEY", raising=False)
    mp.delenv("VALORANT_LLM_MODEL", raising=False)
    mp.setattr(webapp_server, "ENV_FILE", tmp_dir / ".env")
    server, port, thread = start_server("127.0.0.1", 0, quiet=True)
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=10)
    mp.undo()


def test_index_served(base_url):
    with urllib.request.urlopen(base_url + "/", timeout=10) as res:
        assert res.status == 200
        assert res.headers.get_content_type().startswith("text/html")
        html = res.read().decode("utf-8")
    assert "VALORANT 教练复盘" in html
    assert "/static/app.js" in html


def test_static_assets_served(base_url):
    with urllib.request.urlopen(base_url + "/static/style.css", timeout=10) as res:
        assert res.status == 200
        assert res.headers.get_content_type().startswith("text/css")
        assert b".status-pill" in res.read()
    with urllib.request.urlopen(base_url + "/static/app.js", timeout=10) as res:
        assert res.status == 200
        assert res.headers.get_content_type().startswith("application/javascript")


def test_unknown_route_is_404(base_url):
    status, body = request(base_url + "/api/nope")
    assert status == 404


def test_health_reports_demo_mode_and_skills(base_url):
    status, body = request(base_url + "/api/health")
    assert status == 200
    assert body["mode"] == "demo"
    assert set(body["tasks"]) == {"buy_review", "position_review", "match_review"}
    names = {skill["name"] for skill in body["skills"]}
    assert names == {"buy-decision-review", "position-coordination-review", "coach-feedback"}
    assert any("127.0.0.1" in address for address in body["addresses"])


def test_analyze_buy_demo_runs_live_tools(base_url):
    status, body = request(base_url + "/api/analyze",
                           {"task_type": "buy_review", "text": "这回合该怎么买？"})
    assert status == 200
    assert body["mode"] == "demo"
    report = body["report"]
    assert report["task_status"] in {"success", "partial", "failed"}
    assert "4199" in (report["buy_review"] or "")
    tools = [ex["tool_name"] for ex in body["trace"]["tool_executions"]]
    assert "validate_round_data" in tools
    assert "analyze_buy_state" in tools
    assert body["markdown"].startswith("# 教练报告")


def test_analyze_position_demo(base_url):
    status, body = request(base_url + "/api/analyze",
                           {"task_type": "position_review", "text": "站位如何？"})
    assert status == 200
    assert "A Site" in (body["report"]["position_review"] or "")


def test_analyze_with_complete_round_snapshot(base_url):
    _, samples = request(base_url + "/api/samples")
    snap = samples["samples"]["complete_round"]
    status, body = request(base_url + "/api/analyze",
                           {"task_type": "buy_review", "text": "复盘", "snapshot": snap})
    assert status == 200
    assert "22500" in (body["report"]["buy_review"] or "")


def test_analyze_with_position_snapshot(base_url):
    _, samples = request(base_url + "/api/samples")
    snap = samples["samples"]["position_snapshot"]
    status, body = request(base_url + "/api/analyze",
                           {"task_type": "position_review", "text": "复盘", "snapshot": snap})
    assert status == 200
    assert "fixture-frame" in (body["report"]["position_review"] or "")


def test_invalid_snapshot_yields_failed_report(base_url):
    _, samples = request(base_url + "/api/samples")
    snap = samples["samples"]["invalid_round"]
    status, body = request(base_url + "/api/analyze",
                           {"task_type": "buy_review", "text": "复盘", "snapshot": snap})
    assert status == 200
    assert body["report"]["task_status"] == "failed"


def test_bad_json_body_is_400(base_url):
    req = urllib.request.Request(base_url + "/api/analyze", data=b"{not json",
                                 headers={"Content-Type": "application/json"}, method="POST")
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(req, timeout=10)
    assert excinfo.value.code == 400


def test_invalid_task_type_is_400(base_url):
    status, body = request(base_url + "/api/analyze", {"task_type": "nonsense", "text": "x"})
    assert status == 400
    assert "task_type" in body["error"]


def test_invalid_image_base64_is_400(base_url):
    status, body = request(base_url + "/api/analyze",
                           {"task_type": "buy_review", "text": "x",
                            "images": [{"name": "a.png", "data": "!!!not-base64!!!"}]})
    assert status == 400
    assert "base64" in body["error"]


def test_png_image_is_accepted(base_url):
    payload = {"task_type": "buy_review", "text": "看图复盘",
               "images": [{"name": "shot.png",
                           "data": base64.b64encode(make_png()).decode("ascii")}]}
    status, body = request(base_url + "/api/analyze", payload)
    assert status == 200
    assert body["report"]["task_status"] in {"success", "partial", "failed"}


def test_oversized_body_is_413():
    server, port, thread = start_server("127.0.0.1", 0, quiet=True, max_body_bytes=1024)
    try:
        status, body = request(f"http://127.0.0.1:{port}/api/analyze",
                               {"task_type": "buy_review", "text": "x" * 4096})
        assert status == 413
        assert "limit" in body["error"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def test_real_mode_attempts_the_provider(monkeypatch):
    monkeypatch.setenv("VALORANT_LLM_API_KEY", "test-key")
    monkeypatch.setenv("VALORANT_LLM_MODEL", "test-model")
    monkeypatch.setenv("VALORANT_LLM_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("VALORANT_LLM_MAX_RETRIES", "0")
    server, port, thread = start_server("127.0.0.1", 0, quiet=True)
    try:
        status, body = request(f"http://127.0.0.1:{port}/api/analyze",
                               {"task_type": "buy_review", "text": "x"}, timeout=30)
        # The provider is unreachable, so the agent reports a clean server error.
        assert status == 500
        assert body["error"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def _own_server(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp_server, "ENV_FILE", tmp_path / ".env")
    return start_server("127.0.0.1", 0, quiet=True)


def _stop(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=10)


def test_config_get_is_masked_and_never_returns_the_key(monkeypatch, tmp_path):
    server, port, thread = _own_server(monkeypatch, tmp_path)
    try:
        base = f"http://127.0.0.1:{port}"
        status, body = request(base + "/api/config")
        assert status == 200 and body == {"format": "openai", "base_url": "", "model": "",
                                          "has_key": False}
        status, body = request(base + "/api/config",
                               {"format": "openai", "base_url": "http://127.0.0.1:1/v1",
                                "model": "team-model", "api_key": "secret-key-123"})
        assert status == 200
        assert body["has_key"] and body["model"] == "team-model"
        assert "secret-key-123" not in json.dumps(body)
        status, body = request(base + "/api/config")
        assert body["has_key"] and "secret-key-123" not in json.dumps(body)
        # The key lives on the server disk only.
        assert "secret-key-123" in (tmp_path / ".env").read_text(encoding="utf-8")
        status, health = request(base + "/api/health")
        assert health["mode"] == "real"
    finally:
        _stop(server, thread)


def test_config_rejects_invalid_values(monkeypatch, tmp_path):
    server, port, thread = _own_server(monkeypatch, tmp_path)
    try:
        base = f"http://127.0.0.1:{port}"
        status, body = request(base + "/api/config", {"format": "gopher", "model": "m",
                                                      "api_key": "k"})
        assert status == 400 and "format" in body["error"]
        status, body = request(base + "/api/config", {"format": "openai", "model": "",
                                                      "api_key": ""})
        assert status == 400 and "api_key" in body["error"]
    finally:
        _stop(server, thread)


def test_config_probe_reports_failures_instead_of_crashing(monkeypatch, tmp_path):
    server, port, thread = _own_server(monkeypatch, tmp_path)
    try:
        base = f"http://127.0.0.1:{port}"
        status, body = request(base + "/api/config/test",
                               {"format": "openai", "base_url": "http://127.0.0.1:1/v1",
                                "model": "m", "api_key": "k"}, timeout=30)
        assert status == 200
        assert not body["all_passed"]
        assert {probe["name"] for probe in body["probes"]} == {"text", "vision", "tool_calling"}
        assert all(not probe["passed"] for probe in body["probes"])
    finally:
        _stop(server, thread)


def test_analyze_ephemeral_config_requires_key_and_model(base_url):
    status, body = request(base_url + "/api/analyze",
                           {"task_type": "buy_review", "text": "x",
                            "config": {"format": "openai", "model": "", "api_key": ""}})
    assert status == 400
    assert "api_key" in body["error"]


def test_analyze_ephemeral_config_routes_to_the_provider(monkeypatch, tmp_path):
    server, port, thread = _own_server(monkeypatch, tmp_path)
    try:
        base = f"http://127.0.0.1:{port}"
        status, body = request(base + "/api/analyze",
                               {"task_type": "buy_review", "text": "x",
                                "config": {"format": "openai", "base_url": "http://127.0.0.1:1/v1",
                                           "model": "m", "api_key": "k"}}, timeout=30)
        assert status == 500  # unreachable provider, clean error
        assert body["error"]
    finally:
        _stop(server, thread)
