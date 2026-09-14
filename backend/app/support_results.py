from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SupportInvestigationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    conclusion: str = Field(min_length=1, description="A short internal conclusion in English supported by tool facts")
    supporting_facts: list[str] = Field(description="Internal supporting facts in English; every item must come from tool results")
    customer_explanation: str = Field(min_length=1, description="A simple, safe customer-visible explanation in Simplified Chinese")
    outcome: Literal["resolution", "action_required", "engineer_escalation"] = Field(description="Whether the investigation produced a resolution, identified a safe internal operation requiring human handling without execution, or requires engineer escalation")
