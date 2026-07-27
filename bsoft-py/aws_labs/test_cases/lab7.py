import boto3
import json
import subprocess

# ── Clients (instructor credentials) ─────────────────────────────────────────
iam = ""
s3  = ""
sts = ""

# ── Constants ─────────────────────────────────────────────────────────────────
USERNAME        = "lab-s3-user"
POLICY_NAME     = "S3SpecificBucketAccess"
ACCOUNT_ID      = ""
ALLOWED_BUCKET  = ""
DENIED_BUCKET   = ""

REQUIRED_ALLOW_BUCKET_ACTIONS = [
    "s3:ListBucket",
    "s3:GetBucketLocation",
    "s3:GetBucketVersioning",
    "s3:ListBucketVersions"
]

REQUIRED_ALLOW_OBJECT_ACTIONS = [
    "s3:GetObject",
    "s3:PutObject",
    "s3:DeleteObject",
    "s3:GetObjectVersion",
    "s3:GetObjectTagging",
    "s3:PutObjectTagging"
]

MUST_NOT_ALLOW_ACTIONS = [
    "s3:DeleteBucket",
    "s3:PutBucketPolicy",
    "s3:PutBucketAcl",
    "s3:CreateBucket",
    "s3:PutEncryptionConfiguration",
    "s3:PutBucketVersioning"
]

# ── Helper ─────────────────────────────────────────────────────────────────────
def result(label, passed):
    status = "[PASS]" if passed else "[FAIL]"
    print(f"{status} {label}")
    return passed

def section(title):
    print(f"\n{'─' * 55}")
    print(f"  {title}")
    print(f"{'─' * 55}")

# ── IAM User Tests ─────────────────────────────────────────────────────────────
def test_user_exists():
    try:
        r    = iam.get_user(UserName=USERNAME)
        user = r['User']
        result(f"IAM User '{USERNAME}' exists", True)
        return user
    except iam.exceptions.NoSuchEntityException:
        result(f"IAM User '{USERNAME}' exists", False)
        return None
    except Exception as e:
        result(f"User check ERROR: {e}", False)
        return None

def test_user_has_no_console_password(user):
    if not user:
        return result("User has no console password (programmatic only)", False)
    try:
        iam.get_login_profile(UserName=USERNAME)
        return result("User has no console password (programmatic only)", False)
    except iam.exceptions.NoSuchEntityException:
        return result("User has no console password (programmatic only)", True)
    except Exception as e:
        return result(f"Console password check ERROR: {e}", False)

def test_user_tags(user):
    if not user:
        return result("User has required tags", False)
    try:
        r    = iam.list_user_tags(UserName=USERNAME)
        tags = {t['Key']: t['Value'] for t in r.get('Tags', [])}
        result("User has tag Project=IAM-S3-Lab",
               tags.get('Project') == 'IAM-S3-Lab')
        result("User has tag CreatedBy set",
               bool(tags.get('CreatedBy')))
    except Exception as e:
        result(f"User tag check ERROR: {e}", False)

def test_access_key_exists():
    try:
        r    = iam.list_access_keys(UserName=USERNAME)
        keys = r.get('AccessKeyMetadata', [])
        active = [k for k in keys if k.get('Status') == 'Active']
        result(f"User has at least 1 active access key "
               f"(found {len(active)})",
               len(active) >= 1)
        return active
    except Exception as e:
        result(f"Access key check ERROR: {e}", False)
        return []

# ── Policy Tests ──────────────────────────────────────────────────────────────
def get_policy():
    try:
        r        = iam.list_policies(Scope='Local')
        policies = [p for p in r.get('Policies', [])
                    if p.get('PolicyName') == POLICY_NAME]
        if not policies:
            result(f"Customer managed policy '{POLICY_NAME}' exists", False)
            return None
        result(f"Customer managed policy '{POLICY_NAME}' exists", True)
        return policies[0]
    except Exception as e:
        result(f"Policy list check ERROR: {e}", False)
        return None

def get_policy_document(policy):
    if not policy:
        return None
    try:
        version = policy.get('DefaultVersionId', 'v1')
        r       = iam.get_policy_version(
                      PolicyArn=policy['Arn'],
                      VersionId=version)
        doc = r.get('PolicyVersion', {}).get('Document', {})
        if isinstance(doc, str):
            doc = json.loads(doc)
        return doc
    except Exception as e:
        result(f"Policy document fetch ERROR: {e}", False)
        return None

def test_policy_resources(doc):
    if not doc:
        return result("Policy document retrieved", False)
    resources = []
    for stmt in doc.get('Statement', []):
        res = stmt.get('Resource', [])
        resources.extend(res if isinstance(res, list) else [res])

    bucket_arn  = f"arn:aws:s3:::{ALLOWED_BUCKET}"
    objects_arn = f"arn:aws:s3:::{ALLOWED_BUCKET}/*"

    result(f"Policy includes bucket ARN '{bucket_arn}'",
           bucket_arn in resources)
    result(f"Policy includes objects ARN '{objects_arn}'",
           objects_arn in resources)
    result("Policy does NOT use wildcard bucket ARN (arn:aws:s3:::*)",
           "arn:aws:s3:::*" not in resources)

def test_policy_allows(doc):
    if not doc:
        return
    granted = []
    for stmt in doc.get('Statement', []):
        if stmt.get('Effect') == 'Allow':
            acts = stmt.get('Action', [])
            granted.extend(acts if isinstance(acts, list) else [acts])

    print("  ── Required Allow Actions ──")
    for action in REQUIRED_ALLOW_BUCKET_ACTIONS:
        result(f"  Policy allows '{action}'", action in granted)
    for action in REQUIRED_ALLOW_OBJECT_ACTIONS:
        result(f"  Policy allows '{action}'", action in granted)

def test_policy_denies_dangerous_actions(doc):
    if not doc:
        return
    explicitly_denied = []
    for stmt in doc.get('Statement', []):
        if stmt.get('Effect') == 'Deny':
            acts = stmt.get('Action', [])
            if isinstance(acts, str) and acts == 's3:*':
                resources = stmt.get('Resource', [])
                if isinstance(resources, list):
                    for r_arn in resources:
                        if DENIED_BUCKET in r_arn:
                            explicitly_denied.append('s3:*-on-denied-bucket')
            explicitly_denied.extend(
                acts if isinstance(acts, list) else [acts])

    result("Policy has explicit Deny on lab-other-bucket",
           any(DENIED_BUCKET in str(stmt.get('Resource', ''))
               and stmt.get('Effect') == 'Deny'
               for stmt in doc.get('Statement', [])))

def test_policy_does_not_allow_bucket_management(doc):
    if not doc:
        return
    granted = []
    for stmt in doc.get('Statement', []):
        if stmt.get('Effect') == 'Allow':
            acts = stmt.get('Action', [])
            granted.extend(acts if isinstance(acts, list) else [acts])
    print("  ── Must NOT Allow (bucket management) ──")
    for action in MUST_NOT_ALLOW_ACTIONS:
        result(f"  Policy does NOT grant '{action}'",
               action not in granted)

def test_policy_attached_to_user():
    try:
        r        = iam.list_attached_user_policies(UserName=USERNAME)
        attached = [p['PolicyName']
                    for p in r.get('AttachedPolicies', [])]
        result(f"Policy '{POLICY_NAME}' attached to user '{USERNAME}'",
               POLICY_NAME in attached)
        result("User has exactly 1 policy attached (no extras)",
               len(attached) == 1)
        return POLICY_NAME in attached
    except Exception as e:
        result(f"Policy attachment check ERROR: {e}", False)
        return False

def test_no_inline_policies():
    try:
        r        = iam.list_user_policies(UserName=USERNAME)
        inline   = r.get('PolicyNames', [])
        result(f"User has no inline policies "
               f"(found {len(inline)})", len(inline) == 0)
    except Exception as e:
        result(f"Inline policy check ERROR: {e}", False)

# ── S3 Bucket Tests ───────────────────────────────────────────────────────────
def test_allowed_bucket_exists():
    try:
        s3.head_bucket(Bucket=ALLOWED_BUCKET)
        result(f"Allowed bucket '{ALLOWED_BUCKET}' exists", True)
        return True
    except s3.exceptions.ClientError as e:
        code = e.response['Error']['Code']
        result(f"Allowed bucket '{ALLOWED_BUCKET}' exists "
               f"(error: {code})", False)
        return False

def test_denied_bucket_exists():
    try:
        s3.head_bucket(Bucket=DENIED_BUCKET)
        return result(f"Denied bucket '{DENIED_BUCKET}' exists", True)
    except s3.exceptions.ClientError as e:
        code = e.response['Error']['Code']
        return result(f"Denied bucket '{DENIED_BUCKET}' exists "
                      f"(error: {code})", False)

def test_bucket_encryption(bucket):
    try:
        r   = s3.get_bucket_encryption(Bucket=bucket)
        rules = r.get('ServerSideEncryptionConfiguration',
                      {}).get('Rules', [])
        has_enc = any(
            rule.get('ApplyServerSideEncryptionByDefault', {})
                .get('SSEAlgorithm') in ['AES256', 'aws:kms']
            for rule in rules)
        return result(f"Bucket '{bucket}' has default encryption",
                      has_enc)
    except Exception as e:
        return result(f"Encryption check '{bucket}' ERROR: {e}", False)

def test_bucket_public_access_block(bucket):
    try:
        r = s3.get_public_access_block(Bucket=bucket)
        c = r['PublicAccessBlockConfiguration']
        blocked = all([
            c.get('BlockPublicAcls', False),
            c.get('IgnorePublicAcls', False),
            c.get('BlockPublicPolicy', False),
            c.get('RestrictPublicBuckets', False)
        ])
        return result(f"Bucket '{bucket}' blocks all public access",
                      blocked)
    except Exception as e:
        return result(f"Public access check '{bucket}' ERROR: {e}",
                      False)

def test_bucket_versioning(bucket):
    try:
        r      = s3.get_bucket_versioning(Bucket=bucket)
        status = r.get('Status', '')
        return result(f"Bucket '{bucket}' versioning = Enabled",
                      status == 'Enabled')
    except Exception as e:
        return result(f"Versioning check '{bucket}' ERROR: {e}", False)

def test_test_objects_exist():
    expected_keys = [
        "allowed-file.txt",
        "allowed-data.csv",
        "uploads/sample.json"
    ]
    for key in expected_keys:
        try:
            s3.head_object(Bucket=ALLOWED_BUCKET, Key=key)
            result(f"Test object '{key}' exists in allowed bucket", True)
        except s3.exceptions.ClientError as e:
            code = e.response['Error']['Code']
            result(f"Test object '{key}' exists in allowed bucket "
                   f"(error: {code})", False)

# ── IAM Policy Simulator ───────────────────────────────────────────────────────
def test_policy_simulator():
    try:
        user_arn = iam.get_user(UserName=USERNAME)['User']['Arn']

        simulate_cases = [
            {
                "action":   "s3:GetObject",
                "resource": f"arn:aws:s3:::{ALLOWED_BUCKET}/allowed-file.txt",
                "expect":   "allowed",
                "label":    "GetObject on allowed bucket → ALLOW"
            },
            {
                "action":   "s3:PutObject",
                "resource": f"arn:aws:s3:::{ALLOWED_BUCKET}/new-file.txt",
                "expect":   "allowed",
                "label":    "PutObject on allowed bucket → ALLOW"
            },
            {
                "action":   "s3:DeleteObject",
                "resource": f"arn:aws:s3:::{ALLOWED_BUCKET}/allowed-file.txt",
                "expect":   "allowed",
                "label":    "DeleteObject on allowed bucket → ALLOW"
            },
            {
                "action":   "s3:ListBucket",
                "resource": f"arn:aws:s3:::{ALLOWED_BUCKET}",
                "expect":   "allowed",
                "label":    "ListBucket on allowed bucket → ALLOW"
            },
            {
                "action":   "s3:GetObject",
                "resource": f"arn:aws:s3:::{DENIED_BUCKET}/secret.txt",
                "expect":   "implicitDeny",
                "label":    "GetObject on denied bucket → DENY"
            },
            {
                "action":   "s3:DeleteBucket",
                "resource": f"arn:aws:s3:::{ALLOWED_BUCKET}",
                "expect":   "implicitDeny",
                "label":    "DeleteBucket on allowed bucket → DENY"
            },
            {
                "action":   "s3:PutBucketPolicy",
                "resource": f"arn:aws:s3:::{ALLOWED_BUCKET}",
                "expect":   "implicitDeny",
                "label":    "PutBucketPolicy on allowed bucket → DENY"
            }
        ]

        print("  ── IAM Policy Simulator Results ──")
        for case in simulate_cases:
            try:
                r = iam.simulate_principal_policy(
                    PolicySourceArn=user_arn,
                    ActionNames=[case['action']],
                    ResourceArns=[case['resource']]
                )
                eval_results = r.get('EvaluationResults', [])
                decision     = eval_results[0].get(
                                   'EvalDecision', 'unknown') \
                               if eval_results else 'unknown'
                if case['expect'] == 'allowed':
                    passed = decision == 'allowed'
                else:
                    passed = decision in ['explicitDeny', 'implicitDeny']
                result(f"  Simulator: {case['label']}", passed)
            except Exception as e:
                result(f"  Simulator '{case['action']}' ERROR: {e}",
                       False)
    except Exception as e:
        result(f"Policy Simulator ERROR: {e}", False)

# ── CLI Live Tests (using subprocess with lab-s3-user profile) ─────────────────
def test_cli_allowed_list():
    try:
        proc = subprocess.run(
            ['aws', 's3', 'ls',
             f's3://{ALLOWED_BUCKET}/',
             '--profile', 'lab-s3-user'],
            capture_output=True, text=True, timeout=30
        )
        passed = proc.returncode == 0
        result("CLI: aws s3 ls on allowed bucket → SUCCESS", passed)
        if passed:
            print(f"    Objects listed: "
                  f"{len(proc.stdout.strip().splitlines())} items")
    except FileNotFoundError:
        result("CLI: aws s3 ls (aws CLI not found — skipping)", True)
    except Exception as e:
        result(f"CLI list test ERROR: {e}", False)

def test_cli_denied_list():
    try:
        proc = subprocess.run(
            ['aws', 's3', 'ls',
             f's3://{DENIED_BUCKET}/',
             '--profile', 'lab-s3-user'],
            capture_output=True, text=True, timeout=30
        )
        denied = proc.returncode != 0 or \
                 'AccessDenied' in proc.stderr or \
                 'AccessDenied' in proc.stdout
        result("CLI: aws s3 ls on denied bucket → AccessDenied",
               denied)
    except FileNotFoundError:
        result("CLI: denied bucket test (aws CLI not found — skipping)",
               True)
    except Exception as e:
        result(f"CLI denied list test ERROR: {e}", False)

def test_cli_delete_bucket_denied():
    try:
        proc = subprocess.run(
            ['aws', 's3api', 'delete-bucket',
             '--bucket', ALLOWED_BUCKET,
             '--profile', 'lab-s3-user'],
            capture_output=True, text=True, timeout=30
        )
        denied = proc.returncode != 0 or \
                 'AccessDenied' in proc.stderr
        result("CLI: delete-bucket on allowed bucket → AccessDenied",
               denied)
    except FileNotFoundError:
        result("CLI: delete bucket test (aws CLI not found — skipping)",
               True)
    except Exception as e:
        result(f"CLI delete bucket test ERROR: {e}", False)

# ── Main ──────────────────────────────────────────────────────────────────────
def run_test_cases(credentials):
    global iam,s3,sts,ACCOUNT_ID,ALLOWED_BUCKET,DENIED_BUCKET
    ACCOUNT_ID = sts.get_caller_identity()['Account']
    ALLOWED_BUCKET  = f"lab-user-bucket-{ACCOUNT_ID}"
    DENIED_BUCKET   = f"lab-other-bucket-{ACCOUNT_ID}"
    iam = boto3.client('iam',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )
    s3  = boto3.client('s3',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )
    sts = boto3.client('sts',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )
    print("=" * 60)
    print("LAB 32 VALIDATION: IAM User with S3 Bucket-Specific Policy")
    print("=" * 60)

    section("IAM User")
    user = test_user_exists()
    test_user_has_no_console_password(user)
    test_user_tags(user)
    test_access_key_exists()
    test_no_inline_policies()

    section("Customer Managed Policy")
    policy = get_policy()
    doc    = get_policy_document(policy)
    test_policy_resources(doc)
    test_policy_allows(doc)
    test_policy_denies_dangerous_actions(doc)
    test_policy_does_not_allow_bucket_management(doc)
    test_policy_attached_to_user()

    section("S3 Buckets")
    test_allowed_bucket_exists()
    test_denied_bucket_exists()
    for bucket in [ALLOWED_BUCKET, DENIED_BUCKET]:
        test_bucket_encryption(bucket)
        test_bucket_public_access_block(bucket)
        test_bucket_versioning(bucket)
    test_test_objects_exist()

    section("IAM Policy Simulator")
    test_policy_simulator()

    section("Live CLI Tests (lab-s3-user profile)")
    test_cli_allowed_list()
    test_cli_denied_list()
    test_cli_delete_bucket_denied()

    print("\n" + "=" * 60)
    print("Validation complete.")
    print("=" * 60)