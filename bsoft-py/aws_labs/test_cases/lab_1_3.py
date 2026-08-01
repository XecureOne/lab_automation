import boto3

ec2 = ''

EXPECTED_NAME = "lab-vpc"
EXPECTED_CIDR = "10.0.0.0/16"

def result(label, passed):
    status = "[PASS]" if passed else "[FAIL]"
    print(f"{status} {label}")
    return passed

def find_custom_vpcs():
    response = ec2.describe_vpcs(Filters=[{'Name': 'is-default', 'Values': ['false']}])
    return response.get('Vpcs', [])

def check_single_custom_vpc():
    try:
        vpcs = find_custom_vpcs()
        passed = len(vpcs) == 1
        result("Exactly one custom VPC exists (Default VPC ignored)", passed)
        return passed, (vpcs[0] if passed else None)
    except Exception as e:
        result(f"Custom VPC lookup - ERROR: {e}", False)
        return False, None

def check_vpc_name(vpc):
    if not vpc:
        return result(f"VPC Name tag = '{EXPECTED_NAME}'", False)
    tags = {t['Key']: t['Value'] for t in vpc.get('Tags', [])}
    return result(f"VPC Name tag = '{EXPECTED_NAME}'", tags.get('Name') == EXPECTED_NAME)

def check_cidr_block(vpc):
    if not vpc:
        return result(f"IPv4 CIDR Block = '{EXPECTED_CIDR}'", False)
    return result(f"IPv4 CIDR Block = '{EXPECTED_CIDR}'", vpc.get('CidrBlock') == EXPECTED_CIDR)

def check_vpc_available(vpc):
    if not vpc:
        return result("VPC State = Available", False)
    return result("VPC State = Available", vpc.get('State') == 'available')

def run_test_cases(credentials):
    global ec2
    ec2 = boto3.client(
        'ec2',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )
    print("=" * 55)
    print("LAB 1.3 VALIDATION: Creating a Virtual Private Cloud (VPC)")
    print("=" * 55)
    _, vpc = check_single_custom_vpc()
    check_vpc_name(vpc)
    check_cidr_block(vpc)
    check_vpc_available(vpc)
    print("=" * 55)
