import boto3

USER_NAME = "yabez"

session = boto3.Session()  # Uses your current AWS credentials

sts = session.client("sts")
iam = session.client("iam")

print("Current Account:", sts.get_caller_identity()["Account"])

try:
    user = iam.get_user(UserName=USER_NAME)
    print("User exists:", user["User"]["UserName"])
except Exception as e:
    print("User not found:", e)
    raise

print("\nInline Policies:")
print(iam.list_user_policies(UserName=USER_NAME)["PolicyNames"])

print("\nManaged Policies:")
for p in iam.list_attached_user_policies(UserName=USER_NAME)["AttachedPolicies"]:
    print("-", p["PolicyName"])
