"""SkillLoader: Markdown skills (SKILL.md) that steer the agent's use of registered tools.

A skill is plain text plus a small YAML header. It has no code and no access to any LLM
provider; it can only name tools that the ToolRegistry already holds.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

import yaml
from pydantic import BaseModel, Field, ValidationError

from .exceptions import SkillError, SkillNotFoundError

DEFAULT_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)(.*)\Z", re.S)
_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class Skill(BaseModel):
    name: str
    description: str
    task_types: list[str] = Field(default_factory=list)  # buy_review | position_review | match_review
    triggers: list[str] = Field(default_factory=list)  # keywords used when task_type is absent
    allowed_tools: list[str] = Field(default_factory=list)  # empty = all registered tools
    body: str
    path: Path

    @property
    def tool_names(self) -> frozenset[str] | None:
        return frozenset(self.allowed_tools) if self.allowed_tools else None


class SkillLoader:
    def __init__(self, skills_dir: str | Path | None = None) -> None:
        self.skills_dir = Path(skills_dir) if skills_dir is not None else DEFAULT_SKILLS_DIR
        self._skills: dict[str, Skill] = {}
        self.reload()

    # ----------------------------------------------------------------- loading
    def reload(self) -> None:
        if not self.skills_dir.is_dir():
            raise SkillError(f"skills directory not found: {self.skills_dir}")
        skills: dict[str, Skill] = {}
        for path in sorted(self.skills_dir.glob("*/SKILL.md")):
            skill = self._parse(path)
            if skill.name in skills:
                raise SkillError(f"duplicate skill name {skill.name!r} ({path})")
            skills[skill.name] = skill
        self._skills = skills

    @staticmethod
    def _parse(path: Path) -> Skill:
        text = path.read_text(encoding="utf-8-sig")
        match = _FRONTMATTER.match(text)
        if not match:
            raise SkillError(f"{path}: SKILL.md must start with a '---' YAML header")
        try:
            header = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError as exc:
            raise SkillError(f"{path}: invalid YAML header: {exc}") from exc
        if not isinstance(header, dict):
            raise SkillError(f"{path}: YAML header must be a mapping")
        body = match.group(2).strip()
        if not body:
            raise SkillError(f"{path}: skill body is empty")
        try:
            skill = Skill(path=path, body=body, **header)
        except (ValidationError, TypeError) as exc:
            raise SkillError(f"{path}: bad skill header: {exc}") from exc
        if not _NAME.match(skill.name) or skill.name != path.parent.name:
            raise SkillError(f"{path}: skill name {skill.name!r} must be kebab-case and equal "
                             f"its directory name {path.parent.name!r}")
        if not skill.description.strip():
            raise SkillError(f"{path}: description is required")
        return skill

    # ------------------------------------------------------------------ lookup
    def names(self) -> list[str]:
        return list(self._skills)

    def get(self, name: str) -> Skill:
        try:
            return self._skills[name]
        except KeyError:
            raise SkillNotFoundError(f"unknown skill {name!r}; available: {', '.join(self._skills)}") from None

    def catalog(self) -> list[tuple[str, str]]:
        """(name, description) for every skill: the only part shown before a skill is chosen."""
        return [(s.name, s.description) for s in self._skills.values()]

    def select(self, task_type: str | None = None, text: str = "") -> Skill | None:
        """Pick a skill. An explicit task_type wins. Otherwise score trigger keywords in `text`.
        Returns None when nothing matches or the top two skills tie (do not guess)."""
        if task_type:
            for skill in self._skills.values():
                if task_type in skill.task_types:
                    return skill
            raise SkillNotFoundError(f"no skill handles task_type {task_type!r}")
        lowered = text.lower()
        scored = sorted(((sum(lowered.count(t.lower()) for t in s.triggers), s)
                         for s in self._skills.values()), key=lambda x: -x[0])
        if not scored or scored[0][0] == 0:
            return None
        if len(scored) > 1 and scored[1][0] == scored[0][0]:
            return None
        return scored[0][1]

    def validate_tools(self, registered: Iterable[str]) -> None:
        """Fail at start-up if a skill names a tool that is not registered."""
        known = set(registered)
        for skill in self._skills.values():
            unknown = sorted(set(skill.allowed_tools) - known)
            if unknown:
                raise SkillError(f"skill {skill.name!r} allows unregistered tool(s): {', '.join(unknown)}")

    @staticmethod
    def render(skill: Skill) -> str:
        return f"# Active skill: {skill.name}\n\n{skill.body}"
