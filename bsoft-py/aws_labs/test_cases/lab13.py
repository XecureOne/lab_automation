import boto3

rds = ""
ec2 = ""

DB_ID       = "php-app-db"
SUBNET_GRP  = "php-db-subnet-group"
RDS_SG_NAME = "php-rds-sg"

def result(label, passed):
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}")
    return passed

def get_db_instance():
    try:
        r   = rds.describe_db_instances(DBInstanceIdentifier=DB_ID)
        dbs = r.get('DBInstances', [])
        db  = dbs[0] if dbs else None
        result(f"RDS instance '{DB_ID}' exists", db is not None)
        return db
    except rds.exceptions.DBInstanceNotFoundFault:
        result(f"RDS instance '{DB_ID}' exists", False)
        return None
    except Exception as e:
        result(f"RDS check ERROR: {e}", False)
        return None

def test_db_status(db):
    if not db:
        return
    status = db.get('DBInstanceStatus', '')
    result(f"RDS status = available (got {status})",
           status == 'available')

def test_db_engine(db):
    if not db:
        return
    engine  = db.get('Engine', '')
    version = db.get('EngineVersion', '')
    result(f"RDS engine = MySQL (got {engine})",     engine == 'mysql')
    result(f"RDS engine version starts with '8.0'", version.startswith('8.0'))

def test_not_publicly_accessible(db):
    if not db:
        return result("RDS is NOT publicly accessible", False)
    public = db.get('PubliclyAccessible', True)
    return result("RDS is NOT publicly accessible", not public)

def test_encryption(db):
    if not db:
        return result("RDS storage is encrypted", False)
    return result("RDS storage encryption enabled",
                  db.get('StorageEncrypted', False))

def test_deletion_protection(db):
    if not db:
        return result("Deletion protection enabled", False)
    return result("Deletion protection is enabled",
                  db.get('DeletionProtection', False))

def test_backup_retention(db):
    if not db:
        return result("Backup retention ≥ 7 days", False)
    ret = db.get('BackupRetentionPeriod', 0)
    return result(f"Backup retention = {ret} days (expect ≥ 7)", ret >= 7)

def test_storage_type(db):
    if not db:
        return result("Storage type = gp3", False)
    st = db.get('StorageType', '')
    return result(f"Storage type = gp3 (got {st})", st == 'gp3')

def test_subnet_group(db):
    if not db:
        return result(f"DB subnet group = {SUBNET_GRP}", False)
    sg  = db.get('DBSubnetGroup', {})
    name = sg.get('DBSubnetGroupName', '')
    result(f"DB subnet group = '{SUBNET_GRP}'", name == SUBNET_GRP)
    subnets = sg.get('Subnets', [])
    azs     = {s['SubnetAvailabilityZone']['Name'] for s in subnets}
    result(f"Subnet group spans {len(azs)} AZ(s) (expect ≥ 2)", len(azs) >= 2)

def test_rds_sg_rule():
    try:
        r  = ec2.describe_security_groups(
                 Filters=[{'Name': 'group-name', 'Values': [RDS_SG_NAME]}])
        sgs = r.get('SecurityGroups', [])
        if not sgs:
            return result(f"Security group '{RDS_SG_NAME}' found", False)
        sg      = sgs[0]
        perms   = sg.get('IpPermissions', [])
        mysql_from_sg = any(
            p.get('FromPort') == 3306 and
            len(p.get('UserIdGroupPairs', [])) > 0
            for p in perms
        )
        result(f"SG '{RDS_SG_NAME}' allows MySQL 3306 from SG reference",
               mysql_from_sg)
        no_cidr_mysql = not any(
            p.get('FromPort') == 3306 and
            len(p.get('IpRanges', [])) > 0
            for p in perms
        )
        result("MySQL port not open to CIDR (uses SG reference only)",
               no_cidr_mysql)
    except Exception as e:
        result(f"RDS SG rule check ERROR: {e}", False)

def test_db_class(db):
    if not db:
        return result("DB instance class = db.t3.micro", False)
    cls = db.get('DBInstanceClass', '')
    return result(f"DB instance class = db.t3.micro (got {cls})",
                  cls == 'db.t3.micro')

def run_test_cases(credentials):
    global rds,ec2
    rds = boto3.client('rds',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    ec2 = boto3.client('ec2',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    print("=" * 60)
    print("LAB 17 VALIDATION: MySQL RDS Instance")
    print("=" * 60)
    db = get_db_instance()
    test_db_status(db)
    test_db_engine(db)
    test_not_publicly_accessible(db)
    test_encryption(db)
    test_deletion_protection(db)
    test_backup_retention(db)
    test_storage_type(db)
    test_db_class(db)
    test_subnet_group(db)
    test_rds_sg_rule()
    print("=" * 60)