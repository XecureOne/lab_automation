import time

import boto3
from botocore.exceptions import ClientError
from role_credentials import _child_creds
import json
import nuke


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

def create_iam_boundary(iam):
    policy_document = ''
    with open('/home/carpediem/bsoft/lab_permissions/boundary.json','r') as f:
        policy_document = json.load(f)

    try:
        response = iam.create_policy(
            PolicyName="CoderCreatedIdentityBoundary",
            PolicyDocument=json.dumps(policy_document),
            Description="Permissions boundary created by automation."
        )

        print("Policy created successfully!")
        print("Policy ARN:", response["Policy"]["Arn"])

    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            print(f"Policy already exists.")
        else:
            raise

def create_iam_policy(iam,account_id):
    policy_document = ''
    with open('../../lab_permissions/lab0.json','r') as f:
        policy_document = json.load(f)

    policy_document = json.dumps(policy_document).replace("__BOUNDARY_ARN__",f"arn:aws:iam::{account_id}:policy/CoderCreatedIdentityBoundary")
    policy_document = policy_document.replace("__POLICY_ARN__",f"arn:aws:iam::{account_id}:policy/DefaultIamPolicy")

    try:
        response = iam.create_policy(
            PolicyName="DefaultIamPolicy",
            PolicyDocument=policy_document,
            Description="Permissions boundary created by automation."
        )

        print("Policy created successfully!")
        print("Policy ARN:", response["Policy"]["Arn"])

    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            print(f"Policy already exists.")
        else:
            raise

def delete_iam_boundary(iam,account_id):

    policy_arn = f"arn:aws:iam::{account_id}:policy/CoderCreatedIdentityBoundary"
    versions = iam.list_policy_versions(
    PolicyArn=policy_arn
    )["Versions"]

    for version in versions:
        if not version["IsDefaultVersion"]:
            iam.delete_policy_version(
                PolicyArn=policy_arn,
                VersionId=version["VersionId"]
            )
    iam.delete_policy(
        PolicyArn=policy_arn
        )

def delete_iam_policy(iam,account_id):
    
    policy_arn = f"arn:aws:iam::{account_id}:policy/DefaultIamPolicy"
    versions = iam.list_policy_versions(
    PolicyArn=policy_arn
    )["Versions"]

    for version in versions:
        if not version["IsDefaultVersion"]:
            iam.delete_policy_version(
                PolicyArn=policy_arn,
                VersionId=version["VersionId"]
            )

    iam.delete_policy(
    PolicyArn=policy_arn
    )

    print(f"Policy deleted successfully! {account_id}")



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

def fetch_account_alias(account_id):
    with open("./alias_mapping.json","r") as f:
        res = json.loads(f.read())
        return res.get(account_id)

def full_cleanup(account_id,creds):
    alias = fetch_account_alias(account_id)
    nuke.nuke(account_id,alias,creds)

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
        # create_iam_user("Coder",client)
        # create_aws_account_alias(i[1],client)
        # create_iam_policy(client,i[0])
        # full_cleanup(i[0],credentials)
        delete_iam_policy(client,i[0])
        time.sleep(5)
        create_iam_policy(client,i[0])

    