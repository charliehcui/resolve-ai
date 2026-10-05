"""Synthetic evaluator checks; these do not execute Holdout or enter benchmark scores."""
from copy import deepcopy

import pytest

from backend.app.config import PROJECT_ROOT
from evals.dataset import load_cases, smoke_cases
from evals.evaluator import summarize
from evals.reliability import check_contract
from evals.run import empty_result, select_cases


def normal_contract():
    case = next(case for case in smoke_cases() if case['case_id'] == 'recover-normal')
    point = {'label': 'final', 'status': 'verified_resolved', 'response_status': 'verified_resolved', 'http_status': 200, 'business_correct': True, 'business_effect_count': 1, 'executions': [{'request_id': 'stable'}], 'receipts': [{'request_id': 'stable'}], 'tasks': [{'status': 'completed'}], 'decisions': [{}], 'verifications': [{'status': 'verified_resolved', 'evidence_ids': ['real-readback']}], 'events': [{'method': 'POST', 'payload': {'request_id': 'stable'}}]}
    return case, point


def test_duplicate_execution_or_changed_request_id_cannot_hide_behind_one_business_effect():
    case, point = normal_contract()
    assert check_contract(case, [point])['passed']
    point['executions'].append({'request_id': 'different'})
    result = check_contract(case, [point])
    assert not result['idempotent'] and not result['passed']
    assert not result['duplicate']


def test_intermediate_false_success_is_retained_even_when_final_business_state_is_correct():
    case, final = normal_contract()
    earlier = deepcopy(final)
    earlier.update(label='premature', business_correct=False, business_effect_count=0)
    result = check_contract(case, [earlier, final])
    assert result['false_success'] and not result['passed']


def test_early_unknown_retry_is_detected_even_with_stable_id_and_single_effect():
    case, final = normal_contract()
    case['initial_state']['operation'] = 'unknown_before_accept'
    first = deepcopy(final)
    first.update(label='first_submission', status='executing', response_status='executing', business_correct=False, business_effect_count=0, receipts=[], tasks=[])
    first['executions'][0]['status'] = 'unknown'
    final['executions'][0]['attempts'] = 2
    final['events'] *= 2
    resumed = deepcopy(final)
    resumed['label'] = 'immediate_resume'
    result = check_contract(case, [first, resumed, final])
    assert 'Unknown State Mishandled' in result['failure_categories']
    assert result['idempotent'] and not result['duplicate']


def test_reliability_split_and_holdout_selection_guard():
    cases = load_cases(PROJECT_ROOT / 'evals/data/smoke.jsonl')
    selected, _ = select_cases(cases, 'quick', 'reliability', None, None, reliability_stage='baseline')
    assert len(selected) == 13 and all(case.expected['split'] == 'development' for case in selected)
    with pytest.raises(ValueError, match='Holdout'):
        select_cases(cases, 'quick', 'reliability', ['reliability-holdout-timeout-unknown'], None)


def test_reliability_errors_remain_in_success_denominator_with_unknown_adverse_rates():
    case, _ = normal_contract()
    passed = empty_result(case, 'current', 1)
    passed.update(status='passed', metrics={'recovery_success_rate': 1, 'idempotency_success_rate': 1, 'duplicate_business_effect_rate': 0, 'false_success_rate': 0})
    error = empty_result(case, 'current', 1)
    summary = summarize([passed, error])
    assert summary['metrics']['task_success_rate']['value'] == 0.5
    assert summary['metrics']['recovery_success_rate']['value'] == 0.5
    assert summary['metrics']['idempotency_success_rate']['value'] == 0.5
    assert summary['metrics']['false_success_rate']['value'] is None
