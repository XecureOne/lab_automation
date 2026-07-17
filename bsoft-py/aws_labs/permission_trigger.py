import boto3
import json
import logging
from datetime import datetime, timezone
import string
import secrets
import time
import role_credentials
from botocore.exceptions import ClientError

log = logging.getLogger(__name__)

def _generate_password(length: int = 24) -> str:
    """
    Auto-generate a password that satisfies AWS IAM complexity:
    upper, lower, digit, symbol, min 8 chars.
    """
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.isupper() for c in pwd)
            and any(c.islower() for c in pwd)
            and any(c.isdigit() for c in pwd)
            and any(c in "!@#$%^&*()-_=+" for c in pwd)
        ):
            return pwd

def load_permissions_from_s3(bucket: str, key: str) -> dict:
    """
    Load an IAM policy document stored as JSON in S3.

    Expected format (standard IAM policy):
    {
        "Version": "2012-10-17",
        "Statement": [ ... ]
    }
    """
    s3 = boto3.client("s3")
    log.info("Loading permissions from s3://%s/%s", bucket, key)
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    return json.loads(body)


INLINE_POLICY_NAME = "TempInjectedPolicy"

def inject_permissions(
    account_id: str,
    user_name: str,
    policy_document: dict,
):

    iam = role_credentials._child_iam(account_id)

    try:
        iam.create_user(
            UserName=user_name
        )
        print("User Created.")
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            print("User already exists")
        raise

    time.sleep(2)

    log.info("Injecting inline policy '%s' onto user '%s' in account %s …",
             INLINE_POLICY_NAME, user_name, account_id)
    
    try:
        response = iam.list_user_policies(UserName="Coder")
        print(response)

        # iam.delete_user_policy(
        # UserName="Coder",
        # PolicyName="TempSession"
        # )
    except ClientError as e:
        print(e)

    iam.put_user_policy(
        UserName=user_name,
        PolicyName=INLINE_POLICY_NAME,
        PolicyDocument=json.dumps(policy_document),
    )
    print("Permissions Injected!!")
    log.info("Permissions injected.")

    return iam

def delete_access(
    account_id: str,
    user_name: str = "Coder"
):
    iam = role_credentials._child_iam(account_id)

    iam.delete_user_policy(
        UserName=user_name,
        PolicyName=INLINE_POLICY_NAME
    )
    print("Permissions Deleted!!")

    issue_login_info(iam,user_name)
    print(f"Password changed for {user_name} on {account_id}")


def issue_login_info(iam,iam_username: str):

    try:
        iam.delete_login_profile(UserName=iam_username)
    except iam.exceptions.NoSuchEntityException as e:
        pass
    time.sleep(5)

    password = _generate_password()
    iam.create_login_profile(
        UserName=iam_username,
        Password=password,
        PasswordResetRequired=False,   # we handle expiry ourselves
    )
    return password

def trigger_access_s3(
    account_id: str,
    permissions_key: str,
    account_name: str = "Coder",
    permissions_bucket: str = "bsoft-aws-labs-880690594512-ap-south-1-an",
    session_tag: str = "TempSession",
) -> dict:

    policy_doc = load_permissions_from_s3(permissions_bucket, permissions_key)
    iam = inject_permissions(account_id, account_name, policy_doc)
    print(issue_login_info(iam,account_name))
    return { "Status":"Success"}

# if __name__ == "__main__":
#     result = trigger_access_s3(
#         account_id="553444109943",
#         account_name="student03",
#         permissions_key="lab1.json",
#     )
#     print(json.dumps(result, indent=2))
