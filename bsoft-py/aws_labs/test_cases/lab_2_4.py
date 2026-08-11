import boto3

ec2 = ''

VPC_NAME = "lab-vpc"
VPC_CIDR = "10.0.0.0/16"
SUBNET_NAME = "public-subnet"
SUBNET_CIDR = "10.0.1.0/24"
NACL_NAME = "lab-public-nacl"
HTTP_RULE_NUMBER = 100
HTTP_PORT = 80
OPEN_CIDR = "0.0.0.0/0"
DEFAULT_DENY_RULE_NUMBER = 32767

def result(label, passed):
    status = "[PASS]" if passed else "[FAIL]"
    print(f"{status} {label}")
    return passed

def locate_custom_vpc():
    try:
        vpcs = ec2.describe_vpcs(Filters=[{'Name': 'is-default', 'Values': ['false']}]).get('Vpcs', [])
        passed = len(vpcs) == 1
        result("Exactly one custom VPC exists", passed)
        return vpcs[0] if passed else None
    except Exception as e:
        result(f"Custom VPC lookup - ERROR: {e}", False)
        return None

def verify_vpc_name(vpc):
    if not vpc:
        return result(f"VPC Name tag = '{VPC_NAME}'", False)
    tags = {t['Key']: t['Value'] for t in vpc.get('Tags', [])}
    return result(f"VPC Name tag = '{VPC_NAME}'", tags.get('Name') == VPC_NAME)

def verify_vpc_cidr(vpc):
    if not vpc:
        return result(f"VPC CIDR = '{VPC_CIDR}'", False)
    return result(f"VPC CIDR = '{VPC_CIDR}'", vpc.get('CidrBlock') == VPC_CIDR)

def locate_public_subnet(vpc_id):
    if not vpc_id:
        result("Exactly one Public Subnet exists", False)
        return None
    try:
        subnets = ec2.describe_subnets(
            Filters=[
                {'Name': 'vpc-id', 'Values': [vpc_id]},
                {'Name': 'tag:Name', 'Values': [SUBNET_NAME]},
            ]
        ).get('Subnets', [])
        passed = len(subnets) == 1
        result("Exactly one Public Subnet exists", passed)
        return subnets[0] if passed else None
    except Exception as e:
        result(f"Public Subnet lookup - ERROR: {e}", False)
        return None

def verify_subnet_cidr(subnet):
    if not subnet:
        return result(f"Subnet CIDR = '{SUBNET_CIDR}'", False)
    return result(f"Subnet CIDR = '{SUBNET_CIDR}'", subnet.get('CidrBlock') == SUBNET_CIDR)

def locate_custom_nacl(vpc_id):
    if not vpc_id:
        result("Exactly one custom Network ACL exists", False)
        return None
    try:
        nacls = ec2.describe_network_acls(
            Filters=[
                {'Name': 'vpc-id', 'Values': [vpc_id]},
                {'Name': 'default', 'Values': ['false']},
            ]
        ).get('NetworkAcls', [])
        passed = len(nacls) == 1
        result("Exactly one custom Network ACL exists", passed)
        return nacls[0] if passed else None
    except Exception as e:
        result(f"Custom Network ACL lookup - ERROR: {e}", False)
        return None

def verify_nacl_name(nacl):
    if not nacl:
        return result(f"Network ACL Name tag = '{NACL_NAME}'", False)
    tags = {t['Key']: t['Value'] for t in nacl.get('Tags', [])}
    return result(f"Network ACL Name tag = '{NACL_NAME}'", tags.get('Name') == NACL_NAME)

def verify_nacl_associated_with_subnet(nacl, subnet):
    if not nacl or not subnet:
        return result("Network ACL is associated with public-subnet", False)
    associated_subnet_ids = {a['SubnetId'] for a in nacl.get('Associations', []) if a.get('SubnetId')}
    return result("Network ACL is associated with public-subnet", subnet['SubnetId'] in associated_subnet_ids)

def find_inbound_entry(nacl, rule_number):
    for entry in nacl.get('Entries', []):
        if not entry.get('Egress', True) and entry.get('RuleNumber') == rule_number:
            return entry
    return None

def verify_http_allow_rule(nacl):
    if not nacl:
        result(f"Inbound Rule {HTTP_RULE_NUMBER} exists", False)
        result("Rule protocol = TCP", False)
        result(f"Rule port = {HTTP_PORT}", False)
        result(f"Rule source = {OPEN_CIDR}", False)
        return result("Rule Action = ALLOW", False)

    entry = find_inbound_entry(nacl, HTTP_RULE_NUMBER)
    result(f"Inbound Rule {HTTP_RULE_NUMBER} exists", entry is not None)

    if not entry:
        result("Rule protocol = TCP", False)
        result(f"Rule port = {HTTP_PORT}", False)
        result(f"Rule source = {OPEN_CIDR}", False)
        return result("Rule Action = ALLOW", False)

    result("Rule protocol = TCP", entry.get('Protocol') == '6')

    port_range = entry.get('PortRange', {})
    result(f"Rule port = {HTTP_PORT}", port_range.get('From') == HTTP_PORT and port_range.get('To') == HTTP_PORT)

    result(f"Rule source = {OPEN_CIDR}", entry.get('CidrBlock') == OPEN_CIDR)

    return result("Rule Action = ALLOW", entry.get('RuleAction') == 'allow')

def verify_default_deny_rule(nacl):
    if not nacl:
        return result("Default inbound rule denies all other traffic", False)
    entry = find_inbound_entry(nacl, DEFAULT_DENY_RULE_NUMBER)
    passed = entry is not None and entry.get('RuleAction') == 'deny' and entry.get('CidrBlock') == OPEN_CIDR
    return result("Default inbound rule denies all other traffic", passed)

def run_test_cases(credentials):
    global ec2
    ec2 = boto3.client(
        'ec2',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )
    print("=" * 60)
    print("LAB 2.4 VALIDATION: Create a Network ACL and Associate it with a Public Subnet")
    print("=" * 60)
    vpc = locate_custom_vpc()
    vpc_id = vpc.get('VpcId') if vpc else None
    verify_vpc_name(vpc)
    verify_vpc_cidr(vpc)

    subnet = locate_public_subnet(vpc_id)
    verify_subnet_cidr(subnet)

    nacl = locate_custom_nacl(vpc_id)
    verify_nacl_name(nacl)
    verify_nacl_associated_with_subnet(nacl, subnet)
    verify_http_allow_rule(nacl)
    verify_default_deny_rule(nacl)
    print("=" * 60)
