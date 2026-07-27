import boto3
import json

lam = ""
sqs = ""
iam = ""

FUNCTION_NAME = "GeneralPurposeFunction"
ROLE_NAME     = "general-lambda-role"
DLQ_NAME      = "lambda-dlq"

def result(label, passed):
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}")
    return passed

def test_function_exists():
    try:
        r   = lam.get_function(FunctionName=FUNCTION_NAME)
        cfg = r['Configuration']
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
    result("Runtime = python3.12",    cfg.get('Runtime') == 'python3.12')
    result("Timeout ≥ 30 seconds",    cfg.get('Timeout', 0) >= 30)
    result("Memory = 128 MB",         cfg.get('MemorySize') == 128)
    result("Uses general-lambda-role", ROLE_NAME in cfg.get('Role', ''))
    env = cfg.get('Environment', {}).get('Variables', {})
    result("Env var APP_ENV set",
           env.get('APP_ENV') == 'development')
    result("Env var LOG_LEVEL set",   bool(env.get('LOG_LEVEL')))

def test_dlq_configured(cfg):
    if not cfg:
        return result("DLQ configured on function", False)
    dlq = cfg.get('DeadLetterConfig', {})
    arn = dlq.get('TargetArn', '')
    return result(f"DLQ ARN configured (contains '{DLQ_NAME}')",
                  DLQ_NAME in arn)

def test_reserved_concurrency():
    try:
        r = lam.get_function_concurrency(FunctionName=FUNCTION_NAME)
        rc = r.get('ReservedConcurrentExecutions')
        return result(f"Reserved concurrency = 5 (got {rc})", rc == 5)
    except Exception as e:
        return result(f"Concurrency check ERROR: {e}", False)

def test_versions_exist():
    try:
        r     = lam.list_versions_by_function(FunctionName=FUNCTION_NAME)
        vers  = [v for v in r.get('Versions', [])
                 if v.get('Version') != '$LATEST']
        return result(f"At least 1 published version exists "
                      f"(found {len(vers)})", len(vers) >= 1)
    except Exception as e:
        return result(f"Version check ERROR: {e}", False)

def test_alias_exists():
    try:
        a = lam.get_alias(FunctionName=FUNCTION_NAME, Name='production')
        result("Alias 'production' exists", True)
        result("Alias points to version 1",
               a.get('FunctionVersion') == '1')
    except lam.exceptions.ResourceNotFoundException:
        result("Alias 'production' exists", False)
    except Exception as e:
        result(f"Alias check ERROR: {e}", False)

def test_dlq_queue_exists():
    try:
        sqs.get_queue_url(QueueName=DLQ_NAME)
        return result(f"DLQ SQS queue '{DLQ_NAME}' exists", True)
    except sqs.exceptions.QueueDoesNotExist:
        return result(f"DLQ SQS queue '{DLQ_NAME}' exists", False)
    except Exception as e:
        return result(f"DLQ queue check ERROR: {e}", False)

def test_invoke_function():
    try:
        payload = json.dumps({
            "action": "uppercase",
            "data":   {"text": "hello world"}
        }).encode()
        r      = lam.invoke(FunctionName=FUNCTION_NAME,
                            InvocationType='RequestResponse',
                            Payload=payload)
        body   = json.loads(r['Payload'].read())
        result("Function invocation succeeds (no FunctionError)",
               r.get('FunctionError') is None)
        result("Response has statusCode 200",
               body.get('statusCode') == 200)
        result("Uppercase action returns 'HELLO WORLD'",
               body.get('result') == 'HELLO WORLD')
    except Exception as e:
        result(f"Invocation ERROR: {e}", False)

def run_test_cases(credentials):
    global lam,sqs,iam
    lam = boto3.client('lambda',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"])
    sqs = boto3.client('sqs',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    iam = boto3.client('iam',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    print("=" * 60)
    print("LAB 22 VALIDATION: General-Purpose Lambda Function")
    print("=" * 60)
    cfg = test_function_exists()
    test_function_config(cfg)
    test_dlq_configured(cfg)
    test_reserved_concurrency()
    test_versions_exist()
    test_alias_exists()
    test_dlq_queue_exists()
    test_invoke_function()
    print("=" * 60)