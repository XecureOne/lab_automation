import boto3
import json

iam     = ""
ec2     = ""
dynamodb = ""

ROLE_NAME        = "ec2-dynamodb-role"
TABLE_NAME       = "php-app-users"
INSTANCE_NAME    = "php-web-server"
INLINE_POLICY    = "php-dynamodb-table-policy"

REQUIRED_ACTIONS = [
    "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
    "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan",
    "dynamodb:BatchGetItem", "dynamodb:BatchWriteItem", "dynamodb:DescribeTable"
]

def result(label, passed):
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}")
    return passed

# ── Role ────────────────────────────────────────────────────────────────────
def test_role_exists():
    try:
        r = iam.get_role(RoleName=ROLE_NAME)
        result(f"IAM Role '{ROLE_NAME}' exists", True)
        return r['Role']
    except iam.exceptions.NoSuchEntityException:
        result(f"IAM Role '{ROLE_NAME}' exists", False)
        return None
    except Exception as e:
        result(f"Role check ERROR: {e}", False)
        return None

def test_trust_policy(role):
    if not role:
        return result("Trust policy allows EC2 service", False)
    try:
        import urllib.parse
        doc = role.get('AssumeRolePolicyDocument', {})
        if isinstance(doc, str):
            doc = json.loads(urllib.parse.unquote(doc))
        ec2_trusted = any(
            'ec2.amazonaws.com' in (
                s.get('Principal', {}).get('Service', [])
                if isinstance(s.get('Principal', {}).get('Service'), list)
                else [s.get('Principal', {}).get('Service', '')]
            )
            for s in doc.get('Statement', [])
        )
        return result("Trust policy allows ec2.amazonaws.com", ec2_trusted)
    except Exception as e:
        return result(f"Trust policy check ERROR: {e}", False)

def test_inline_policy_exists():
    try:
        iam.get_role_policy(RoleName=ROLE_NAME, PolicyName=INLINE_POLICY)
        return result(f"Inline policy '{INLINE_POLICY}' exists on role", True)
    except iam.exceptions.NoSuchEntityException:
        return result(f"Inline policy '{INLINE_POLICY}' exists on role", False)
    except Exception as e:
        return result(f"Inline policy check ERROR: {e}", False)

def test_policy_scoped_to_table():
    try:
        r   = iam.get_role_policy(RoleName=ROLE_NAME, PolicyName=INLINE_POLICY)
        doc = r['PolicyDocument']
        if isinstance(doc, str):
            doc = json.loads(doc)
        resources = []
        for stmt in doc.get('Statement', []):
            res = stmt.get('Resource', [])
            resources.extend(res if isinstance(res, list) else [res])
        scoped = all(TABLE_NAME in r for r in resources if r != '*')
        wildcard = any(r == '*' for r in resources)
        result(f"Policy resources include table name '{TABLE_NAME}'",
               any(TABLE_NAME in r for r in resources))
        return result("Policy does NOT use wildcard '*' resource",
                      not wildcard)
    except Exception as e:
        return result(f"Policy scope check ERROR: {e}", False)

def test_required_actions_present():
    try:
        r   = iam.get_role_policy(RoleName=ROLE_NAME, PolicyName=INLINE_POLICY)
        doc = r['PolicyDocument']
        if isinstance(doc, str):
            doc = json.loads(doc)
        granted = []
        for stmt in doc.get('Statement', []):
            if stmt.get('Effect') == 'Allow':
                actions = stmt.get('Action', [])
                granted.extend(actions if isinstance(actions, list) else [actions])
        for action in REQUIRED_ACTIONS:
            result(f"  Policy grants '{action}'", action in granted)
    except Exception as e:
        result(f"Action check ERROR: {e}", False)

# ── DynamoDB ─────────────────────────────────────────────────────────────────
def test_table_exists():
    try:
        r = dynamodb.describe_table(TableName=TABLE_NAME)
        status = r['Table']['TableStatus']
        return result(f"DynamoDB table '{TABLE_NAME}' exists (status={status})",
                      status == 'ACTIVE'), r['Table']
    except dynamodb.exceptions.ResourceNotFoundException:
        return result(f"DynamoDB table '{TABLE_NAME}' exists", False), None
    except Exception as e:
        return result(f"Table check ERROR: {e}", False), None

def test_table_schema(table):
    if not table:
        return result("Table has correct partition key 'userId'", False)
    keys = {k['AttributeName']: k['KeyType']
            for k in table.get('KeySchema', [])}
    result("Table partition key is 'userId'",
           keys.get('userId') == 'HASH')
    result("Table sort key is 'email'",
           keys.get('email') == 'RANGE')

def test_table_has_items():
    try:
        r     = dynamodb.scan(TableName=TABLE_NAME, Select='COUNT')
        count = r.get('Count', 0)
        return result(f"Table has at least 2 test items (found {count})",
                      count >= 2)
    except Exception as e:
        return result(f"Item count check ERROR: {e}", False)

# ── Instance ─────────────────────────────────────────────────────────────────
def test_profile_on_instance():
    try:
        r = ec2.describe_instances(Filters=[
            {'Name': 'tag:Name',            'Values': [INSTANCE_NAME]},
            {'Name': 'instance-state-name', 'Values': ['running', 'stopped']}
        ])
        for res in r.get('Reservations', []):
            for inst in res['Instances']:
                profile = inst.get('IamInstanceProfile', {})
                has_profile = ROLE_NAME in profile.get('Arn', '')
                return result(
                    f"Role '{ROLE_NAME}' attached to instance '{INSTANCE_NAME}'",
                    has_profile)
        return result(f"Instance '{INSTANCE_NAME}' found", False)
    except Exception as e:
        return result(f"Instance profile check ERROR: {e}", False)

# ── Main ─────────────────────────────────────────────────────────────────────
def run_test_cases(credentials):
    global iam,ec2,dynamodb
    iam     = boto3.client('iam',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"])
    ec2     = boto3.client('ec2',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    dynamodb = boto3.client('dynamodb',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    print("=" * 60)
    print("LAB 2_7 VALIDATION: IAM Role for DynamoDB Table Access")
    print("=" * 60)
    role = test_role_exists()
    test_trust_policy(role)
    test_inline_policy_exists()
    test_policy_scoped_to_table()
    test_required_actions_present()
    print()
    passed, table = test_table_exists()
    test_table_schema(table)
    test_table_has_items()
    print()
    test_profile_on_instance()
    print("=" * 60)