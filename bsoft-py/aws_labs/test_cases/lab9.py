import boto3

iam = ""
sqs = ""

GROUP_NAME  = "php-app-queue-users"
QUEUE_NAME  = "php-app-jobs-queue"
USER_NAMES  = ["queue-user-1", "queue-user-2"]
INLINE_POL  = "sqs-queue-access-policy"
REQUIRED_ACTIONS = [
    "sqs:SendMessage", "sqs:ReceiveMessage", "sqs:DeleteMessage",
    "sqs:GetQueueAttributes", "sqs:GetQueueUrl", "sqs:ChangeMessageVisibility"
]

def result(label, passed):
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}")
    return passed

def test_queue_exists():
    try:
        r = sqs.get_queue_url(QueueName=QUEUE_NAME)
        url = r['QueueUrl']
        result(f"SQS Queue '{QUEUE_NAME}' exists", True)
        return url
    except sqs.exceptions.QueueDoesNotExist:
        result(f"SQS Queue '{QUEUE_NAME}' exists", False)
        return None
    except Exception as e:
        result(f"Queue check ERROR: {e}", False)
        return None

def test_queue_encryption(queue_url):
    if not queue_url:
        return result("Queue has SSE enabled", False)
    try:
        r = sqs.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=['SqsManagedSseEnabled', 'KmsMasterKeyId']
        )
        attrs = r.get('Attributes', {})
        sse    = attrs.get('SqsManagedSseEnabled', 'false').lower() == 'true'
        kms    = bool(attrs.get('KmsMasterKeyId', ''))
        return result("Queue has server-side encryption enabled", sse or kms)
    except Exception as e:
        return result(f"Encryption check ERROR: {e}", False)

def test_group_exists():
    try:
        iam.get_group(GroupName=GROUP_NAME)
        return result(f"IAM Group '{GROUP_NAME}' exists", True)
    except iam.exceptions.NoSuchEntityException:
        return result(f"IAM Group '{GROUP_NAME}' exists", False)
    except Exception as e:
        return result(f"Group check ERROR: {e}", False)

def test_users_in_group():
    try:
        paginator = iam.get_paginator('get_group')
        members   = []
        for page in paginator.paginate(GroupName=GROUP_NAME):
            members.extend(u['UserName'] for u in page.get('Users', []))
        for name in USER_NAMES:
            result(f"User '{name}' is member of '{GROUP_NAME}'", name in members)
    except Exception as e:
        result(f"Group membership check ERROR: {e}", False)

def test_inline_policy_exists():
    try:
        iam.get_group_policy(GroupName=GROUP_NAME, PolicyName=INLINE_POL)
        return result(f"Inline policy '{INLINE_POL}' exists on group", True)
    except iam.exceptions.NoSuchEntityException:
        return result(f"Inline policy '{INLINE_POL}' exists on group", False)
    except Exception as e:
        return result(f"Inline policy check ERROR: {e}", False)

def test_policy_scoped_and_actions():
    import json
    try:
        r   = iam.get_group_policy(GroupName=GROUP_NAME, PolicyName=INLINE_POL)
        doc = r['PolicyDocument']
        if isinstance(doc, str):
            doc = json.loads(doc)
        resources = []
        granted   = []
        for stmt in doc.get('Statement', []):
            res = stmt.get('Resource', [])
            resources.extend(res if isinstance(res, list) else [res])
            if stmt.get('Effect') == 'Allow':
                acts = stmt.get('Action', [])
                granted.extend(acts if isinstance(acts, list) else [acts])
        result(f"Policy resource scoped to '{QUEUE_NAME}'",
               any(QUEUE_NAME in r for r in resources))
        result("Policy does NOT use wildcard '*' resource",
               not any(r == '*' for r in resources))
        for action in REQUIRED_ACTIONS:
            result(f"  Policy grants '{action}'", action in granted)
    except Exception as e:
        result(f"Policy content check ERROR: {e}", False)

def test_users_exist():
    for name in USER_NAMES:
        try:
            iam.get_user(UserName=name)
            result(f"IAM User '{name}' exists", True)
        except iam.exceptions.NoSuchEntityException:
            result(f"IAM User '{name}' exists", False)
        except Exception as e:
            result(f"User '{name}' check ERROR: {e}", False)

def run_test_cases(credentials):
    global iam,sqs
    iam = boto3.client('iam',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    sqs = boto3.client('sqs',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )   
    print("=" * 60)
    print("LAB 13 VALIDATION: IAM Group for SQS Queue Access")
    print("=" * 60)
    url = test_queue_exists()
    test_queue_encryption(url)
    print()
    test_group_exists()
    test_users_in_group()
    test_inline_policy_exists()
    test_policy_scoped_and_actions()
    print()
    test_users_exist()
    print("=" * 60)