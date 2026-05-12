
import boto3
import json
import time
import secrets
import string
import logging
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
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

def provision_iam_user(account_id: str, iam_username: str) -> dict:
    """
    Step 3 – Inside the child account, create:
        • An IAM user with an auto-generated console password
        • An IAM role with NO permissions (trust policy allows only the user above)

    Returns a dict with user ARN, role ARN, and the plaintext password
    (store this securely – it is only returned once).
    """
    iam = role_credentials._child_iam(account_id)

    # # ---- IAM User ----
    log.info("Creating IAM user '%s' in account %s …", iam_username, account_id)
    user_resp = iam.create_user(UserName=iam_username)
    # user_resp = iam.get_user(UserName=iam_username)
    user_arn = user_resp["User"]["Arn"]

    password = _generate_password()
    iam.create_login_profile(
        UserName=iam_username,
        Password=password,
        PasswordResetRequired=False,   # we handle expiry ourselves
    )
    log.info("IAM user created: %s", user_arn)

    return {
        "account_id": account_id,
        "iam_username": iam_username,
        "user_arn": user_arn,
        "initial_password": password,          # ⚠ store in Secrets Manager immediately
        "provisioned_at": datetime.now(timezone.utc).isoformat(),
    }


def provision_account(
    account_id: str,
    iam_username: str,
) -> dict:
    iam_info = provision_iam_user(account_id, iam_username)

    return


if __name__ == "__main__":
    result = provision_account(
        account_id = "553444109943",
        iam_username="Coder",
    )
    print(json.dumps(result, indent=2))
