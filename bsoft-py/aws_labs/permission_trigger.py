import boto3
import json
import logging
from datetime import datetime, timezone
import string
import secrets
import time
import role_credentials
from botocore.exceptions import ClientError

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
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
    student_id: str | None = None,
):

    log.info("Preparing IAM permission injection student_id=%s account_id=%s user_name=%s", student_id, account_id, user_name)
    iam = role_credentials._child_iam(account_id)

    try:
        iam.create_user(
            UserName=user_name
        )
        print(f"[OK] IAM user created: {user_name}")
        log.info("Created IAM user student_id=%s user_name=%s account_id=%s", student_id, user_name, account_id)
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            print(f"[INFO] IAM user already exists: {user_name}")
            log.info("IAM user already exists student_id=%s user_name=%s account_id=%s", student_id, user_name, account_id)
        else:
            log.exception("Failed to create IAM user student_id=%s user_name=%s account_id=%s", student_id, user_name, account_id)
        raise

    time.sleep(2)

    log.info("Injecting inline policy policy_name=%s student_id=%s user_name=%s account_id=%s",
             INLINE_POLICY_NAME, student_id, user_name, account_id)
    
    try:
        response = iam.list_user_policies(UserName="Coder")
        log.info("Listed inline policies student_id=%s user_name=Coder account_id=%s count=%s", student_id, account_id, len(response.get("PolicyNames", [])))

        # iam.delete_user_policy(
        # UserName="Coder",
        # PolicyName="TempSession"
        # )
    except ClientError as e:
        print(f"[WARN] Could not list existing inline policies for Coder: {e}")
        log.warning("Unable to list inline policies student_id=%s user_name=Coder account_id=%s error=%s", student_id, account_id, e)
    

    # iam.put_user_policy(
    #     UserName=user_name,
    #     PolicyName="DefaultIamPolicy",
    #     PolicyDocument=json.dumps(load_permissions_from_s3("bsoft-aws-labs-880690594512-ap-south-1-an","lab0.json")).replace("__BOUNDARY_ARN__",f"arn:aws:iam::{account_id}:policy/CoderCreatedIdentityBoundary").replace("__POLICY_ARN__",f"arn:aws:iam::{account_id}:policy/DefaultIamPolicy"),
    # )
    # time.sleep(5)

    iam.attach_user_policy(
    UserName="Coder",
    PolicyArn=f"arn:aws:iam::{account_id}:policy/DefaultIamPolicy"
    )
    log.info("Attached managed policy DefaultIamPolicy student_id=%s user_name=Coder account_id=%s", student_id, account_id)


    iam.put_user_policy(
        UserName=user_name,
        PolicyName=INLINE_POLICY_NAME,
        PolicyDocument=json.dumps(policy_document),
    )
    print(f"[OK] Permissions injected for {user_name}")
    log.info("Inline permissions injected policy_name=%s student_id=%s user_name=%s account_id=%s", INLINE_POLICY_NAME, student_id, user_name, account_id)

    return iam

def delete_access(
    account_id: str,
    user_name: str = "Coder",
    student_id: str | None = None,
):
    log.info("Deleting temporary access student_id=%s account_id=%s user_name=%s", student_id, account_id, user_name)
    iam = role_credentials._child_iam(account_id)

    iam.delete_user_policy(
        UserName=user_name,
        PolicyName=INLINE_POLICY_NAME
    )
    print(f"[OK] Permissions deleted for {user_name}")
    log.info("Deleted inline policy policy_name=%s student_id=%s user_name=%s account_id=%s", INLINE_POLICY_NAME, student_id, user_name, account_id)

    issue_login_info(iam,user_name, student_id=student_id, account_id=account_id)
    print(f"[OK] Password rotated for {user_name} on account {account_id}")
    log.info("Rotated login profile student_id=%s user_name=%s account_id=%s", student_id, user_name, account_id)


def issue_login_info(iam,iam_username: str, student_id: str | None = None, account_id: str | None = None):

    try:
        iam.delete_login_profile(UserName=iam_username)
        log.info("Deleted existing login profile student_id=%s account_id=%s user_name=%s", student_id, account_id, iam_username)
    except iam.exceptions.NoSuchEntityException as e:
        log.info("No existing login profile found student_id=%s account_id=%s user_name=%s", student_id, account_id, iam_username)
        pass
    time.sleep(5)

    password = _generate_password()
    iam.create_login_profile(
        UserName=iam_username,
        Password=password,
        PasswordResetRequired=False,   # we handle expiry ourselves
    )
    log.info("Created login profile student_id=%s account_id=%s user_name=%s", student_id, account_id, iam_username)
    return password

def trigger_access_s3(
    account_id: str,
    permissions_key: str,
    account_name: str = "Coder",
    permissions_bucket: str = "bsoft-aws-labs-880690594512-ap-south-1-an",
    session_tag: str = "TempSession",
    student_id: str | None = None,
) -> dict:

    log.info("Triggering access from S3 student_id=%s account_id=%s account_name=%s permissions_bucket=%s permissions_key=%s session_tag=%s", student_id, account_id, account_name, permissions_bucket, permissions_key, session_tag)
    policy_doc = load_permissions_from_s3(permissions_bucket, permissions_key)
    iam = inject_permissions(account_id, account_name, policy_doc, student_id=student_id)
    print(f"[CREDENTIAL] Temporary password for {account_name}: {issue_login_info(iam,account_name, student_id=student_id, account_id=account_id)}")
    log.info("Access trigger completed student_id=%s account_id=%s account_name=%s permissions_key=%s", student_id, account_id, account_name, permissions_key)
    return { "Status":"Success"}

# if __name__ == "__main__":
#     result = trigger_access_s3(
#         account_id="553444109943",
#         account_name="student03",
#         permissions_key="lab1.json",
#     )
#     print(json.dumps(result, indent=2))
