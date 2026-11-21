"""HTTP integration for durable multi-round sessions."""
import base64
import json

import pytest

import webapp.server as module
from tests.agent.test_match_graph import inputs
from tests.agent.test_match_graph import MatchLlm
from tests.agent.helpers import make_png
from tests.webapp.test_server import request


@pytest.fixture
def match_url(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(module, "MATCH_DB", tmp_path / "matches.sqlite")
    monkeypatch.setattr(module, "MATCH_MEDIA", tmp_path / "images")
    server, port, thread = module.start_server("127.0.0.1", 0, quiet=True)
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=10)


def test_full_match_append_restore_and_summary(match_url):
    status, first = request(match_url + "/api/matches/analyze", {
        "match_id": "team-game", "rounds": inputs(*range(1, 14)), "force_demo": True})
    assert status == 200 and first["completed_rounds"] == 13
    assert first["report"]["task_status"] == "partial"
    status, restored = request(match_url + "/api/matches/team-game")
    assert status == 200 and restored["rounds"] == first["rounds"]
    status, appended = request(match_url + "/api/matches/analyze", {
        "match_id": "team-game", "rounds": inputs(14), "force_demo": True, "summarize": False})
    assert status == 200 and appended["completed_rounds"] == 14 and appended["report"] is None
    status, summary = request(match_url + "/api/matches/analyze", {
        "match_id": "team-game", "rounds": [], "force_demo": True})
    assert status == 200 and "14" in summary["report"]["coach_summary"]


def test_more_than_ten_screenshots_use_match_graph_without_silent_demo(match_url):
    image = {"name": "opening.png", "data": base64.b64encode(make_png()).decode()}
    status, body = request(match_url + "/api/analyze", {
        "task_type": "match_review", "images": [image] * 13, "force_demo": True})
    assert status == 400 and "视觉模型" in body["error"]
    assert "at most 10" not in body["error"]


def test_match_api_rejects_invalid_inputs_and_unknown_sessions(match_url):
    status, _ = request(match_url + "/api/matches/unknown")
    assert status == 404
    status, _ = request(match_url + "/api/matches/analyze", {"match_id": "../bad", "rounds": []})
    assert status == 400
    status, _ = request(match_url + "/api/matches/analyze", {"rounds": "not a list", "force_demo": True})
    assert status == 400
    status, _ = request(match_url + "/api/matches/unknown/resume", {"force_demo": True})
    assert status == 400


def test_ephemeral_model_key_never_enters_match_state(match_url, monkeypatch):
    monkeypatch.setattr(module, "RealLlmAdapter", lambda config: MatchLlm())
    secret = "ephemeral-test-key-never-checkpoint-this"
    status, result = request(match_url + "/api/matches/analyze", {
        "match_id": "private-config", "rounds": inputs(1),
        "config": {"format": "openai", "model": "scripted", "api_key": secret}})
    assert status == 200 and result["mode"] == "real"
    assert secret not in json.dumps(result)
    assert secret.encode() not in module.MATCH_DB.read_bytes()
    assert not module.ENV_FILE.exists()


def test_cli_full_match_snapshot_manifest(tmp_path, capsys):
    from agent.__main__ import main

    manifest = tmp_path / "rounds.json"
    manifest.write_text(json.dumps([value["snapshot"] for value in inputs(*range(1, 14))]), encoding="utf-8")
    assert main(["--task", "match_review", "--demo", "--rounds", str(manifest),
                 "--match-id", "cli-match", "--state-db", str(tmp_path / "cli.sqlite"), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["completed_rounds"] == 13 and result["match_id"] == "cli-match"
