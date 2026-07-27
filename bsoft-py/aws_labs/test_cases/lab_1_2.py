import boto3

s3 = ''

EXPECTED_REGION = "ap-south-1"
BUCKET_PREFIX = "lab-1-2-"

def result(label, passed):
    status = "[PASS]" if passed else "[FAIL]"
    print(f"{status} {label}")
    return passed

def find_lab_buckets():
    response = s3.list_buckets()
    buckets = response.get('Buckets', [])
    return [b for b in buckets if b['Name'].startswith(BUCKET_PREFIX)]

def check_single_bucket():
    try:
        matches = find_lab_buckets()
        passed = len(matches) == 1
        result(f"Exactly one S3 bucket exists with prefix '{BUCKET_PREFIX}'", passed)
        return passed, (matches[0]['Name'] if passed else None)
    except Exception as e:
        result(f"S3 bucket lookup - ERROR: {e}", False)
        return False, None

def check_bucket_name(bucket_name):
    if not bucket_name:
        return result(f"Bucket name starts with '{BUCKET_PREFIX}'", False)
    return result(f"Bucket name starts with '{BUCKET_PREFIX}'", bucket_name.startswith(BUCKET_PREFIX))

def check_bucket_region(bucket_name):
    if not bucket_name:
        return result(f"Bucket is in the correct Region ('{EXPECTED_REGION}')", False)
    try:
        response = s3.get_bucket_location(Bucket=bucket_name)
        # AWS returns None/empty for the us-east-1 region
        location = response.get('LocationConstraint') or 'us-east-1'
        return result(f"Bucket is in the correct Region ('{EXPECTED_REGION}')", location == EXPECTED_REGION)
    except Exception as e:
        return result(f"Bucket region check - ERROR: {e}", False)

def check_block_public_access(bucket_name):
    if not bucket_name:
        return result("Block Public Access is enabled on the bucket", False)
    try:
        response = s3.get_public_access_block(Bucket=bucket_name)
        config = response.get('PublicAccessBlockConfiguration', {})
        all_enabled = all([
            config.get('BlockPublicAcls', False),
            config.get('IgnorePublicAcls', False),
            config.get('BlockPublicPolicy', False),
            config.get('RestrictPublicBuckets', False),
        ])
        return result("Block Public Access is enabled on the bucket", all_enabled)
    except Exception as e:
        return result(f"Block Public Access check - ERROR: {e}", False)

def run_test_cases(credentials):
    global s3
    s3 = boto3.client(
        's3',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )
    print("=" * 55)
    print("LAB 1.2 VALIDATION: Creating an Amazon S3 Bucket")
    print("=" * 55)
    _, bucket_name = check_single_bucket()
    check_bucket_name(bucket_name)
    check_bucket_region(bucket_name)
    check_block_public_access(bucket_name)
    print("=" * 55)
