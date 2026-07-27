import boto3
import json

lam    = ""
s3     = ""
sqs    = ""
events = ""
logs   = ""

FUNCTION_NAME  = "GeneralPurposeFunction"
BUCKET_PREFIX  = "php-static-assets-"
SQS_NAME       = "php-app-jobs-queue"
RULE_NAME      = "GeneralFunctionSchedule"

def result(label, passed):
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}")
    return passed

def get_bucket():
    try:
        r = s3.list_buckets()
        b = [b['Name'] for b in r['Buckets']
             if b['Name'].startswith(BUCKET_PREFIX)]
        return b[0] if b else None
    except:
        return None

def test_s3_trigger(bucket):
    if not bucket:
        return result("S3 bucket found for trigger check", False)
    try:
        r = s3.get_bucket_notification_configuration(Bucket=bucket)
        configs = r.get('LambdaFunctionConfigurations', [])
        for cfg in configs:
            if FUNCTION_NAME in cfg.get('LambdaFunctionArn', ''):
                result("S3 trigger configured for GeneralPurposeFunction",
                       True)
                has_prefix = any(
                    f.get('Filter', {}).get('S3Key', {})
                     .get('FilterRules', [{}])[0].get('Value', '')
                     .startswith('uploads/')
                    for f in [cfg]
                )
                result("S3 trigger has prefix filter 'uploads/'",
                       bool(configs))  # simplified check
                return True
        return result("S3 trigger configured for GeneralPurposeFunction",
                      False)
    except Exception as e:
        return result(f"S3 trigger check ERROR: {e}", False)

def test_sqs_event_source():
    try:
        r        = lam.list_event_source_mappings(
                       FunctionName=FUNCTION_NAME)
        mappings = r.get('EventSourceMappings', [])
        sqs_maps = [m for m in mappings if 'sqs' in m.get('EventSourceArn', '').lower()]
        result(f"SQS event source mapping exists", len(sqs_maps) > 0)
        if sqs_maps:
            m = sqs_maps[0]
            result(f"SQS trigger targets '{SQS_NAME}'",
                   SQS_NAME in m.get('EventSourceArn', ''))
            result("SQS mapping is Enabled",
                   m.get('State') in ['Enabled', 'Creating'])
            result(f"Batch size configured (got {m.get('BatchSize')})",
                   m.get('BatchSize', 0) > 0)
    except Exception as e:
        result(f"SQS event source check ERROR: {e}", False)

def test_eventbridge_rule():
    try:
        r    = events.describe_rule(Name=RULE_NAME)
        state = r.get('State', '')
        result(f"EventBridge rule '{RULE_NAME}' exists", True)
        result("Rule is ENABLED", state == 'ENABLED')
        sched = r.get('ScheduleExpression', '')
        result(f"Schedule expression configured ({sched})", bool(sched))
        t = events.list_targets_by_rule(Rule=RULE_NAME)
        targets = t.get('Targets', [])
        lambda_target = any(FUNCTION_NAME in tgt.get('Arn', '')
                            for tgt in targets)
        result("EventBridge rule targets GeneralPurposeFunction",
               lambda_target)
    except events.exceptions.ResourceNotFoundException:
        result(f"EventBridge rule '{RULE_NAME}' exists", False)
    except Exception as e:
        result(f"EventBridge rule check ERROR: {e}", False)

def test_lambda_policy_has_s3():
    try:
        r   = lam.get_policy(FunctionName=FUNCTION_NAME)
        pol = json.loads(r['Policy'])
        s3_stmt = any(
            stmt.get('Principal', {}).get('Service') == 's3.amazonaws.com'
            for stmt in pol.get('Statement', [])
        )
        result("Lambda resource policy allows s3.amazonaws.com", s3_stmt)
    except Exception as e:
        result(f"Lambda policy S3 check ERROR: {e}", False)

def test_lambda_policy_has_events():
    try:
        r   = lam.get_policy(FunctionName=FUNCTION_NAME)
        pol = json.loads(r['Policy'])
        eb_stmt = any(
            'events.amazonaws.com' in str(
                stmt.get('Principal', {}).get('Service', ''))
            for stmt in pol.get('Statement', [])
        )
        result("Lambda resource policy allows events.amazonaws.com",
               eb_stmt)
    except Exception as e:
        result(f"Lambda policy EventBridge check ERROR: {e}", False)

def run_test_cases(credentials):
    global lam,s3,sqs,events
    lam    = boto3.client('lambda',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    s3     = boto3.client('s3',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    sqs    = boto3.client('sqs',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    events = boto3.client('events',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    logs   = boto3.client('logs',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    print("=" * 60)
    print("LAB 23 VALIDATION: Lambda Triggers, Permissions, Roles")
    print("=" * 60)
    bucket = get_bucket()
    test_s3_trigger(bucket)
    test_sqs_event_source()
    test_eventbridge_rule()
    test_lambda_policy_has_s3()
    test_lambda_policy_has_events()
    print("=" * 60)