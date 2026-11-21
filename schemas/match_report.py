from pydantic import Field

from .coach_response import Recommendation
from .common import Contract, Text


class MatchReport(Contract):
    match_id: Text
    analyzed_rounds: list[Text]
    strengths: list[Text] = Field(default_factory=list)
    recurring_problems: list[Text] = Field(default_factory=list)
    improvement_plan: list[Recommendation] = Field(default_factory=list)
    final_summary: Text
