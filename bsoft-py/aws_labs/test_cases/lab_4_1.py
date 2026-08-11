import boto3

rds = ""

DB_ID       = "php-app-db"



def result(label, passed):
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}")
    return passed

def skip(label):
    print(f"[SKIP] {label}")
    return None


def get_db_instance():
    label = f"RDS instance starting with '{DB_ID}' exists"
    try:
        r   = rds.describe_db_instances()
        dbs = r.get('DBInstances', [])
        db  = next((d for d in dbs
                    if d.get('DBInstanceIdentifier', '').startswith(DB_ID)), None)
        result(label, db is not None)
        return db
    except Exception as e:
        result(f"RDS check ERROR: {e}", False)
        return None

def test_db_status(db):
    if not db:
        return skip("RDS status = available")
    status = db.get('DBInstanceStatus', '')
    result(f"RDS status = available (got {status})",
           status == 'available')

def test_db_engine(db):
    if not db:
        skip("RDS engine = MySQL")
        skip("RDS engine version starts with '8.4'")
        return
    engine  = db.get('Engine', '')
    version = db.get('EngineVersion', '')
    result(f"RDS engine = MySQL (got {engine})",     engine == 'mysql')
    result(f"RDS engine version starts with '8.4'", version.startswith('8.4'))

def test_not_publicly_accessible(db):
    if not db:
        return skip("RDS is NOT publicly accessible")
    public = db.get('PubliclyAccessible', True)
    return result("RDS is NOT publicly accessible", not public)

def test_encryption(db):
    if not db:
        return skip("RDS storage is encrypted")
    return result("RDS storage encryption enabled",
                  db.get('StorageEncrypted', False))

def test_backup_retention(db):
    if not db:
        return skip("Backup retention ≥ 7 days")
    ret = db.get('BackupRetentionPeriod', 0)
    return result(f"Backup retention = {ret} days (expect ≥ 7)", ret >= 7)

def test_storage_type(db):
    if not db:
        return skip("Storage type = gp3")
    st = db.get('StorageType', '')
    return result(f"Storage type = gp3 (got {st})", st == 'gp3')

def run_test_cases(credentials):
    global rds
    rds = boto3.client('rds',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    sts = boto3.client(
    "sts",
    aws_access_key_id=credentials["AccessKeyId"],
    aws_secret_access_key=credentials["SecretAccessKey"],
    aws_session_token=credentials["SessionToken"]
    )

    print("Caller:", sts.get_caller_identity())

    print("=" * 60)
    print("LAB 4_1 VALIDATION: MySQL RDS Instance")
    print("=" * 60)
    db = get_db_instance()
    test_db_status(db)
    test_db_engine(db)
    test_not_publicly_accessible(db)
    test_encryption(db)
    test_backup_retention(db)
    test_storage_type(db)
    print("=" * 60)