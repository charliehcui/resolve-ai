import json
import re

import pytest

from app.classification import CLASSIFICATION_SYSTEM_PROMPT, ClassificationResult
from app.customer_agent import CUSTOMER_QUESTION_PROMPT, CUSTOMER_RESOLUTION_PROMPT, CUSTOMER_SIDE_DATA_DECISION_PROMPT, CUSTOMER_SYSTEM_PROMPT, CUSTOMER_VERIFICATION_PROMPT, CustomerQuestion, CustomerResolution, CustomerSideDataDecision, CustomerVerification, ProblemDetails
from app.customer_question_retrieval import retrieve_documents_for_customer_question
from app.handoff import SUPPORT_HANDOFF_SYSTEM_PROMPT, SupportHandoffSummary
from app.support_agent import SUPPORT_INVESTIGATION_SYSTEM_PROMPT, SupportInvestigationRun, support_investigation_tools
from app.support_results import SupportInvestigationResult


@pytest.mark.parametrize("prompt", [CLASSIFICATION_SYSTEM_PROMPT, CUSTOMER_SYSTEM_PROMPT, CUSTOMER_QUESTION_PROMPT, CUSTOMER_SIDE_DATA_DECISION_PROMPT, CUSTOMER_RESOLUTION_PROMPT, CUSTOMER_VERIFICATION_PROMPT, SUPPORT_HANDOFF_SYSTEM_PROMPT, SUPPORT_INVESTIGATION_SYSTEM_PROMPT])
def test_system_prompts_keep_internal_language_english(prompt: str) -> None:
    assert re.search(r"[\u4e00-\u9fff]", prompt) is None
    assert "Simplified Chinese" in prompt


def test_prompts_limit_simplified_chinese_to_customer_visible_output() -> None:
    assert "summary and customer_goal are displayed in the Customer View" in CUSTOMER_SYSTEM_PROMPT
    assert "affected_feature, problem, and missing_information are internal workflow data and must use English" in CUSTOMER_SYSTEM_PROMPT
    assert "question is customer-visible" in CUSTOMER_QUESTION_PROMPT
    assert "explanation and steps are customer-visible" in CUSTOMER_RESOLUTION_PROMPT
    assert "conclusion and supporting_facts are internal technical output and must use English" in SUPPORT_INVESTIGATION_SYSTEM_PROMPT
    assert "customer_explanation is customer-visible" in SUPPORT_INVESTIGATION_SYSTEM_PROMPT


@pytest.mark.parametrize("output_model", [ClassificationResult, ProblemDetails, CustomerQuestion, CustomerSideDataDecision, CustomerResolution, CustomerVerification, SupportHandoffSummary, SupportInvestigationResult, SupportInvestigationRun])
def test_structured_output_descriptions_are_english(output_model: type) -> None:
    schema_text = json.dumps(output_model.model_json_schema(), ensure_ascii=False)

    assert re.search(r"[\u4e00-\u9fff]", schema_text) is None


def test_tool_descriptions_are_english() -> None:
    tools = [retrieve_documents_for_customer_question, *support_investigation_tools]

    for current_tool in tools:
        assert re.search(r"[\u4e00-\u9fff]", current_tool.description) is None
