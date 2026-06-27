import boto3


client = ''

EXPECTED_USERS = ["contractor-user-1", "contractor-user-2", "contractor-user-3"]
GROUP_NAME = "php-project-contractors"
EXPECTED_POLICIES = [
    "arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess",
    "arn:aws:iam::aws:policy/AmazonEC2ReadOnlyAccess"
]

def result(label, passed):
    status = "[PASS]" if passed else "[FAIL]"
    print(f"{status} {label}")
    return passed

def test_users_exist():
    all_passed = True
    for username in EXPECTED_USERS:
        try:
            client.get_user(UserName=username)
            result(f"IAM User '{username}' exists", True)
        except client.exceptions.NoSuchEntityException:
            result(f"IAM User '{username}' exists", False)
            all_passed = False
        except Exception as e:
            result(f"IAM User '{username}' check - ERROR: {e}", False)
            all_passed = False
    return all_passed

def test_group_exists():
    try:
        client.get_group(GroupName=GROUP_NAME)
        return result(f"client Group '{GROUP_NAME}' exists", True)
    except client.exceptions.NoSuchEntityException:
        return result(f"client Group '{GROUP_NAME}' exists", False)
    except Exception as e:
        return result(f"Group existence check - ERROR: {e}", False)

def test_users_in_group():
    all_passed = True
    try:
        paginator = client.get_paginator('get_group')
        members = []
        for page in paginator.paginate(GroupName=GROUP_NAME):
            members.extend([u['UserName'] for u in page.get('Users', [])])
        for username in EXPECTED_USERS:
            in_group = username in members
            result(f"User '{username}' is a member of '{GROUP_NAME}'", in_group)
            if not in_group:
                all_passed = False
    except Exception as e:
        result(f"Group membership check - ERROR: {e}", False)
        all_passed = False
    return all_passed

def test_group_policies():
    all_passed = True
    try:
        response = client.list_attached_group_policies(GroupName=GROUP_NAME)
        attached_arns = [p['PolicyArn'] for p in response.get('AttachedPolicies', [])]
        for policy_arn in EXPECTED_POLICIES:
            has_policy = policy_arn in attached_arns
            short_name = policy_arn.split('/')[-1]
            result(f"Group has policy '{short_name}' attached", has_policy)
            if not has_policy:
                all_passed = False
    except Exception as e:
        result(f"Group policy check - ERROR: {e}", False)
        all_passed = False
    return all_passed

def test_no_extra_policies():
    try:
        response = client.list_attached_group_policies(GroupName=GROUP_NAME)
        attached_arns = [p['PolicyArn'] for p in response.get('AttachedPolicies', [])]
        extra = [a for a in attached_arns if a not in EXPECTED_POLICIES]
        no_extra = len(extra) == 0
        result(f"Group has no extra (non-readonly) policies attached", no_extra)
        if not no_extra:
            print(f"  WARNING: Extra policies found: {extra}")
        return no_extra
    except Exception as e:
        return result(f"Extra policies check - ERROR: {e}", False)

def test_user_project_tags():
    all_passed = True
    for username in EXPECTED_USERS:
        try:
            response = client.list_user_tags(UserName=username)
            tags = {t['Key']: t['Value'] for t in response.get('Tags', [])}
            has_tag = tags.get('Project') == 'PHP-WebApp'
            result(f"User '{username}' has tag Project=PHP-WebApp", has_tag)
            if not has_tag:
                all_passed = False
        except Exception as e:
            result(f"Tag check for '{username}' - ERROR: {e}", False)
            all_passed = False
    return all_passed

def run_test_cases(credentials):
    global client
    client = boto3.client('iam',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"])
    print("=" * 60)
    print("LAB 2 VALIDATION: IAM Users, Groups, and Contractor Access")
    print("=" * 60)
    test_users_exist()
    test_group_exists()
    test_users_in_group()
    test_group_policies()
    test_no_extra_policies()
    test_user_project_tags()
    print("=" * 60)