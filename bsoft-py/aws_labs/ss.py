import boto3
import time

org = boto3.client("organizations")

TARGET_OU_ID = "ou-anaf-fe7lhyxx"
EMAIL_PREFIX = "test"
EMAIL_DOMAIN = "xecureone.com"

# Get root ID
root_id = "r-anaf"

for i in range(4, 11):

    email = f"{EMAIL_PREFIX}+aws{i:02d}@{EMAIL_DOMAIN}"
    account_name = f"lab-user{i:02d}"

    print(f"Creating {account_name} ({email})")

    response = org.create_account(
        Email=email,
        AccountName=account_name,
        RoleName="OrganizationAccountAccessRole"
    )

    request_id = response["CreateAccountStatus"]["Id"]

    # Wait for completion
    while True:

        status = org.describe_create_account_status(
            CreateAccountRequestId=request_id
        )["CreateAccountStatus"]

        state = status["State"]

        if state == "SUCCEEDED":

            account_id = status["AccountId"]

            print(f"Account created: {account_id}")

            org.move_account(
                AccountId=account_id,
                SourceParentId=root_id,
                DestinationParentId=TARGET_OU_ID
            )

            print(
                f"Moved {account_id} to {TARGET_OU_ID}"
            )

            break

        elif state == "FAILED":

            print(
                f"Failed creating {account_name}"
            )
            print(status.get("FailureReason"))
            break

        else:
            print(f"Waiting... {account_name}")
            time.sleep(30)