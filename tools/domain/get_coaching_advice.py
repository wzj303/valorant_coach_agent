"""Retrieve citable passages from the bundled Chinese PC match review guide."""
import json
import math
import re
import unicodedata
from collections import Counter
from pathlib import Path

from schemas.tool_contracts import AdviceEntry, CoachingAdvice, CoachingAdviceInput
from schemas.tool_result import ToolResult

SOURCE = "knowledge/valorant-pc-match-review.zh-CN.json"
GUIDE = Path(__file__).resolve().parents[2] / SOURCE
_WORDS = re.compile(r"[a-z0-9_]+|[\u3400-\u9fff]+")
_ENTRY_ID = re.compile(r"[a-z]\d{2}", re.ASCII)
_FIELD_LABELS = {
    "cap": "经济上限 金币上限 资金上限 信用点上限",
    "loss_by_consecutive_losses": "连败奖励",
    "full_defuse_seconds": "拆包时间",
    "manual_plant_seconds": "下包时间",
}
_QUESTION_WORDS = {"多少", "怎么", "如何", "是否", "能否", "能不", "不能", "什么", "请问", "吗"}
_SECTION_TITLES = {
    "economy_calculation": "经济计算 预算 余额",
    "round_record": "回合记录",
    "reporting": "整场报告",
    "agent_instructions": "复盘方法",
}


def _normalize(text):
    return unicodedata.normalize("NFKC", text).casefold().strip()


def _terms(text):
    terms = set()
    for word in _WORDS.findall(_normalize(text)):
        if word.isascii() or len(word) == 1:
            terms.add(word)
        else:
            terms.update(word[i:i + 2] for i in range(len(word) - 1))
    return terms


def _entries(guide):
    """Keep source values intact, including conditions, exceptions and example labels."""
    for section in ("rules", "mechanics", "map_notes", "examples", "match_metrics"):
        for index, row in enumerate(guide[section]):
            title = " ".join(str(row[key]) for key in ("id", "category", "title", "topic", "map", "zh", "feature", "name")
                             if key in row)
            yield AdviceEntry(section=section, reference=f"/{section}/{index}", title=title, content=row)
    for key, definition in guide["glossary"].items():
        yield AdviceEntry(section="glossary", reference=f"/glossary/{key}", title=key, content=definition)
    for section, label in _SECTION_TITLES.items():
        values = guide[section]
        items = values.items() if isinstance(values, dict) else enumerate(values)
        for key, value in items:
            yield AdviceEntry(section=section, reference=f"/{section}/{key}",
                             title=f"{label} {key}", content=value)


def _search(entries, query, limit):
    phrase = _normalize(query)
    if _ENTRY_ID.fullmatch(phrase):
        return [e for e in entries if isinstance(e.content, dict)
                and _normalize(e.content.get("id", "")) == phrase][:limit]
    if phrase.startswith("/"):
        return [e for e in entries if e.reference.casefold() == phrase][:limit]

    terms = _terms(phrase) - _QUESTION_WORDS
    matches = []
    frequencies = Counter()
    for entry in entries:
        title = _normalize(entry.title)
        if entry.section == "mechanics":
            title += " " + " ".join(_FIELD_LABELS.get(key, "") for key in entry.content.get("values", {}))
        body = _normalize(json.dumps(entry.content, ensure_ascii=False))
        title_hits = terms & _terms(title)
        hits = title_hits | (terms & _terms(body))
        if hits:
            frequencies.update(hits)
            matches.append((entry, phrase in title, phrase in body, title_hits, hits))
    # Rare phrases such as 半拆 are more useful than words shared by many rules.
    weights = {t: 1 + math.log((1 + len(entries)) / (1 + n)) for t, n in frequencies.items()}
    ranked = []
    for entry, title_phrase, body_phrase, title_hits, hits in matches:
        score = 12 * title_phrase + 4 * body_phrase + sum(weights[t] for t in sorted(hits))
        score += 3 * sum(weights[t] for t in sorted(title_hits))
        if entry.section == "examples":
            score *= 0.75
        ranked.append((score, entry))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [entry for _, entry in ranked[:limit]]


def get_coaching_advice(request: CoachingAdviceInput) -> ToolResult[CoachingAdvice]:
    with GUIDE.open(encoding="utf-8-sig") as source:
        guide = json.load(source)
    candidates = [entry for entry in _entries(guide) if request.section in (None, entry.section)]
    entries = _search(candidates, request.query, request.limit)
    data = CoachingAdvice(source=SOURCE, version=guide["version"], reference_patch=guide["reference_patch"],
                          scope=guide["scope"], entries=entries)
    return ToolResult[CoachingAdvice](tool_name="get_coaching_advice", status="success" if entries else "partial",
                                      data=data, warnings=[] if entries else ["知识库未命中，可换用场景关键词或条目编号。"])
