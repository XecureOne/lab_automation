import boto3
from botocore.exceptions import ClientError
from role_credentials import _child_creds


def create_iam_user(user_name: str,iam_client):
    """
    Creates an IAM user with the specified name.
    """
    try:
        print(f"Creating IAM user: {user_name}...")
        response = iam_client.create_user(UserName=user_name)
        user_arn = response['User']['Arn']
        print(f"✅ Success! User '{user_name}' created. ARN: {user_arn}")
        return response
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'EntityAlreadyExists':
            print(f"⚠️ User '{user_name}' already exists.")
        else:
            print(f"❌ Failed to create user: {e}")
        return None

def create_aws_account_alias(alias: str,iam_client):
    """
    Creates a global AWS account alias for the login URL.
    Note: An account can only have ONE alias at a time.
    """
    try:
        print(f"Setting global account alias to: '{alias}'...")
        iam_client.create_account_alias(AccountAlias=alias)
        print(f"✅ Success! Your sign-in URL is now: https://{alias}.signin.aws.amazon.com/console")
        return True
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'EntityAlreadyExists':
            print(f"⚠️ The account alias '{alias}' is already taken by you or another AWS customer.")
        else:
            print(f"❌ Failed to create account alias: {e}")
        return False

def list_active_accounts():
    client = boto3.client("organizations")
    res = client.list_accounts_for_parent(
        ParentId="ou-anaf-fe7lhyxx",
    )
    if res:
        res = res["Accounts"]
        res = [[i["Id"],i["Name"]] for i in res if i["Status"]=="ACTIVE"]
        print("Active accounts listed!!")
        # print(res)cl
        return res

# --- Execution ---
if __name__ == "__main__":
    res = list_active_accounts()
    for i in res:
        credentials = _child_creds(i[0])
        client = boto3.client(
            "iam",
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
        )
        create_iam_user("Coder",client)
        create_aws_account_alias(i[1],client)

    