import boto3
import json
import urllib.request

apigw = ""
lam   = ""

API_NAME      = "GifFinderAPI"
FUNCTION_NAME = "GifFinderSearch"
STAGE_NAME    = "dev"

def result(label, passed):
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}")
    return passed

def get_api():
    try:
        r    = apigw.get_rest_apis()
        apis = [a for a in r.get('items', [])
                if a.get('name') == API_NAME]
        result(f"REST API '{API_NAME}' exists", len(apis) > 0)
        return apis[0] if apis else None
    except Exception as e:
        result(f"API check ERROR: {e}", False)
        return None

def test_search_resource(api):
    if not api:
        return result("/search resource exists", False), None
    try:
        r    = apigw.get_resources(restApiId=api['id'])
        items = r.get('items', [])
        search_res = [i for i in items
                      if i.get('pathPart') == 'search' or
                         i.get('path')     == '/search']
        result("/search resource exists on API", len(search_res) > 0)
        return len(search_res) > 0, search_res[0] if search_res else None
    except Exception as e:
        result(f"/search resource check ERROR: {e}", False)
        return False, None

def test_get_method(api, resource):
    if not api or not resource:
        return result("GET method on /search exists", False)
    try:
        apigw.get_method(
            restApiId=api['id'],
            resourceId=resource['id'],
            httpMethod='GET'
        )
        return result("GET method on /search exists", True)
    except apigw.exceptions.NotFoundException:
        return result("GET method on /search exists", False)
    except Exception as e:
        return result(f"GET method check ERROR: {e}", False)

def test_lambda_integration(api, resource):
    if not api or not resource:
        return result("GET /search integrates with Lambda", False)
    try:
        intg = apigw.get_integration(
                   restApiId=api['id'],
                   resourceId=resource['id'],
                   httpMethod='GET')
        i_type = intg.get('type', '')
        uri    = intg.get('uri', '')
        result("Integration type = AWS_PROXY (Lambda Proxy)",
               i_type == 'AWS_PROXY')
        return result(f"Integration URI references '{FUNCTION_NAME}'",
                      FUNCTION_NAME in uri)
    except Exception as e:
        return result(f"Integration check ERROR: {e}", False)

def test_stage_exists(api):
    if not api:
        return result(f"Stage '{STAGE_NAME}' exists", False), None
    try:
        stage = apigw.get_stage(
                    restApiId=api['id'],
                    stageName=STAGE_NAME)
        result(f"Stage '{STAGE_NAME}' exists", True)
        return True, stage
    except apigw.exceptions.NotFoundException:
        result(f"Stage '{STAGE_NAME}' exists", False)
        return False, None
    except Exception as e:
        result(f"Stage check ERROR: {e}", False)
        return False, None

def test_invoke_url(api):
    if not api:
        return result("Invoke URL accessible", False)
    region = boto3.session.Session().region_name
    url = (f"https://{api['id']}.execute-api.{region}.amazonaws.com"
           f"/{STAGE_NAME}/search?keyword=cats")
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            body  = json.loads(resp.read().decode())
            code  = resp.getcode()
            result(f"GET {url} returned HTTP 200", code == 200)
            inner = json.loads(body.get('body', '{}')) \
                    if isinstance(body.get('body'), str) else body
            count = inner.get('count', -1)
            return result(f"Search returns ≥ 1 GIF for 'cats' (got {count})",
                          count >= 1)
    except Exception as e:
        return result(f"Invoke URL test ERROR: {e}", False)

def test_lambda_permission():
    try:
        r   = lam.get_policy(FunctionName=FUNCTION_NAME)
        pol = json.loads(r['Policy'])
        apigw_stmt = any(
            stmt.get('Principal', {}).get('Service')
            == 'apigateway.amazonaws.com'
            for stmt in pol.get('Statement', [])
        )
        return result(
            "Lambda resource policy allows apigateway.amazonaws.com",
            apigw_stmt)
    except lam.exceptions.ResourceNotFoundException:
        return result("Lambda has resource policy for API Gateway", False)
    except Exception as e:
        return result(f"Lambda permission check ERROR: {e}", False)

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
    print("LAB 20 VALIDATION: API Gateway → Lambda Integration")
    print("=" * 60)
    api = get_api()
    ok, resource = test_search_resource(api)
    test_get_method(api, resource)
    test_lambda_integration(api, resource)
    test_stage_exists(api)
    test_invoke_url(api)
    test_lambda_permission()
    print("=" * 60)