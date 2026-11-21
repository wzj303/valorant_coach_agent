"""Fixtures for the agent tests."""
from __future__ import annotations

from typing import Any

import pytest

from agent import AgentLoop, MockLlmAdapter, SkillLoader, ToolRegistry
from tools.registry import build_registry

from tests.agent.helpers import ROOT, fake_handlers, make_png


@pytest.fixture
def png_bytes() -> bytes:
    return make_png()


@pytest.fixture
def png_file(tmp_path, png_bytes: bytes):
    path = tmp_path / "buy_phase.png"
    path.write_bytes(png_bytes)
    return path

@pytest.fixture
def skills() -> SkillLoader:
    return SkillLoader(ROOT / "skills")


@pytest.fixture
def registry() -> ToolRegistry:
    return build_registry(handlers=fake_handlers())


@pytest.fixture
def make_loop(skills: SkillLoader, registry: ToolRegistry):
    def factory(steps: list[Any], **kwargs: Any) -> tuple[AgentLoop, MockLlmAdapter]:
        llm = MockLlmAdapter(steps)
        return AgentLoop(llm, registry, skills, **kwargs), llm
    return factory
