"""SkillLoader: parsing, validation, selection and the three shipped skills."""
from __future__ import annotations

import pytest

from agent import SkillLoader
from agent.exceptions import SkillError, SkillNotFoundError
from tools.registry import DOMAIN_TOOLS

from tests.agent.helpers import ROOT

SHIPPED = {"buy-decision-review", "position-coordination-review", "coach-feedback"}
ALL_TOOLS = {"validate_round_data", *DOMAIN_TOOLS}


def write_skill(root, name, header, body="# body\nsteps"):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\n{header}\n---\n{body}\n", encoding="utf-8")


@pytest.fixture
def shipped():
    return SkillLoader(ROOT / "skills")


def test_the_three_required_skills_exist(shipped):
    assert set(shipped.names()) == SHIPPED


def test_every_shipped_skill_only_names_real_tools(shipped):
    for name in shipped.names():
        assert set(shipped.get(name).allowed_tools) <= ALL_TOOLS, name
    shipped.validate_tools(ALL_TOOLS)


def test_validate_tools_catches_a_missing_tool(shipped):
    with pytest.raises(SkillError, match="unregistered"):
        shipped.validate_tools({"validate_round_data"})


def test_catalog_shows_only_name_and_description(shipped):
    catalog = dict(shipped.catalog())
    assert set(catalog) == SHIPPED and all(len(d) > 10 for d in catalog.values())


@pytest.mark.parametrize("task_type,expected", [
    ("buy_review", "buy-decision-review"), ("position_review", "position-coordination-review"),
    ("match_review", "coach-feedback")])
def test_select_by_task_type(shipped, task_type, expected):
    assert shipped.select(task_type).name == expected


@pytest.mark.parametrize("text,expected", [
    ("这回合该不该起枪，经济怎么样", "buy-decision-review"),
    ("看一下小地图上大家的站位是不是太分散", "position-coordination-review"),
    ("帮我做个整场的赛后总结，下场练什么", "coach-feedback"),
    ("should I eco this round? check my credits", "buy-decision-review")])
def test_select_by_keywords(shipped, text, expected):
    assert shipped.select(None, text).name == expected


@pytest.mark.parametrize("text", ["", "你好", "看看这张图"])
def test_select_returns_none_instead_of_guessing(shipped, text):
    assert shipped.select(None, text) is None


def test_unknown_task_type(shipped):
    with pytest.raises(SkillNotFoundError):
        shipped.select("nonsense")
    with pytest.raises(SkillNotFoundError):
        shipped.get("nonsense")


def test_skill_text_encodes_the_prd_rules(shipped):
    buy = shipped.get("buy-decision-review").body
    assert "validate_round_data" in buy and "analyze_buy_state" in buy and "simulate_buy_options" in buy
    assert "事后验证" in buy and "null" in buy and "credits_unclassified" in buy
    pos = shipped.get("position-coordination-review").body
    assert "控制权" in pos and "轨迹" in pos and "analyze_positions" in pos
    assert "三个改进方向" in shipped.get("coach-feedback").body or "最重要的三个改进方向" in shipped.get("coach-feedback").body


def test_render_wraps_body(shipped):
    s = shipped.get("coach-feedback")
    assert shipped.render(s).startswith("# Active skill: coach-feedback") and s.body in shipped.render(s)


def test_tool_names_empty_means_unrestricted(tmp_path):
    write_skill(tmp_path, "open-skill", "name: open-skill\ndescription: d")
    assert SkillLoader(tmp_path).get("open-skill").tool_names is None


@pytest.mark.parametrize("header,fragment", [
    ("name: wrong-dir\ndescription: d", "directory name"),
    ("name: Bad_Name\ndescription: d", "kebab-case"),
    ("name: s", "bad skill header"),
    ("name: s\ndescription: '  '", "description is required"),
    ("name: s\ndescription: d\nallowed_tools: not-a-list", "bad skill header"),
    ("- just\n- a list", "mapping"),
    ("name: [unclosed", "invalid YAML"),
])
def test_malformed_headers_are_rejected(tmp_path, header, fragment):
    write_skill(tmp_path, "s", header)
    with pytest.raises(SkillError, match=fragment):
        SkillLoader(tmp_path)


def test_missing_header_and_empty_body_rejected(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "SKILL.md").write_text("no header here", encoding="utf-8")
    with pytest.raises(SkillError, match="YAML header"):
        SkillLoader(tmp_path)
    (d / "SKILL.md").write_text("---\nname: s\ndescription: d\n---\n", encoding="utf-8")
    with pytest.raises(SkillError, match="empty"):
        SkillLoader(tmp_path)


def test_missing_directory(tmp_path):
    with pytest.raises(SkillError, match="not found"):
        SkillLoader(tmp_path / "nope")


def test_bom_and_crlf_files_load(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "SKILL.md").write_bytes("﻿---\r\nname: s\r\ndescription: d\r\n---\r\nbody\r\n".encode("utf-8"))
    assert SkillLoader(tmp_path).get("s").body == "body"


def test_reload_picks_up_new_skill(tmp_path):
    write_skill(tmp_path, "a", "name: a\ndescription: d")
    loader = SkillLoader(tmp_path)
    write_skill(tmp_path, "b", "name: b\ndescription: d")
    loader.reload()
    assert loader.names() == ["a", "b"]
