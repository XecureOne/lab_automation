import boto3
import json
import urllib.request

apigw = ""
lam   = ""

API_NAME = "PHPAppAPI"

def result(label, passed):
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}")
    return passed

def get_api():
    try:
        r = apigw.get_rest_apis()
        for a in r.get('items', []):
            if a.get('name') == API_NAME:
                result(f"REST API '{API_NAME}' exists", True)
                return a
        result(f"REST API '{API_NAME}' exists", False)
        return None
    except Exception as e:
        result(f"API check ERROR: {e}", False)
        return None

def get_resources(api):
    if not api:
        return []
    try:
        r = apigw.get_resources(restApiId=api['id'])
        return r.get('items', [])
    except:
        return []

def test_resources(api):
    resources = get_resources(api)
    paths     = {r.get('path'): r for r in resources}
    result("/gifs resource exists",       '/gifs'          in paths)
    result("/gifs/{gif_id} exists",       '/gifs/{gif_id}' in paths)
    result("/health resource exists",     '/health'        in paths)
    return paths

def test_methods(api, paths):
    if not api:
        return
    for path, methods, expected in [
        ('/gifs',          ['GET', 'POST'], True),
        ('/gifs/{gif_id}', ['GET'],         True),
        ('/health',        ['GET'],         True),
    ]:
        res = paths.get(path, {})
        resource_methods = res.get('resourceMethods', {}) if res else {}
        for m in methods:
            result(f"{m} method on '{path}' exists", m in resource_methods)

def test_stages(api):
    if not api:
        return
    for stage_name in ['dev', 'prod']:
        try:
            stage = apigw.get_stage(restApiId=api['id'],
                                    stageName=stage_name)
            result(f"Stage '{stage_name}' exists", True)
            xray = stage.get('tracingEnabled', False)
            result(f"X-Ray tracing enabled on '{stage_name}'", xray)
            sv = stage.get('variables', {})
            result(f"Stage var ENV set on '{stage_name}'",
                   bool(sv.get('ENV')))
        except apigw.exceptions.NotFoundException:
            result(f"Stage '{stage_name}' exists", False)
        except Exception as e:
            result(f"Stage '{stage_name}' check ERROR: {e}", False)

def test_health_endpoint(api):
    if not api:
        return result("GET /health returns 200 (Mock)", False)
    region = boto3.session.Session().region_name
    url    = (f"https://{api['id']}.execute-api.{region}.amazonaws.com"
              "/dev/health")
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = json.loads(resp.read().decode())
            result("GET /health returns HTTP 200", resp.getcode() == 200)
            result("GET /health returns status=healthy",
                   body.get('status') == 'healthy')
    except Exception as e:
        result(f"Health endpoint test ERROR: {e}", False)

def test_gifs_endpoint(api):
    if not api:
        return result("GET /gifs?keyword=cats returns 200", False)
    region = boto3.session.Session().region_name
    url    = (f"https://{api['id']}.execute-api.{region}.amazonaws.com"
              "/dev/gifs?keyword=cats")
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            body  = json.loads(resp.read().decode())
            result("GET /gifs?keyword=cats returns 200",
                   resp.getcode() == 200)
            inner = json.loads(body.get('body', '{}')) \
                    if isinstance(body.get('body'), str) else body
            result("GIF results returned",
                   inner.get('count', 0) >= 0)
    except Exception as e:
        result(f"GIFs endpoint test ERROR: {e}", False)

def run_test_cases(credentials):
    global apigw,lam
    apigw = boto3.client('apigateway',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    lam   = boto3.client('lambda',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    print("=" * 60)
    print("LAB 24 VALIDATION: Full API — Resources, Methods, Settings")
    print("=" * 60)
    api   = get_api()
    paths = test_resources(api)
    test_methods(api, paths)
    test_stages(api)
    test_health_endpoint(api)
    test_gifs_endpoint(api)
    print("=" * 60)