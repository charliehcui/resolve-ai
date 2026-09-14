from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.handoff import SupportHandoff


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    evidence_id: str = Field(min_length=1, description="An evidence ID assigned by server code, never by the model")
    ticket_id: int = Field(gt=0, description="The ticket that owns this evidence")
    customer_id: str = Field(min_length=1, max_length=100, description="The customer bound by the server to the current ticket")
    source_type: Literal["tool"] = Field(default="tool", description="The actual source type; only read-only tool evidence is supported")
    source_reference: str = Field(min_length=1, max_length=300, description="The tool name and the observed record reference")
    observed_at: AwareDatetime = Field(description="The timestamp supplied by the observed source record")
    summary: str = Field(min_length=1, max_length=1000, description="A bounded factual source summary in English, created by server code")
    customer_visibility: Literal["INTERNAL"] = Field(default="INTERNAL", description="Internal evidence is never returned in customer-facing responses")
    feature: str = Field(max_length=100, description="The feature associated with the observed record")
    facts: dict[str, str | int | bool] = Field(description="Only the bounded source fields needed for validation; excludes raw logs and response messages")


class SupportDiagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    conclusion: str = Field(min_length=1, description="A short internal conclusion in English supported by tool facts")
    root_cause: str | None = Field(default=None, description="The evidence-supported cause in English, or null when the cause is unknown")
    supporting_evidence_ids: list[str] = Field(default_factory=list, max_length=10, description="Existing server-assigned evidence IDs supporting the cause and resolution")
    contradicting_evidence_ids: list[str] = Field(default_factory=list, max_length=10, description="Existing evidence IDs conflicting with the current diagnosis")
    confidence_band: Literal["low", "medium", "high"] = Field(default="low", description="Confidence in the diagnosis; low confidence cannot justify a definite resolution")
    resolution: str | None = Field(default=None, description="A short evidence-supported internal resolution in English; null when no safe resolution is supported")
    escalation_reason: str | None = Field(default=None, description="Why engineer investigation is required, in English; null for a supported non-escalation outcome")
    customer_explanation: str = Field(min_length=1, description="A simple, safe customer-visible explanation in Simplified Chinese")
    outcome: Literal["resolution", "action_required", "engineer_escalation"] = Field(description="Whether the investigation produced a resolution, identified a safe internal operation requiring human handling without execution, or requires engineer escalation")


class EngineerEscalationPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: int = Field(description="The ticket the engineer should continue investigating")
    customer_id: str | None = Field(description="The server-bound customer ID, or null when the handoff is missing")
    issue_summary: str = Field(description="The original issue summary in English, without an invented cause")
    customer_impact: str = Field(description="The recorded customer impact in English, without invented impact")
    customer_diagnosis: SupportHandoff | None = Field(description="The existing customer handoff, preserving confirmed facts, timestamps, environment, attempted steps, citations, and remaining questions")
    internal_evidence_ids: list[str] = Field(description="References to the evidence attached to the same saved investigation result, without duplicating evidence bodies")
    tools_used: list[str] = Field(description="The internal read-only tools actually used, including failed queries")
    excluded_causes: list[str] = Field(description="Only causes ruled out by validated evidence, in English")
    possible_causes: list[str] = Field(description="Evidence-supported possibilities in English, never presented as confirmed recovery")
    unanswered_questions: list[str] = Field(description="Remaining customer questions and internal investigation gaps in English")
    escalation_reason: str = Field(description="The recorded reason the investigation requires an engineer, in English")
    next_checks: list[str] = Field(description="Bounded suggested engineer checks in English, without executing actions or repeating confirmed questions")


class SupportInvestigationResult(SupportDiagnosis):
    supporting_facts: list[str] = Field(default_factory=list, description="Internal English source summaries derived by the server from the cited evidence")
    evidence: list[EvidenceItem] = Field(default_factory=list, max_length=10, description="Server-created evidence retained with the saved ticket investigation")
    validation_errors: list[str] = Field(default_factory=list, description="Internal English validation failures; never exposed to the customer")
    escalation_package: EngineerEscalationPackage | None = Field(default=None, description="The server-built engineer handoff when the outcome requires escalation")
