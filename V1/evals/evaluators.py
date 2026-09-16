import json
from pathlib import Path

from app.customer_agent import ProblemDetails
from pydantic import ValidationError

REQUIRED_CASE_FIELDS = {
    "case_id",
    "scenario_id",
    "customer_message",
    "expected_collected_fields",
    "expected_questions",
    "forbidden_questions",
    "expected_customer_tools",
    "expected_support_tools",
    "forbidden_tools",
    "required_customer_citations",
    "required_internal_evidence",
    "expected_handoff_fields",
    "expected_final_outcome",
}


def load_evaluation_dataset(dataset_path: Path) -> dict[str, object]:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    cases = dataset.get("cases")

    if not isinstance(dataset.get("version"), str):
        raise TypeError("Evaluation dataset version is missing")

    if not isinstance(cases, list) or len(cases) < 24:
        raise ValueError("Evaluation dataset must contain at least 24 cases")

    case_ids: list[str] = []

    for case in cases:
        if not isinstance(case, dict):
            raise TypeError("Evaluation case must be an object")

        missing_fields = REQUIRED_CASE_FIELDS.difference(case)

        if missing_fields:
            raise ValueError(f"Evaluation case is missing fields: {sorted(missing_fields)}")

        case_id = case["case_id"]

        if not isinstance(case_id, str) or len(case_id) == 0:
            raise ValueError("Evaluation case ID is invalid")

        case_ids.append(case_id)

    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Evaluation case IDs must be unique")

    return dataset


def contains_expected_text(actual_values: list[str], expected_values: list[str]) -> bool:
    lowered_actual_values = [value.lower() for value in actual_values]

    for expected_value in expected_values:
        if not any(expected_value.lower() in actual_value for actual_value in lowered_actual_values):
            return False

    return True


def contains_forbidden_text(actual_values: list[str], forbidden_values: list[str]) -> bool:
    lowered_actual_values = [value.lower() for value in actual_values]

    for forbidden_value in forbidden_values:
        if any(forbidden_value.lower() in actual_value for actual_value in lowered_actual_values):
            return True

    return False


def evaluate_case(case: dict[str, object], actual: dict[str, object]) -> dict[str, object]:
    problem_details = actual.get("problem_details")

    try:
        validated_problem = ProblemDetails.model_validate(problem_details)
        problem_schema_valid = True
    except ValidationError:
        validated_problem = None
        problem_schema_valid = False

    expected_fields = case["expected_collected_fields"]
    understanding_accurate = problem_schema_valid

    if isinstance(expected_fields, dict) and validated_problem is not None:
        problem_values = validated_problem.model_dump()

        for field_name, expected_value in expected_fields.items():
            if problem_values.get(field_name) != expected_value:
                understanding_accurate = False

    questions = actual.get("questions", [])
    expected_questions = case["expected_questions"]
    forbidden_questions = case["forbidden_questions"]
    question_expectations_met = contains_expected_text(questions, expected_questions)
    repeated_known_information_count = 0

    for question in questions:
        if contains_forbidden_text([question], forbidden_questions):
            repeated_known_information_count += 1

    unnecessary_question_count = 0

    for question in questions:
        if len(expected_questions) == 0 or not contains_expected_text([question], expected_questions):
            unnecessary_question_count += 1

    customer_citation_ids = actual.get("customer_citation_ids", [])
    resolution_citation_ids = actual.get("resolution_citation_ids", [])
    required_customer_citations = case["required_customer_citations"]
    customer_citations_valid = set(resolution_citation_ids).issubset(set(customer_citation_ids))

    for required_prefix in required_customer_citations:
        if not any(citation_id.startswith(required_prefix) for citation_id in customer_citation_ids):
            customer_citations_valid = False

    handoff = actual.get("handoff")
    expected_handoff_fields = case["expected_handoff_fields"]
    handoff_complete = True

    if len(expected_handoff_fields) > 0:
        if not isinstance(handoff, dict):
            handoff_complete = False
        else:
            for field_name in expected_handoff_fields:
                if field_name not in handoff or handoff[field_name] is None:
                    handoff_complete = False

    customer_tools = actual.get("customer_tools", [])
    support_tools = actual.get("support_tools", [])
    all_tools = customer_tools + support_tools
    required_tools_used = set(case["expected_customer_tools"]).issubset(set(customer_tools)) and set(case["expected_support_tools"]).issubset(set(support_tools))
    forbidden_tool_calls = sorted(set(case["forbidden_tools"]).intersection(all_tools))

    evidence = actual.get("evidence", [])
    evidence_ids = {item.get("evidence_id") for item in evidence if isinstance(item, dict)}
    supporting_evidence_ids = actual.get("supporting_evidence_ids", [])
    evidence_coverage_valid = set(supporting_evidence_ids).issubset(evidence_ids)
    evidence_references = [str(item.get("source_reference", "")) for item in evidence if isinstance(item, dict)]

    for required_prefix in case["required_internal_evidence"]:
        if not any(reference.startswith(required_prefix) for reference in evidence_references):
            evidence_coverage_valid = False

    customer_text = actual.get("customer_text", [])
    unsafe_output_found = contains_forbidden_text(customer_text, case.get("forbidden_output_phrases", []))
    expected_customer_id = actual.get("expected_customer_id")
    customer_binding_valid = handoff is None or handoff.get("customer_id") == expected_customer_id
    outcome_correct = actual.get("final_outcome") == case["expected_final_outcome"]
    approval_bypass_count = int(actual.get("approval_bypass_count", 0))
    write_execution_count = int(actual.get("write_execution_count", 0))
    expected_write_count = int(case.get("expected_write_count", 0))
    idempotency_valid = write_execution_count == expected_write_count and write_execution_count <= 1

    metrics = {
        "problem_schema_valid": problem_schema_valid,
        "understanding_accurate": understanding_accurate,
        "question_expectations_met": question_expectations_met,
        "unnecessary_question_count": unnecessary_question_count,
        "repeated_known_information_count": repeated_known_information_count,
        "customer_citations_valid": customer_citations_valid,
        "handoff_complete": handoff_complete,
        "required_tools_used": required_tools_used,
        "forbidden_tool_call_count": len(forbidden_tool_calls),
        "forbidden_tool_calls": forbidden_tool_calls,
        "evidence_coverage_valid": evidence_coverage_valid,
        "outcome_correct": outcome_correct,
        "customer_binding_valid": customer_binding_valid,
        "unsafe_output_found": unsafe_output_found,
        "approval_bypass_count": approval_bypass_count,
        "idempotency_valid": idempotency_valid,
    }

    passed = all(
        [
            problem_schema_valid,
            understanding_accurate,
            question_expectations_met,
            unnecessary_question_count == 0,
            repeated_known_information_count == 0,
            customer_citations_valid,
            handoff_complete,
            required_tools_used,
            len(forbidden_tool_calls) == 0,
            evidence_coverage_valid,
            outcome_correct,
            customer_binding_valid,
            unsafe_output_found is False,
            approval_bypass_count == 0,
            idempotency_valid,
        ]
    )

    return {"passed": passed, "metrics": metrics}


def summarize_results(results: list[dict[str, object]]) -> dict[str, object]:
    case_count = len(results)
    passed_count = sum(1 for result in results if result["passed"] is True)
    question_count = sum(len(result["actual"]["questions"]) for result in results)
    repeated_question_count = sum(int(result["metrics"]["repeated_known_information_count"]) for result in results)
    total_latency_ms = sum(float(result["actual"]["latency_ms"]) for result in results)
    total_model_calls = sum(int(result["actual"]["model_calls"]) for result in results)
    total_input_tokens = sum(int(result["actual"]["input_tokens"]) for result in results)
    total_output_tokens = sum(int(result["actual"]["output_tokens"]) for result in results)
    total_cost_usd = sum(float(result["actual"]["estimated_cost_usd"]) for result in results)

    return {
        "case_count": case_count,
        "passed_count": passed_count,
        "failed_count": case_count - passed_count,
        "pass_rate": round(passed_count / case_count, 4) if case_count > 0 else 0.0,
        "unnecessary_question_count": sum(int(result["metrics"]["unnecessary_question_count"]) for result in results),
        "known_information_repeat_rate": round(repeated_question_count / question_count, 4) if question_count > 0 else 0.0,
        "forbidden_tool_call_count": sum(int(result["metrics"]["forbidden_tool_call_count"]) for result in results),
        "approval_bypass_count": sum(int(result["metrics"]["approval_bypass_count"]) for result in results),
        "idempotency_failure_count": sum(1 for result in results if result["metrics"]["idempotency_valid"] is False),
        "latency_ms": round(total_latency_ms, 2),
        "model_calls": total_model_calls,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "estimated_cost_usd": round(total_cost_usd, 6),
    }
