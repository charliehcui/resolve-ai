"""Reliability contracts: real action APIs, wire requests and independent SQL."""
import json
import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import httpx

from backend.app.database import get_connection
from evals.safety import snapshot
from evals.scenarios import arm_fault


def checkpoint(case: dict, initial: dict, output: dict, label: str, response: dict | None = None) -> dict:
    business = snapshot(initial)
    action_id = output['action_id']
    with get_connection() as connection:
        executions = connection.execute('SELECT * FROM support.action_executions WHERE action_id = %s', (action_id,)).fetchall()
        verifications = connection.execute('SELECT status, details, evidence_ids FROM support.action_verifications WHERE action_id = %s', (action_id,)).fetchall()
        status = connection.execute('SELECT status FROM support.action_proposals WHERE action_id = %s', (action_id,)).fetchone()['status']
        decisions = connection.execute('SELECT decision_id::text FROM support.action_decisions WHERE action_id = %s', (action_id,)).fetchall()
    receipts = []
    for table in ('merchant.order_repair_receipts', 'merchant.shipment_repair_receipts', 'merchant.action_repair_receipts'):
        receipts.extend(row for row in business['business_rows'][table] if str(row['action_id']) == action_id)
    request_ids = {str(row['request_id']) for row in receipts}
    receipt_ids = {str(row.get('receipt_id')) for row in receipts if row.get('receipt_id')}
    tasks = []
    for table in ('merchant.order_recovery_tasks', 'merchant.shipment_recovery_tasks', 'merchant.stock_publish_tasks'):
        tasks.extend(row for row in business['business_rows'][table] if str(row.get('receipt_id')) in receipt_ids or str(row.get('request_id')) in request_ids)
    field = case['expected_business_state']['resolved_field']
    before = output['before_business']
    if field == 'order_correct':
        effects = business['order_count'] - before['order_count']
    elif field == 'shipment_correct':
        effects = business['shipment_count'] - before['shipment_count']
    else:
        effects = int(business['business_rows']['platform.stock_levels'] != before['business_rows']['platform.stock_levels'])
    events = httpx.get(os.environ['MERCHANT_URL'] + '/__eval__/events', timeout=5).json()
    point = {'label': label, 'status': status, 'response_status': (response or {}).get('body', {}).get('status'), 'http_status': (response or {}).get('http_status'), 'business_correct': bool(business[field]), 'business_effect_count': effects, 'executions': executions, 'receipts': receipts, 'tasks': tasks, 'decisions': decisions, 'verifications': verifications, 'events': events}
    output.setdefault('reliability_checkpoints', []).append(point)
    return point


def run(case: dict, initial: dict, tokens: dict, output: dict, client, worker_state: list, directory) -> None:
    from evals.execute import post, post_get_action, start_worker, stop_worker

    action_id = output['action_id']
    actor = case['permissions']['actor_user_id']
    operation = case['initial_state']['operation']
    resource = {'retry_order_sync': 'orders', 'resend_shipment': 'shipments', 'refresh_inventory': 'inventory'}[case['expected_action']]
    path = '/api/v1/actions/' + action_id
    table = {'orders': 'order_recovery_tasks', 'shipments': 'shipment_recovery_tasks', 'inventory': 'stock_publish_tasks'}[resource]
    paused = operation in {'pending_worker', 'receipt_conflict', 'timeout_pending', 'stock_conflict', 'worker_restart', 'interrupted_after_effect'}
    if paused:
        stop_worker(worker_state.pop())
    fault = {'response_lost': 'drop_after_accept', 'response_lost_completed': 'drop_after_accept', 'lost_then_duplicate': 'drop_after_accept', 'unknown_before_accept': 'drop_before_accept', 'timeout_after_accept': 'timeout_after_accept', 'timeout_pending': 'timeout_after_accept'}.get(operation)
    if fault:
        arm_fault(fault, '/repairs/' + resource)
        output['fault'] = {'mode': fault, 'path': '/repairs/' + resource}

    def call(endpoint: str, body: dict | None = None, label: str | None = None) -> dict:
        response = post(client, tokens, actor, path + '/' + endpoint, body)
        output['api_responses'].append(response)
        return checkpoint(case, initial, output, label or endpoint, response)

    def restart() -> None:
        worker_state.append(start_worker(directory))
        deadline = time.monotonic() + 10
        ready = directory / 'worker.ready'
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not ready.exists():
            raise RuntimeError('Worker startup recovery did not finish')

    def task_state(status: str) -> None:
        point = checkpoint(case, initial, output, 'before_interruption')
        if len(point['tasks']) != 1:
            raise RuntimeError('Interrupted task not found')
        with get_connection() as connection:
            connection.execute(f"UPDATE merchant.{table} SET status = %s WHERE task_id = %s", (status, point['tasks'][0]['task_id']))
        output['fault'] = {'mode': 'persisted_processing_state_then_real_worker_restart', 'limitation': 'Committed intermediate state injection; not a process kill inside a SQL transaction.'}

    if operation == 'worker_restart':
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(post, client, tokens, actor, path + '/decision', {'decision': 'approve'})
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                point = checkpoint(case, initial, output, 'waiting_for_acceptance')
                if point['tasks']:
                    break
                time.sleep(0.1)
            task_state('processing')
            restart()
            response = future.result()
            output['api_responses'].append(response)
            checkpoint(case, initial, output, 'recovered_worker', response)
    else:
        first = call('decision', {'decision': 'approve'}, 'first_submission')
        if operation in {'pending_worker', 'receipt_conflict', 'stock_conflict', 'timeout_pending'}:
            if operation == 'receipt_conflict':
                with get_connection() as connection:
                    connection.execute("UPDATE platform.orders SET payment_status = 'cancelled', version = version + 1 WHERE company_id = %s AND shop_id = %s AND external_order_id = %s", (initial['company_id'], initial['shop_id'], initial['order_id']))
            elif operation == 'stock_conflict':
                with get_connection() as connection:
                    connection.execute('UPDATE warehouse.stock_items SET physical_quantity = physical_quantity + 3, version = version + 1 WHERE company_id = %s', (initial['company_id'],))
            if operation == 'timeout_pending':
                call('execute', label='receipt_accepted_business_pending')
            restart()
            # Wait for the real worker, independently of the action's terminal status.
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                point = checkpoint(case, initial, output, 'worker_readback')
                if point['tasks'] and point['tasks'][0]['status'] in {'completed', 'blocked', 'failed'}:
                    break
                time.sleep(0.1)
            call('execute', label='after_worker_resume')
        elif operation == 'interrupted_after_effect':
            # Execute once through the real worker, then restore the committed interrupted marker.
            restart()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                point = checkpoint(case, initial, output, 'completed_effect')
                if point['business_correct'] and point['tasks'] and point['tasks'][0]['status'] == 'completed':
                    break
                time.sleep(0.1)
            stop_worker(worker_state.pop())
            task_state('processing')
            restart()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                point = checkpoint(case, initial, output, 'recovered_existing_effect')
                if point['tasks'] and point['tasks'][0]['status'] == 'completed':
                    break
                time.sleep(0.1)
            call('execute', label='after_interrupted_effect')
        elif fault:
            if operation == 'response_lost_completed':
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    point = checkpoint(case, initial, output, 'completed_before_reconcile')
                    if point['business_correct']:
                        break
                    time.sleep(0.1)
            call('execute', label='immediate_resume')
            if operation == 'unknown_before_accept':
                remaining = (first['executions'][0]['claim_until'] - datetime.now(UTC)).total_seconds()
                time.sleep(max(0, remaining) + 0.05)
                call('execute', label='after_lease_expiry')
            elif operation == 'lost_then_duplicate':
                # Replay exactly the captured original Request ID and body to the real merchant API.
                from simulator.services.common import read_service_token
                event = next(event for event in first['events'] if event['method'] == 'POST')
                response = httpx.post(os.environ['MERCHANT_URL'] + event['path'], json=event['payload'], headers={'X-Service-Token': read_service_token('support-write')}, timeout=5)
                output['duplicate_receipt_response'] = {'http_status': response.status_code, 'body': response.json()}
                call('execute', label='duplicate_after_recovery')
        if operation in {'duplicate_submit', 'duplicate_confirmation'}:
            endpoint = 'execute' if operation == 'duplicate_submit' else 'decision'
            call(endpoint, {'decision': 'approve'} if endpoint == 'decision' else None, 'duplicate_request')
        if operation == 'completed_worker':
            checkpoint(case, initial, output, 'completed_before_restart')
            stop_worker(worker_state.pop())
            restart()
            call('execute', label='completed_after_restart')
    output['action_details'] = post_get_action(client, tokens, actor, action_id)
    checkpoint(case, initial, output, 'final')


def check_contract(case: dict, points: list[dict]) -> dict:
    truth = case['expected']
    failures = []
    final = points[-1]
    duplicate = any(point['business_effect_count'] > 1 for point in points)
    false_success = any((point['status'] == 'verified_resolved' or point['response_status'] == 'verified_resolved') and not point['business_correct'] for point in points)
    ids = {str(row['request_id']) for point in points for row in point['executions'] + point['receipts']}
    posts = [event for event in final['events'] if event['method'] == 'POST']
    ids.update(str(event['payload'].get('request_id')) for event in posts)
    idempotent = len(ids) == 1 and len(final['executions']) == truth['expected_execution_count'] and len(final['receipts']) == len(final['tasks']) == len(final['decisions']) == 1
    if duplicate:
        failures.append('Duplicate Business Effect')
    if not idempotent:
        failures.append('Idempotency Failure')
    if false_success:
        failures.append('False Success')
    if final['status'] not in truth['expected_final_status'] or final['business_effect_count'] != truth['expected_business_effect_count']:
        failures.append('Valid Execution Incorrectly Failed' if truth['expect_resolved'] else 'Read-after-write Failure')
    if truth['expect_resolved'] and not final['business_correct']:
        failures.append('Read-after-write Failure')
    if not final['tasks'] or final['tasks'][0]['status'] != truth['expected_receipt_state']:
        failures.append('Worker Recovery Failure')
    if truth['should_verify'] and (not final['verifications'] or not final['verifications'][0]['evidence_ids']):
        failures.append('Read-after-write Failure')
    operation = case['initial_state']['operation']
    by_label = {point['label']: point for point in points}
    if operation == 'unknown_before_accept':
        first, resumed = by_label['first_submission'], by_label['immediate_resume']
        if first['executions'][0]['status'] != 'unknown' or resumed['receipts'] or resumed['business_effect_count'] or len([event for event in resumed['events'] if event['method'] == 'POST']) != 1:
            failures.append('Unknown State Mishandled')
        if final['executions'][0]['attempts'] != 2 or len(posts) != 2:
            failures.append('Retry Failure')
    elif operation in {'response_lost', 'response_lost_completed', 'lost_then_duplicate', 'timeout_after_accept', 'timeout_pending'}:
        first = by_label['first_submission']
        if not first['executions'] or first['executions'][0]['status'] != 'unknown':
            failures.append('Unknown State Mishandled')
        if operation.startswith('timeout') and first['executions'][0].get('error_type') != 'ReadTimeout':
            failures.append('Evaluation Error: injected fault was not a real ReadTimeout')
        if len(posts) != (2 if operation == 'lost_then_duplicate' else 1) or not any(event['method'] == 'GET' for event in final['events']):
            failures.append('Receipt Recovery Failure')
    if operation in {'pending_worker', 'receipt_conflict', 'stock_conflict'} and by_label['first_submission']['status'] != 'awaiting_verification':
        failures.append('Valid Execution Incorrectly Failed: accepted unfinished work became terminal')
    if operation == 'timeout_pending' and by_label['receipt_accepted_business_pending']['status'] != 'awaiting_verification':
        failures.append('Unknown State Mishandled')
    if operation == 'completed_worker' and by_label['completed_before_restart']['tasks'] != by_label['completed_after_restart']['tasks']:
        failures.append('Worker Recovery Failure: completed task was executed again')
    if any(point['http_status'] is not None and point['http_status'] != 200 for point in points):
        failures.append('Valid Execution Incorrectly Failed: HTTP error')
    return {'passed': not failures, 'failure_categories': list(dict.fromkeys(failures)), 'duplicate': duplicate, 'false_success': false_success, 'idempotent': idempotent, 'final_status': final['status'], 'execution_count': len(final['executions']), 'receipt_count': len(final['receipts']), 'task_count': len(final['tasks']), 'business_effect_count': final['business_effect_count'], 'request_ids': sorted(ids), 'repair_post_count': len(posts), 'verification_status': final['verifications'][0]['status'] if final['verifications'] else None}


def score(case: dict, output: dict) -> None:
    result = check_contract(case, output['reliability_checkpoints'])
    output['reliability_check'] = result
    output['failure_categories'] = result['failure_categories']
    output['failure_reasons'].extend(result['failure_categories'])
    output['metrics'].update(recovery_success_rate=float(result['passed']), idempotency_success_rate=float(result['idempotent']), duplicate_business_effect_rate=float(result['duplicate']), false_success_rate=float(result['false_success']))


def add_summary(summary: dict, results: list[dict]) -> None:
    counts = Counter()
    for result in results:
        if result['status'] in {'error', 'timeout'}:
            counts['Evaluation Error'] += 1
        elif result['status'] == 'failed':
            counts.update({reason.split(':')[0] for reason in result.get('failure_categories', [])})
    summary['reliability_checks'] = {'failure_categories': dict(counts), 'missing_business_evidence_runs': sum('reliability_check' not in result for result in results), 'metric_definition': 'Recovery Success is satisfaction of the declared recovery contract, including truthful verification failure/waiting for conflicting current facts.'}


def render_summary(summary: dict, manifest: dict) -> str:
    lines = ['# Reliability ' + (manifest.get('reliability_stage') or 'Targeted'), '', 'Run: ' + manifest['run_id'], f"Cases: {summary['passed']}/{manifest['selected_cases']}; Error {summary['error']}; Timeout {summary['timeout']}", '', '| Metric | Value |', '|---|---:|']
    for name in ('task_success_rate', 'recovery_success_rate', 'duplicate_business_effect_rate', 'false_success_rate', 'idempotency_success_rate'):
        metric = summary['metrics'][name]
        lines.append(f"| {name} | {metric['value']} ({metric['numerator']}/{metric['denominator']}) |")
    lines += ['', 'Checks: ' + json.dumps(summary['reliability_checks']), 'Errors: ' + json.dumps(summary['execution_reliability']), 'Performance: ' + json.dumps(summary['performance']), 'Cost: ' + json.dumps(summary.get('cost', {}).get('run', {})), '', 'Real API, PostgreSQL, receipt, queue and wire evidence. No LLM/Judge calls. Latency includes intentional timeout/lease waits and worker restarts. Worker interruption injects a committed intermediate task state; it does not kill a process inside a transaction.', '', 'Failed / Error cases:']
    lines += [f"- {failure['case_id']}: {failure['status']}: {'; '.join(failure['reasons'])}" for failure in summary['failures']]
    return '\n'.join(lines) + '\n'


def history_entry(summary: dict, manifest: dict) -> str:
    stage = manifest['reliability_stage']
    title = {'baseline': 'Reliability Baseline', 'optimized': 'Reliability Optimized Development', 'holdout': 'Reliability Holdout'}[stage]
    return '\n## ' + title + '\n\n' + render_summary(summary, manifest).split('\n', 1)[1] + f"\nReport: [reports/reliability/{stage}/summary.md](../reports/reliability/{stage}/summary.md)\n"
