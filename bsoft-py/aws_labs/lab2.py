import boto3
import botocore
client = ''
account_id = ''

REGION = 'ap-south-1'

def test_case1():
    try:
        print("TestCase 01 : Checking public s3 bucket with name 'sample-s3-bucket'")
        try:
            if account_id:
                response = client.head_bucket(
                    Bucket=f'sample-s3-bucket-{account_id}-{REGION}-an'
                )
                print("Status :: Success")
                return True
        except client.exceptions.NoSuchBucket as e:
            print("Status :: Failure")
            return False
    except Exception as e:
        print(e)

def test_case2():
    try:
        try:
            print("TestCase 02 : Checking s3 bucket 'sample-s3-bucket' polices")
            response = client.get_bucket_policy(
                Bucket=f'sample-s3-bucket-{account_id}-{REGION}-an'
            )
            if response['Policy']:
                print("Status :: Success")
                return True
        except client.exceptions.NoSuchBucketPolicy as e:
            print("Status :: Failure")
            return False
    except Exception as e:
        print(e)

def run_test_cases(credentials):
    global client,account_id
    client = boto3.client('s3',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"])
    sts = boto3.client("sts",
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"])
    account_id = sts.get_caller_identity()["Account"]
    flag = []
    for i in range(1,3):
        flag.append(globals()[f"test_case{i}"]())
    return "Failure" if False in flag else "Success"