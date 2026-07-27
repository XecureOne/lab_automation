import boto3
import json

apigw = ""

API_NAME   = "GifFinderAPI"
STAGE_NAME = "dev"
PLAN_NAME  = "GifFinderBasicPlan"
KEY_NAME   = "GifFinderClient1Key"

def result(label, passed):
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}")
    return passed

def get_api_id():
    try:
        r  = apigw.get_rest_apis()
        for a in r.get('items', []):
            if a.get('name') == API_NAME:
                return a['id']
        result(f"API '{API_NAME}' found", False)
        return None
    except Exception as e:
        result(f"API lookup ERROR: {e}", False)
        return None

def test_caching_enabled(api_id):
    if not api_id:
        return result("Caching enabled on dev stage", False)
    try:
        stage = apigw.get_stage(restApiId=api_id, stageName=STAGE_NAME)
        cached = stage.get('cacheClusterEnabled', False)
        result("API caching enabled on dev stage", cached)
        if cached:
            size = stage.get('cacheClusterSize', '0')
            result(f"Cache cluster size configured ({size} GB)", bool(size))
        return cached
    except Exception as e:
        return result(f"Caching check ERROR: {e}", False)

def test_default_route_throttle(api_id):
    if not api_id:
        return result("Stage-level throttle configured", False)
    try:
        stage = apigw.get_stage(restApiId=api_id, stageName=STAGE_NAME)
        td    = stage.get('defaultRouteSettings', {})
        rate  = stage.get('methodSettings', {}).get(
                    '*/*', {}).get('throttlingRateLimit', 0)
        burst = stage.get('methodSettings', {}).get(
                    '*/*', {}).get('throttlingBurstLimit', 0)
        result(f"Stage method settings exist", bool(stage.get('methodSettings')))
    except Exception as e:
        result(f"Throttle check ERROR: {e}", False)

def test_usage_plan_exists():
    try:
        r     = apigw.get_usage_plans()
        plans = [p for p in r.get('items', [])
                 if p.get('name') == PLAN_NAME]
        result(f"Usage Plan '{PLAN_NAME}' exists", len(plans) > 0)
        return plans[0] if plans else None
    except Exception as e:
        result(f"Usage plan check ERROR: {e}", False)
        return None

def test_usage_plan_limits(plan):
    if not plan:
        return
    throttle = plan.get('throttle', {})
    quota    = plan.get('quota', {})
    result("Usage plan has throttle rate configured",
           throttle.get('rateLimit', 0) > 0)
    result("Usage plan has burst limit configured",
           throttle.get('burstLimit', 0) > 0)
    result("Usage plan has monthly quota configured",
           quota.get('limit', 0) > 0)

def test_api_key_exists():
    try:
        r    = apigw.get_api_keys(includeValues=False)
        keys = [k for k in r.get('items', [])
                if k.get('name') == KEY_NAME]
        result(f"API Key '{KEY_NAME}' exists", len(keys) > 0)
        if keys:
            enabled = keys[0].get('enabled', False)
            result("API Key is enabled", enabled)
    except Exception as e:
        result(f"API key check ERROR: {e}", False)

def test_usage_plan_has_api_stage(plan, api_id):
    if not plan or not api_id:
        return result("Usage plan associated with GifFinderAPI/dev", False)
    stages = plan.get('apiStages', [])
    linked = any(
        s.get('apiId') == api_id and s.get('stage') == STAGE_NAME
        for s in stages
    )
    return result(f"Usage plan linked to '{API_NAME}/{STAGE_NAME}'", linked)

def run_test_cases(credentials):
    global apigw
    apigw = boto3.client('apigateway',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    print("=" * 60)
    print("LAB 21 VALIDATION: API Gateway Caching and Throttling")
    print("=" * 60)
    api_id = get_api_id()
    result("REST API found", api_id is not None)
    test_caching_enabled(api_id)
    test_default_route_throttle(api_id)
    plan = test_usage_plan_exists()
    test_usage_plan_limits(plan)
    test_api_key_exists()
    test_usage_plan_has_api_stage(plan, api_id)
    print()
    print("NOTE: Throttle behavior (429 responses) must be tested manually.")
    print("=" * 60)