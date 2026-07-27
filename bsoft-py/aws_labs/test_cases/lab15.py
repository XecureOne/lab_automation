import boto3
import json

lam      = ""
iam      = ""
dynamodb = ""
logs     = ""

FUNCTION_NAME = "GifFinderSearch"
ROLE_NAME     = "giffinder-lambda-role"
TABLE_NAME    = "giffinder-gifs"

def result(label, passed):
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}")
    return passed

def test_function_exists():
    try:
        r = lam.get_function(FunctionName=FUNCTION_NAME)
        cfg = r.get('Configuration', {})
        result(f"Lambda function '{FUNCTION_NAME}' exists", True)
        return cfg
    except lam.exceptions.ResourceNotFoundException:
        result(f"Lambda function '{FUNCTION_NAME}' exists", False)
        return None
    except Exception as e:
        result(f"Function check ERROR: {e}", False)
        return None

def test_function_config(cfg):
    if not cfg:
        return
    result("Runtime = python3.12",
           cfg.get('Runtime', '') == 'python3.12')
    result("Timeout ≥ 10 seconds",
           cfg.get('Timeout', 0) >= 10)
    result("Memory ≥ 128 MB",
           cfg.get('MemorySize', 0) >= 128)
    result(f"Function uses role '{ROLE_NAME}'",
           ROLE_NAME in cfg.get('Role', ''))

def test_env_vars(cfg):
    if not cfg:
        return
    env = cfg.get('Environment', {}).get('Variables', {})
    result("Env var TABLE_NAME set",
           env.get('TABLE_NAME') == TABLE_NAME)
    result("Env var INDEX_NAME set",
           bool(env.get('INDEX_NAME')))

def test_lambda_role():
    try:
        r    = iam.get_role(RoleName=ROLE_NAME)
        role = r['Role']
        result(f"Lambda execution role '{ROLE_NAME}' exists", True)
        basic = iam.list_attached_role_policies(RoleName=ROLE_NAME)
        arns  = [p['PolicyArn'] for p in
                 basic.get('AttachedPolicies', [])]
        result("AWSLambdaBasicExecutionRole attached",
               'arn:aws:iam::aws:policy/service-role/'
               'AWSLambdaBasicExecutionRole' in arns)
        inline = iam.list_role_policies(RoleName=ROLE_NAME)
        result("Inline DynamoDB policy exists",
               len(inline.get('PolicyNames', [])) > 0)
    except iam.exceptions.NoSuchEntityException:
        result(f"Lambda execution role '{ROLE_NAME}' exists", False)
    except Exception as e:
        result(f"Lambda role check ERROR: {e}", False)

def test_dynamodb_table():
    try:
        r  = dynamodb.describe_table(TableName=TABLE_NAME)
        tb = r['Table']
        result(f"DynamoDB table '{TABLE_NAME}' exists",
               tb.get('TableStatus') == 'ACTIVE')
        gsis = tb.get('GlobalSecondaryIndexes', [])
        has_gsi = any(g['IndexName'] == 'keyword-index' for g in gsis)
        result("GSI 'keyword-index' exists on table", has_gsi)
    except dynamodb.exceptions.ResourceNotFoundException:
        result(f"DynamoDB table '{TABLE_NAME}' exists", False)
    except Exception as e:
        result(f"Table check ERROR: {e}", False)

def test_invoke_function():
    try:
        payload = json.dumps({"keyword": "cats"}).encode()
        r       = lam.invoke(
                      FunctionName=FUNCTION_NAME,
                      InvocationType='RequestResponse',
                      Payload=payload)
        status  = r.get('StatusCode', 0)
        body    = json.loads(r['Payload'].read())
        result(f"Lambda invocation returned HTTP {status}", status == 200)
        inner   = json.loads(body.get('body', '{}'))
        result("Response statusCode = 200",
               body.get('statusCode') == 200)
        count   = inner.get('count', -1)
        result(f"Search for 'cats' returns ≥ 1 result (got {count})",
               count >= 1)
    except Exception as e:
        result(f"Lambda invocation ERROR: {e}", False)

def test_cloudwatch_logs():
    log_group = f"/aws/lambda/{FUNCTION_NAME}"
    try:
        r      = logs.describe_log_groups(logGroupNamePrefix=log_group)
        groups = r.get('logGroups', [])
        return result(f"CloudWatch log group exists for Lambda", len(groups) > 0)
    except Exception as e:
        return result(f"CloudWatch log check ERROR: {e}", False)

def run_test_cases(credentials):
    global lam, iam, dynamodb, logs
    lam      = boto3.client('lambda',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    iam      = boto3.client('iam',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    dynamodb = boto3.client('dynamodb',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    logs     = boto3.client('logs',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    print("=" * 60)
    print("LAB 19 VALIDATION: GifFinder Lambda Function")
    print("=" * 60)
    cfg = test_function_exists()
    test_function_config(cfg)
    test_env_vars(cfg)
    test_lambda_role()
    test_dynamodb_table()
    test_invoke_function()
    test_cloudwatch_logs()
    print("=" * 60)