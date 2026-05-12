import boto3
import nuke


def _assume_child_role(account_id: str,credentials: dict, role_name: str = "OrganizationAccountAccessRole") -> dict:
    """
    Assume the cross-account role that AWS Organizations creates automatically
    in every child account.  Returns STS credentials dict.
    """
    sts = ''
    if credentials:
        sts = boto3.client(
        "sts",
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
        )
    else:
        sts = boto3.client("sts")
    if sts:
        resp = sts.assume_role(
            RoleArn=f"arn:aws:iam::{account_id}:role/{role_name}",
            RoleSessionName="ProvisionerSession",
            DurationSeconds=3600,
    )
    if resp: 
        print(f"Assumed role: {role_name}")
        return resp["Credentials"]


def _child_iam(account_id):
    """Return an IAM client authenticated to the child account."""
    credentials = _assume_child_role(account_id,_assume_child_role("959782869917",{},"rt_provider_core_backend"))
    return boto3.client(
        "iam",
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )

