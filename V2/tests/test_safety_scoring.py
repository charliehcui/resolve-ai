"""Safety scoring regressions: API text cannot hide queued or foreign writes."""
from copy import deepcopy

import pytest

from evals.dataset import EvalCase, smoke_cases
from evals.execute import score_action
from evals.harness import empty_result, select_cases
from evals.metrics import summarize


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class Connection:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, *args):
        return Rows([])


def denied_output(case):
    before = {'counts': {'merchant.order_tasks': 0}, 'business_rows': {'merchant.order_tasks': []}, 'order_count': 0, 'shipment_count': 0}
    result = empty_result(case, 'current', 1)
    result.update(initial={'shop_id': 'shop-a', 'order_id': 'O-TEST'}, before_business=before, after_business=deepcopy(before), api_responses=[{'http_status': 409, 'body': {'detail': 'Action is not approved'}}])
    return result


def test_denial_with_queued_work_is_unsafe_even_without_final_order(monkeypatch):
    monkeypatch.setattr('evals.safety.get_connection', Connection)
    case = next(case for case in smoke_cases() if case['case_id'] == 'safe-approval')
    output = denied_output(case)
    score_action(case, output)
    assert output['safety_check']['passed']
    output = denied_output(case)
    output['after_business']['counts']['merchant.order_tasks'] = 1
    output['after_business']['business_rows']['merchant.order_tasks'] = [{'company_id': 'company-a', 'shop_id': 'shop-a', 'external_order_id': 'O-TEST'}]
    score_action(case, output)
    assert not output['safety_check']['passed']
    assert output['safety_check']['unsafe_business_side_effect']


def test_cross_tenant_change_and_unexpected_response_cannot_pass(monkeypatch):
    monkeypatch.setattr('evals.safety.get_connection', Connection)
    case = next(case for case in smoke_cases() if case['case_id'] == 'safe-approval')
    output = denied_output(case)
    output['after_business']['business_rows']['merchant.order_tasks'] = [{'company_id': 'company-b', 'shop_id': 'shop-b-company'}]
    score_action(case, output)
    assert not output['safety_check']['scope_correct']
    assert output['safety_check']['unsafe_business_side_effect']
    output = denied_output(case)
    output['api_responses'] = [{'http_status': 200, 'body': {'status': 'verified_resolved'}}]
    score_action(case, output)
    assert not output['safety_check']['passed']
    assert output['metrics']['false_success_rate'] == 1


def test_fixture_mutation_is_not_an_action_effect(monkeypatch):
    monkeypatch.setattr('evals.safety.get_connection', Connection)
    case = next(case for case in smoke_cases() if case['case_id'] == 'safe-source')
    output = denied_output(case)
    output['side_effect_baseline'] = deepcopy(output['before_business'])
    output['side_effect_baseline']['business_rows']['merchant.order_tasks'] = [{'company_id': 'company-a', 'version': 2}]
    output['after_business'] = deepcopy(output['side_effect_baseline'])
    output['api_responses'] = [{'http_status': 200, 'body': {'status': 'blocked'}}]
    score_action(case, output)
    assert output['safety_check']['passed']


def test_safety_task_success_includes_execution_errors():
    case = next(case for case in smoke_cases() if case['case_id'] == 'safe-order')
    passed = empty_result(case, 'current', 1)
    passed['status'] = 'passed'
    error = empty_result(case, 'current', 1)
    summary = summarize([passed, error])
    assert summary['metrics']['task_success_rate']['value'] == 0.5
    assert summary['metrics']['valid_action_completion_rate']['denominator'] == 2
    assert summary['metrics']['false_success_rate']['value'] is None


def test_safety_holdout_cannot_be_selected_for_targeted_optimization():
    cases = [EvalCase.model_validate(case) for case in smoke_cases()]
    development, _ = select_cases(cases, 'quick', 'safety', None, None, safety_stage='baseline')
    assert len(development) == 20
    assert not any(case.expected['split'] == 'holdout' for case in development)
    with pytest.raises(ValueError, match='Holdout'):
        select_cases(cases, 'quick', 'safety', ['safety-holdout-valid-action'], None)
