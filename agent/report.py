"""Render a CoachResponse as a Markdown report."""
from __future__ import annotations

from schemas import CoachResponse
from schemas.coach_response import Recommendation


def guide_recommendation(result: dict, priority: int = 1) -> Recommendation | None:
    """Show a sourced review question in offline reports, even with incomplete observations."""
    data = result.get("data") or {}
    for entry in data.get("entries", []):
        if entry["section"] == "rules":
            rule = entry["content"]
            return Recommendation(priority=priority, action=rule["evaluate"],
                                  reason=f"知识库 {rule['id']}《{rule['title']}》适用于：{rule['when']}",
                                  expected_benefit=f"帮助核对本回合是否适用，同时考虑例外：{rule['exception']}")
    return None

_STATUS = {"success": "完整", "partial": "部分完成", "failed": "未完成"}
_SEVERITY = {"info": "提示", "low": "低", "medium": "中", "high": "高"}
_EVIDENCE = {"observed": "截图观察", "inferred": "推测", "user_confirmed": "用户确认",
             "derived": "工具计算", "unknown": "未知"}


def render_markdown(report: CoachResponse) -> str:
    out: list[str] = [f"# 教练报告 · {report.task_id}", "", f"**状态：** {_STATUS[report.task_status]}"]
    if report.overall_judgement:
        out += ["", "## 总体判断", report.overall_judgement]
    if report.buy_review:
        out += ["", "## 购买复盘", report.buy_review]
    if report.position_review:
        out += ["", "## 站位复盘", report.position_review]
    if report.findings:
        out += ["", "## 发现"]
        for i, f in enumerate(report.findings, 1):
            conf = f"，置信度 {f.confidence:.0%}" if f.confidence is not None else ""
            out += [f"{i}. **{f.title}**（严重度：{_SEVERITY[f.severity]}{conf}）",
                    f"   {f.explanation}", f"   依据：{', '.join(f.evidence_refs)}"]
    if report.recommendations:
        out += ["", "## 建议"]
        for r in sorted(report.recommendations, key=lambda r: r.priority):
            out += [f"{r.priority}. **{r.action}**", f"   - 原因：{r.reason}", f"   - 预期收益：{r.expected_benefit}"]
    if report.evidence:
        out += ["", "## 依据", "| 项目 | 类型 | 来源 | 置信度 | 备注 |", "|---|---|---|---|---|"]
        for e in report.evidence:
            conf = f"{e.confidence:.0%}" if e.confidence is not None else "-"
            out.append(f"| {e.field_name} | {_EVIDENCE[e.status]} | {e.source} | {conf} | {e.note or ''} |")
    if report.missing_information:
        out += ["", "## 缺失信息"] + [f"- {m}" for m in report.missing_information]
    out += ["", "## 总结", report.coach_summary, ""]
    return "\n".join(out)
