import boto3
import json

iam = ''
ec2 = ''

ROLE_NAME = "ec2-app-to-db-role"
INSTANCE_PROFILE_NAME = "ec2-app-to-db-role"
APP_INSTANCE_NAME = "app-server"  # Adjust to match student's instance name tag

def result(label, passed):
    status = "[PASS]" if passed else "[FAIL]"
    print(f"{status} {label}")
    return passed

def test_role_exists():
    try:
        response = iam.get_role(RoleName=ROLE_NAME)
        role = response['Role']
        return result(f"IAM Role '{ROLE_NAME}' exists", True), role
    except iam.exceptions.NoSuchEntityException:
        return result(f"IAM Role '{ROLE_NAME}' exists", False), None
    except Exception as e:
        return result(f"Role existence check - ERROR: {e}", False), None

def test_trust_policy(role):
    if not role:
        return result("Trust policy allows EC2 service to assume role", False)
    try:
        import urllib.parse
        doc = role.get('AssumeRolePolicyDocument', {})
        if isinstance(doc, str):
            doc = json.loads(urllib.parse.unquote(doc))
        statements = doc.get('Statement', [])
        ec2_trusted = False
        for s in statements:
            principal = s.get('Principal', {})
            service = principal.get('Service', '')
            if isinstance(service, list):
                ec2_trusted = 'ec2.amazonaws.com' in service
            elif isinstance(service, str):
                ec2_trusted = service == 'ec2.amazonaws.com'
            if ec2_trusted:
                break
        return result("Trust policy allows EC2 service (ec2.amazonaws.com)", ec2_trusted)
    except Exception as e:
        return result(f"Trust policy check - ERROR: {e}", False)

def test_role_has_policies():
    try:
        response = iam.list_role_policies(RoleName=ROLE_NAME)
        inline = response.get('PolicyNames', [])
        response2 = iam.list_attached_role_policies(RoleName=ROLE_NAME)
        managed = response2.get('AttachedPolicies', [])
        has_policies = len(inline) > 0 or len(managed) > 0
        result(f"Role '{ROLE_NAME}' has at least one policy attached", has_policies)
        print(f"  Inline policies: {inline}")
        print(f"  Managed policies: {[p['PolicyName'] for p in managed]}")
        return has_policies
    except Exception as e:
        return result(f"Role policies check - ERROR: {e}", False)

def test_instance_profile_exists():
    try:
        iam.get_instance_profile(InstanceProfileName=INSTANCE_PROFILE_NAME)
        return result(f"Instance profile '{INSTANCE_PROFILE_NAME}' exists", True)
    except iam.exceptions.NoSuchEntityException:
        return result(f"Instance profile '{INSTANCE_PROFILE_NAME}' exists", False)
    except Exception as e:
        return result(f"Instance profile check - ERROR: {e}", False)

def test_role_in_instance_profile():
    try:
        response = iam.get_instance_profile(InstanceProfileName=INSTANCE_PROFILE_NAME)
        roles = [r['RoleName'] for r in response['InstanceProfile'].get('Roles', [])]
        has_role = ROLE_NAME in roles
        return result(f"Role '{ROLE_NAME}' is associated with instance profile", has_role)
    except Exception as e:
        return result(f"Role-to-profile association check - ERROR: {e}", False)

def test_profile_attached_to_instance():
    try:
        response = ec2.describe_instances(
            Filters=[{'Name': 'tag:Name', 'Values': [APP_INSTANCE_NAME]},
                     {'Name': 'instance-state-name', 'Values': ['running', 'stopped']}]
        )
        reservations = response.get('Reservations', [])
        if not reservations:
            return result(f"EC2 instance '{APP_INSTANCE_NAME}' found", False)
        instance = reservations[0]['Instances'][0]
        profile = instance.get('IamInstanceProfile', {})
        arn = profile.get('Arn', '')
        has_profile = INSTANCE_PROFILE_NAME in arn
        return result(f"Instance profile attached to EC2 instance '{APP_INSTANCE_NAME}'", has_profile)
    except Exception as e:
        return result(f"Instance profile attachment check - ERROR: {e}", False)

def test_role_tag():
    try:
        response = iam.list_role_tags(RoleName=ROLE_NAME)
        tags = {t['Key']: t['Value'] for t in response.get('Tags', [])}
        has_tag = tags.get('Purpose') == 'AppToDBAccess'
        return result(f"Role has tag Purpose=AppToDBAccess", has_tag)
    except Exception as e:
        return result(f"Role tag check - ERROR: {e}", False)

def run_test_cases(credentials):
    global iam,ec2
    iam = boto3.client(
        "iam",
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )
    ec2 = boto3.client(
        "ec2",
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )
    print("=" * 60)
    print("LAB 3 VALIDATION: IAM Role for EC2 App-to-DB Access")
    print("=" * 60)
    passed, role = test_role_exists()
    test_trust_policy(role)
    test_role_has_policies()
    test_instance_profile_exists()
    test_role_in_instance_profile()
    test_profile_attached_to_instance()
    test_role_tag()
    print("=" * 60)