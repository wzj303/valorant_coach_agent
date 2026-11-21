"""Validate raw tool arguments without guessing missing observations."""
from pydantic import ValidationError

from schemas.common import DataQuality
from schemas.validation import ValidateRoundInput, ValidationResult, ValidationToolResult

HEADER_FIELDS = ("map_revision", "game_patch", "side", "round_phase", "score.ally",
                 "score.enemy", "ally_loss_streak", "enemy_loss_streak")
PLAYER_FIELDS = ("agent", "role", "credits_before_buy", "credits_remaining", "weapon",
                 "secondary_weapon", "armor", "abilities")


class QualityCheck:
    def __init__(self):
        self.missing = []
        self.warnings = []

    def require_evidence(self, evidence, field, path):
        if field not in evidence:
            self.warnings.append(f"{path}: source has not been recorded")

    def map_check(self, snapshot, prefix, expected_revision):
        if snapshot.map_revision is None:
            return
        if expected_revision is None:
            self.warnings.append(f"{prefix}.map_revision: map provider revision unavailable")
        elif snapshot.map_revision != expected_revision:
            self.warnings.append(f"{prefix}.map_revision: does not match the configured map data")

    def round_score(self, snapshot):
        prefix = "round_snapshot"
        header = 0
        evidence = {e.field_name for e in snapshot.evidence}
        for field in HEADER_FIELDS:
            if field.startswith("score."):
                value = getattr(snapshot.score, field.split(".")[1], None)
            else:
                value = getattr(snapshot, field)
            path = f"{prefix}.{field}"
            if value is None:
                self.missing.append(path)
            else:
                header += 1
                self.require_evidence(evidence, field, path)

        player_points = 0.0
        for team in ("ally", "enemy"):
            roster = {p.slot: p for p in getattr(snapshot, f"{team}_players") or []}
            for slot in range(1, 6):
                base = f"{prefix}.{team}_players[slot={slot}]"
                if slot not in roster:
                    self.missing.append(base)
                    continue
                player = roster[slot]
                documented = {e.field_name for e in player.evidence}
                if player.credits_unclassified is not None:
                    self.warnings.append(f"{base}.credits_unclassified: buy timing unknown")
                    self.require_evidence(documented, "credits_unclassified", base + ".credits_unclassified")
                if player.credit_exception_note:
                    self.warnings.append(f"{base}.credit_exception_note: {player.credit_exception_note}")
                for field in PLAYER_FIELDS:
                    value = getattr(player, field)
                    path = f"{base}.{field}"
                    if value is None or value == {}:
                        self.missing.append(path)
                        continue
                    if field == "abilities":
                        known = 0
                        for name, purchased in value.items():
                            if purchased is None:
                                self.missing.append(f"{path}.{name}")
                            else:
                                known += 1
                                if field not in documented:
                                    self.require_evidence(documented, f"abilities.{name}", f"{path}.{name}")
                        player_points += known / len(value)
                    else:
                        player_points += 1
                        self.require_evidence(documented, field, path)
        return 0.20 * header / len(HEADER_FIELDS) + 0.80 * player_points / (10 * len(PLAYER_FIELDS))

    def position_score(self, snapshot):
        prefix = "position_snapshot"
        fields = ("map_revision", "timestamp_ms", "coordinate_system")
        header = 0
        evidence = {e.field_name for e in snapshot.evidence}
        for field in fields:
            if getattr(snapshot, field) is None:
                self.missing.append(f"{prefix}.{field}")
            else:
                header += 1
                self.require_evidence(evidence, field, f"{prefix}.{field}")
        players = snapshot.players or []
        located = 0
        if not players:
            self.missing.append(f"{prefix}.players")
        for p in players:
            base = f"players.{p.team}.{p.slot}"
            if p.region or (p.x is not None and p.y is not None):
                located += 1
            else:
                self.missing.append(f"{prefix}.{base}.location")
            if (p.x is None) != (p.y is None):
                self.warnings.append(f"{prefix}.{base}: incomplete coordinate pair")
            for field in ("region", "x", "y", "agent"):
                if getattr(p, field) is not None:
                    self.require_evidence(evidence, f"{base}.{field}", f"{prefix}.{base}.{field}")
        return 0.25 * header / len(fields) + 0.75 * located / max(1, len(players))


def _result(status, completeness, missing, warnings, errors, normalized=None, position=None):
    data = ValidationResult(valid=status != "failed", completeness=completeness,
                            missing_fields=missing, warnings=warnings, errors=errors,
                            normalized_snapshot=normalized, normalized_position_snapshot=position)
    return ValidationToolResult(tool_name="validate_round_data", status=status, data=data,
                                warnings=warnings, errors=errors)


def validate_round_data(round_snapshot=None, strict_mode=False, *, position_snapshot=None,
                        expected_map_revision=None):
    """expected_map_revision is trusted provider context, outside the LLM input schema."""
    try:
        args = ValidateRoundInput(round_snapshot=round_snapshot, position_snapshot=position_snapshot,
                                  strict_mode=strict_mode)
    except ValidationError as exc:
        issues = exc.errors(include_url=False, include_input=False)
        def path(issue):
            return ".".join(map(str, issue["loc"])) or "input"
        errors = [f"{path(i)}: {i['msg']}" for i in issues]
        missing = [path(i) for i in issues if i["type"] == "missing"]
        return _result("failed", None, missing, [], errors)

    check = QualityCheck()
    scores = []
    r, p = args.round_snapshot, args.position_snapshot
    if r is not None:
        scores.append(check.round_score(r))
        check.map_check(r, "round_snapshot", expected_map_revision)
        round_warnings = list(check.warnings)
        if check.missing:
            round_warnings.append("Round observations are incomplete.")
        r = r.model_copy(update={"data_quality": DataQuality(completeness=round(scores[0], 6),
                         missing_fields=list(check.missing), warnings=round_warnings)})
    if p is not None:
        scores.append(check.position_score(p))
        check.map_check(p, "position_snapshot", expected_map_revision)
        if r is None:
            check.warnings.append("position_snapshot.round_id: parent round unavailable for comparison")

    if check.missing:
        check.warnings.append("Missing observations are listed in missing_fields.")
    errors = ["strict_mode requires complete, documented, consistent data."] if args.strict_mode and check.warnings else []
    status = "failed" if errors else "partial" if check.warnings else "success"
    return _result(status, round(sum(scores) / len(scores), 6), check.missing, check.warnings,
                   errors, r if r is not None else p, p if r is not None else None)
