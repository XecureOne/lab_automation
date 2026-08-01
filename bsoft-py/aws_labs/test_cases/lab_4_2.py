import boto3

rds = ""
s3  = ""

DB_ID        = "php-app-db"
BUCKET_PREFIX = "php-static-assets-"
DUMP_KEY     = "db-backups/source_app_dump.sql"

def result(label, passed):
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}")
    return passed

def skip(label):
    print(f"[SKIP] {label}")
    return None

def find_bucket():
    label = f"S3 bucket starting with '{BUCKET_PREFIX}' exists"
    try:
        r       = s3.list_buckets()
        buckets = [b['Name'] for b in r['Buckets']
                   if b['Name'].startswith(BUCKET_PREFIX)]
        bucket  = buckets[0] if buckets else None
        result(label, bucket is not None)
        return bucket
    except Exception as e:
        result(f"{label} - ERROR: {e}", False)
        return None

def test_rds_available():
    try:
        r  = rds.describe_db_instances(DBInstanceIdentifier=DB_ID)
        db = r['DBInstances'][0] if r.get('DBInstances') else None
        ok = db and db.get('DBInstanceStatus') == 'available'
        return result(f"RDS '{DB_ID}' is available for migration", ok), \
               db.get('Endpoint', {}).get('Address', '') if db else ''
    except Exception as e:
        result(f"RDS availability check ERROR: {e}", False)
        return False, ''

def test_dump_in_s3(bucket):
    if not bucket:
        return skip(f"Dump file '{DUMP_KEY}' uploaded to S3 bucket")
    try:
        s3.head_object(Bucket=bucket, Key=DUMP_KEY)
        result(f"Dump file '{DUMP_KEY}' uploaded to S3 bucket", True)
        return True
    except s3.exceptions.ClientError as e:
        if e.response['Error']['Code'] in ['404', 'NoSuchKey']:
            return result(f"Dump file '{DUMP_KEY}' found in S3", False)
        return result(f"Dump S3 check ERROR: {e}", False)
    except Exception as e:
        return result(f"Dump S3 check ERROR: {e}", False)

def test_dump_sse(bucket):
    if not bucket:
        return skip("Dump file has SSE (AES256)")
    try:
        r2  = s3.head_object(Bucket=bucket, Key=DUMP_KEY)
        enc = r2.get('ServerSideEncryption', '')
        return result(f"Dump file has SSE ({enc})", bool(enc))
    except Exception as e:
        return result(f"SSE check ERROR: {e}", False)

def test_rds_endpoint_accessible(endpoint):
    if not endpoint:
        return result("RDS endpoint is set", False)
    return result(f"RDS endpoint exists: {endpoint}", bool(endpoint))

def run_test_cases(credentials):
    global rds,s3
    rds = boto3.client('rds',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    s3  = boto3.client('s3',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    print("=" * 60)
    print("LAB 4_2 VALIDATION: Data Migration to RDS")
    print("=" * 60)
    ok, endpoint = test_rds_available()
    test_rds_endpoint_accessible(endpoint)
    bucket = find_bucket()
    test_dump_in_s3(bucket)
    test_dump_sse(bucket)
    print("=" * 60)