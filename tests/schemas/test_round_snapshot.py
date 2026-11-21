import pytest
from pydantic import ValidationError

from schemas import FieldEvidence, PlayerSnapshot, RoundSnapshot, apply_player_observation


@pytest.mark.parametrize("name", ["complete_round", "partial_round", "jett_example"])
def test_roundtrip(load_case, name):
    r = RoundSnapshot.model_validate(load_case(name))
    assert RoundSnapshot.model_validate_json(r.model_dump_json()) == r


@pytest.mark.parametrize(("key", "value"), [
    ("round_id", " "), ("round_index", 0), ("round_index", True), ("round_index", "1"),
    ("map_id", "ascent"), ("side", "blue"), ("other", 0), ("score", {"ally": -1}),
])
def test_round_errors(load_case, key, value):
    with pytest.raises(ValidationError):
        RoundSnapshot.model_validate(load_case("jett_example") | {key: value})


@pytest.mark.parametrize("change", [
    {"slot": 0}, {"slot": 6}, {"team": "enemy"}, {"credits_remaining": -1},
    {"credits_before_buy": 4.5}, {"credits_before_buy": "4199"},
    {"credits_before_buy": True}, {"armor": "medium"}, {"abilities": {"Q": 0}},
    {"weapon": ["Vandal", "Phantom"]}, {"weapon": "Vandal / Phantom"},
    {"weapon": "Classic", "secondary_weapon": "classic"},
])
def test_player_errors(load_case, change):
    r = load_case("partial_round")
    r["ally_players"][0].update(change)
    with pytest.raises(ValidationError):
        RoundSnapshot.model_validate(r)


def test_roster_identity(load_case):
    r = load_case("complete_round")
    p = r["ally_players"][0]
    for bad in ([p, p], r["ally_players"] + [p]):
        with pytest.raises(ValidationError):
            RoundSnapshot.model_validate(r | {"ally_players": bad})


def test_unknown_money_and_abilities(load_case):
    r = RoundSnapshot.model_validate(load_case("partial_round"))
    assert r.ally_players[0].credits_remaining is None
    assert r.ally_players[0].abilities == {"Slow Orb": None, "Barrier Orb": False}
    assert r.enemy_players[0].credits_before_buy == 3000
    assert r.enemy_players[0].evidence[-1].status == "inferred"


def test_jett_confirmation_survives_inference(load_case):
    r = RoundSnapshot.model_validate(load_case("jett_example"))
    assert len(r.ally_players) == 1 and r.enemy_players is None
    p = r.ally_players[0]
    assert (p.agent, p.weapon, p.armor, p.credits_unclassified) == ("Jett", "Vandal", "light", 4199)
    assert p.credits_before_buy is None and p.credits_remaining is None
    guess = FieldEvidence(field_name="weapon", source="model_inference", status="inferred", confidence=1)
    assert apply_player_observation(p, "Phantom", guess) == p
    confirmed = FieldEvidence(field_name="weapon", source="user_input", status="user_confirmed")
    revised = apply_player_observation(p, "Phantom", confirmed)
    assert revised.weapon == "Phantom" and p.weapon == "Vandal"


@pytest.mark.parametrize("ev", [
    {"status": "observed", "source": "model_inference"},
    {"status": "user_confirmed", "source": "tool"},
    {"status": "unknown", "source": "unknown", "confidence": 0.0},
    {"status": "inferred", "source": "model_inference", "confidence": 1.1},
])
def test_evidence_origin(ev):
    with pytest.raises(ValidationError):
        FieldEvidence(field_name="weapon", **ev)


def test_evidence_targets():
    p = {"slot": 1, "team": "ally", "weapon": "Vandal"}
    observed = {"field_name": "weapon", "status": "observed", "source": "screenshot"}
    for entries in ([observed, observed], [observed | {"field_name": "bad"}],
                    [observed | {"field_name": "agent"}],
                    [observed | {"status": "unknown", "source": "unknown"}]):
        with pytest.raises(ValidationError):
            PlayerSnapshot.model_validate(p | {"evidence": entries})
