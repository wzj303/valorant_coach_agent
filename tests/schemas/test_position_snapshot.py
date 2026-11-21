import pytest
from pydantic import ValidationError

from schemas import AnalysisContext, PositionSnapshot
from tools.validate_round_data import validate_round_data


def test_region_and_unknown(load_case):
    p = PositionSnapshot.model_validate(load_case("position_snapshot"))
    assert p.players[1].region == "Mid" and p.players[1].x is None
    assert p.players[2].region is None and p.players[2].confidence is None
    assert PositionSnapshot.model_validate_json(p.model_dump_json()) == p
    result = validate_round_data(position_snapshot=p, expected_map_revision="fixture-r1")
    assert result.status == "partial"
    assert result.data.completeness == 0.75
    assert result.data.missing_fields == ["position_snapshot.players.enemy.1.location"]


@pytest.mark.parametrize(("system", "x", "ok"), [
    ("normalized", 0.0, True), ("normalized", 1, True), ("normalized", 1.001, False),
    ("normalized", -0.001, False), ("region_only", 0.2, False), (None, 0, False),
    ("pixel", 500, True), ("pixel", float("nan"), False),
])
def test_bounds(load_case, system, x, ok):
    p = load_case("position_snapshot")
    p["coordinate_system"] = system
    p["players"][0]["x"] = x
    result = validate_round_data(position_snapshot=p)
    assert result.data.valid is ok


def test_unknown_position(load_case):
    for change in ({"confidence": 0}, {"region": "Mid"}, {"x": 0}):
        p = load_case("position_snapshot")
        p["players"][-1].update(change)
        with pytest.raises(ValidationError):
            PositionSnapshot.model_validate(p)


def test_context_links(load_case):
    r, p = load_case("complete_round"), load_case("position_snapshot")
    base = dict(task_id="t", task_type="position_review", match_id=r["match_id"],
                round_snapshots=[r], position_snapshots=[p])
    ctx = AnalysisContext(**base)
    assert AnalysisContext.model_validate_json(ctx.model_dump_json()) == ctx
    for changes in ({"match_id": "other"}, {"round_snapshots": []}, {"round_snapshots": [r, r]},
                    {"position_snapshots": [p, p]}, {"position_snapshots": [p | {"map_revision": "old"}]}):
        with pytest.raises(ValidationError):
            AnalysisContext.model_validate(base | changes)
