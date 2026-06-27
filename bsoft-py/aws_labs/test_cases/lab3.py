import boto3
import sys

VPC_CIDR = "10.0.0.0/16"
VPC_NAME = "lab-vpc"
client = ''

def result(label, passed):
    status = "[PASS]" if passed else "[FAIL]"
    print(f"{status} {label}")
    return passed

def test_vpc_exists():
    try:
        response = client.describe_vpcs(
            Filters=[{'Name': 'cidr', 'Values': [VPC_CIDR]},
                     {'Name': 'state', 'Values': ['available']}]
        )
        vpcs = response.get('Vpcs', [])
        return result("VPC exists with CIDR 10.0.0.0/16 and state=available", len(vpcs) > 0), \
               vpcs[0]['VpcId'] if vpcs else None
    except Exception as e:
        result(f"VPC exists - ERROR: {e}", False)
        return False, None

def test_vpc_name_tag(vpc_id):
    if not vpc_id:
        return result("VPC has Name tag 'lab-vpc'", False)
    try:
        response = client.describe_tags(
            Filters=[
                {'Name': 'resource-id', 'Values': [vpc_id]},
                {'Name': 'key', 'Values': ['Name']}
            ]
        )
        tags = response.get('Tags', [])
        has_name = any(t['Value'] == VPC_NAME for t in tags)
        return result("VPC has Name tag 'lab-vpc'", has_name)
    except Exception as e:
        return result(f"VPC Name tag check - ERROR: {e}", False)

def test_dns_resolution(vpc_id):
    if not vpc_id:
        return result("DNS Resolution enabled", False)
    try:
        response = client.describe_vpc_attribute(VpcId=vpc_id, Attribute='enableDnsSupport')
        enabled = response['EnableDnsSupport']['Value']
        return result("DNS Resolution (enableDnsSupport) is enabled", enabled)
    except Exception as e:
        return result(f"DNS Resolution check - ERROR: {e}", False)

def test_dns_hostnames(vpc_id):
    if not vpc_id:
        return result("DNS Hostnames enabled", False)
    try:
        response = client.describe_vpc_attribute(VpcId=vpc_id, Attribute='enableDnsHostnames')
        enabled = response['EnableDnsHostnames']['Value']
        return result("DNS Hostnames (enableDnsHostnames) is enabled", enabled)
    except Exception as e:
        return result(f"DNS Hostnames check - ERROR: {e}", False)

def test_vpc_tenancy(vpc_id):
    if not vpc_id:
        return result("VPC Tenancy is default", False)
    try:
        response = client.describe_vpcs(VpcIds=[vpc_id])
        tenancy = response['Vpcs'][0].get('InstanceTenancy', '')
        return result("VPC Instance Tenancy is 'default'", tenancy == 'default')
    except Exception as e:
        return result(f"VPC Tenancy check - ERROR: {e}", False)

def test_not_default_vpc(vpc_id):
    if not vpc_id:
        return result("VPC is not the default VPC", False)
    try:
        response = client.describe_vpcs(VpcIds=[vpc_id])
        is_default = response['Vpcs'][0].get('IsDefault', True)
        return result("VPC is not the default VPC", not is_default)
    except Exception as e:
        return result(f"Default VPC check - ERROR: {e}", False)

def run_test_cases(credentials):
    global client
    client = boto3.client('ec2',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"])
    print("=" * 55)
    print("LAB 1 VALIDATION: Creating a VPC")
    print("=" * 55)
    passed, vpc_id = test_vpc_exists()
    test_vpc_name_tag(vpc_id)
    test_dns_resolution(vpc_id)
    test_dns_hostnames(vpc_id)
    test_vpc_tenancy(vpc_id)
    test_not_default_vpc(vpc_id)
    print("=" * 55)