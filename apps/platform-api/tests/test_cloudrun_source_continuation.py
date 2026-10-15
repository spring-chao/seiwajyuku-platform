import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from test_cloudrun_release_network_guard import make_controller, source_input
import deploy_cloudrun_api as release


def fixture():
    controller, api, probe, source = make_controller()
    api.stable['Port'] = api.candidate['Port'] = 8000
    token = 'source-resume-probe-' + 'x' * 32
    env = json.loads(api.stable['EnvParams'])
    api.candidate['EnvParams'] = json.dumps({**env, 'DEPLOYMENT_CANDIDATE_PROBE_ID': token})
    task = {'Id': 77, 'Status': 'running', 'VersionName': api.candidate_name, 'ReleaseType': 'GRAY',
            'Steps': [{'Name': 'CreateVersion', 'Status': 'finished',
                       'StartTime': (datetime.now(UTC) + timedelta(hours=8, minutes=-10)).strftime('%Y-%m-%d %H:%M:%S')}]}
    api.describe_manage_task = lambda _: dict(task)
    api.release_order = {'Id': 55, 'TrafficType': 'FLOW', 'IsReleasing': True, 'ReleaseStatus': 'open',
                         'CurrentVersion': {'VersionName': api.stable['Name']},
                         'ReleaseVersion': {'VersionName': api.candidate_name}}
    continuation = release.RepairContinuation(api.candidate_name, 77, 55)
    proof = {'id': 'sj_result_' + hashlib.sha256(token.encode()).hexdigest(), 'status': 'passed',
             'commit_sha': 'b' * 40, 'loopback': '127.0.0.1:8000', 'http_requests': 23, 'database_health_passed': 20}
    original = api.search_cls_logs
    def logs(query, start, end):
        if proof['id'] in query:
            return {'Results': [{'Content': {'container_name': api.candidate_name,
                    'message': 'CANDIDATE_PROBE_RESULT ' + json.dumps(proof)}}]}
        return original(query, start, end)
    api.search_cls_logs = logs
    return controller, api, source, continuation, task, proof


def build(controller, api, continuation, **overrides):
    return controller.build_plan(source_input(candidate_verification='startup-loopback', **overrides),
        continuation=continuation, source_resume=True, expected_stable_revision=api.stable['Name'])


def test_resume_reuses_same_source_candidate_and_original_probe_window():
    c, api, source, continuation, _, _ = fixture()
    plan = build(c, api, continuation)
    assert plan.startup_probe_start < datetime.now(UTC) - timedelta(minutes=9)
    assert plan.startup_probe_id not in str(plan.safe_dict())
    result = c.execute(plan, promotion='full')
    assert result['state'] == 'VERIFIED'
    assert source.calls == 0 and api.update_specs == [] and api.targeted_calls == []
    assert api.flow_calls == [5, 100]
    assert api.events.index('search_cls_logs') < api.events.index('release_flow:5')


@pytest.mark.parametrize('change', ['vpc', 'env', 'probe_reused', 'task', 'order', 'stale_creation', 'ci', 'new_flag', 'missing_vpc'])
def test_resume_rejects_baseline_or_authorization_drift_before_write(change):
    c, api, source, continuation, task, _ = fixture()
    overrides = {}
    if change == 'vpc': api.candidate['VpcConf']['SubnetId'] = 'other'
    if change == 'env': api.candidate['EnvParams'] = json.dumps({'DEPLOYMENT_CANDIDATE_PROBE_ID': 'x' * 40})
    if change == 'probe_reused': api.stable['EnvParams'] = api.candidate['EnvParams']
    if change == 'task': task['Id'] += 1
    if change == 'order': api.release_order['Id'] += 1
    if change == 'stale_creation': task['Steps'][0]['StartTime'] = '2000-01-01 00:00:00'
    if change == 'ci': overrides['ci_provenance_verified'] = False
    if change == 'new_flag': overrides['requested_env_changes'] = {'IDENTITY_ADMIN_WRITES_ENABLED': 'true'}
    if change == 'missing_vpc': api.stable['VpcConf']['VpcId'] = ''
    with pytest.raises(release.ReleaseFailure):
        build(c, api, continuation, **overrides)
    assert source.calls == 0 and api.update_specs == [] and api.flow_calls == []


@pytest.mark.parametrize('change', ['commit', 'db', 'identity'])
def test_resume_still_requires_candidate_build_database_and_log_identity(change):
    c, api, _, continuation, _, proof = fixture()
    plan = build(c, api, continuation)
    if change == 'commit': proof['commit_sha'] = '0' * 40
    if change == 'db': proof['database_health_passed'] = 19
    if change == 'identity': api.log_rows = [{'Content': {'container_name': api.stable['Name']}}]
    with pytest.raises(release.ReleaseFailure):
        c.execute(plan, promotion='full')
    assert api.flow_calls == []
