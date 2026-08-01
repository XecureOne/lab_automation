import boto3

ec2 = ''

VPC_NAME = "lab-php-vpc"
VPC_CIDR = "10.0.0.0/16"
REQUIRED_AZ_COUNT = 2
REQUIRED_PUBLIC_SUBNETS = 2
REQUIRED_PRIVATE_SUBNETS = 2

def result(label, passed):
    status = "[PASS]" if passed else "[FAIL]"
    print(f"{status} {label}")
    return passed

def locate_custom_vpc():
    try:
        response = ec2.describe_vpcs(Filters=[{'Name': 'is-default', 'Values': ['false']}])
        candidates = response.get('Vpcs', [])
        passed = len(candidates) == 1
        result("Exactly one custom VPC exists (Default VPC ignored)", passed)
        return candidates[0] if passed else None
    except Exception as e:
        result(f"Custom VPC lookup - ERROR: {e}", False)
        return None

def verify_name_tag(vpc):
    if not vpc:
        return result(f"Name tag = '{VPC_NAME}'", False)
    tags = {t['Key']: t['Value'] for t in vpc.get('Tags', [])}
    return result(f"Name tag = '{VPC_NAME}'", tags.get('Name') == VPC_NAME)

def verify_cidr(vpc):
    if not vpc:
        return result(f"IPv4 CIDR = '{VPC_CIDR}'", False)
    return result(f"IPv4 CIDR = '{VPC_CIDR}'", vpc.get('CidrBlock') == VPC_CIDR)

def verify_available(vpc):
    if not vpc:
        return result("State = Available", False)
    return result("State = Available", vpc.get('State') == 'available')

def list_vpc_subnets(vpc_id):
    return ec2.describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]).get('Subnets', [])

def verify_two_availability_zones(vpc_id):
    if not vpc_id:
        return result(f"Networking spans {REQUIRED_AZ_COUNT} Availability Zones", False)
    try:
        subnets = list_vpc_subnets(vpc_id)
        az_count = len({s['AvailabilityZone'] for s in subnets})
        return result(f"Networking spans {REQUIRED_AZ_COUNT} Availability Zones", az_count == REQUIRED_AZ_COUNT)
    except Exception as e:
        return result(f"Availability Zone check - ERROR: {e}", False)

def list_vpc_route_tables(vpc_id):
    return ec2.describe_route_tables(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]).get('RouteTables', [])

def routes_to_internet_gateway(route_table):
    for route in route_table.get('Routes', []):
        gateway = route.get('GatewayId', '')
        if route.get('DestinationCidrBlock') == '0.0.0.0/0' and gateway.startswith('igw-'):
            return True
    return False

def split_public_private_subnets(vpc_id, subnets):
    route_tables = list_vpc_route_tables(vpc_id)
    main_table = next((rt for rt in route_tables if any(a.get('Main') for a in rt.get('Associations', []))), None)

    explicit_association = {}
    for rt in route_tables:
        for assoc in rt.get('Associations', []):
            subnet_id = assoc.get('SubnetId')
            if subnet_id:
                explicit_association[subnet_id] = rt

    public_subnets, private_subnets = [], []
    for subnet in subnets:
        governing_table = explicit_association.get(subnet['SubnetId'], main_table)
        if governing_table and routes_to_internet_gateway(governing_table):
            public_subnets.append(subnet)
        else:
            private_subnets.append(subnet)
    return public_subnets, private_subnets

def verify_public_subnets(vpc_id):
    if not vpc_id:
        return result(f"{REQUIRED_PUBLIC_SUBNETS} Public Subnets exist", False)
    try:
        subnets = list_vpc_subnets(vpc_id)
        public_subnets, _ = split_public_private_subnets(vpc_id, subnets)
        return result(f"{REQUIRED_PUBLIC_SUBNETS} Public Subnets exist", len(public_subnets) == REQUIRED_PUBLIC_SUBNETS)
    except Exception as e:
        return result(f"Public Subnet check - ERROR: {e}", False)

def verify_private_subnets(vpc_id):
    if not vpc_id:
        return result(f"{REQUIRED_PRIVATE_SUBNETS} Private Subnets exist", False)
    try:
        subnets = list_vpc_subnets(vpc_id)
        _, private_subnets = split_public_private_subnets(vpc_id, subnets)
        return result(f"{REQUIRED_PRIVATE_SUBNETS} Private Subnets exist", len(private_subnets) == REQUIRED_PRIVATE_SUBNETS)
    except Exception as e:
        return result(f"Private Subnet check - ERROR: {e}", False)

def verify_internet_gateway(vpc_id):
    if not vpc_id:
        return result("Internet Gateway is attached", False)
    try:
        response = ec2.describe_internet_gateways(
            Filters=[{'Name': 'attachment.vpc-id', 'Values': [vpc_id]}]
        )
        attached = any(
            attachment.get('VpcId') == vpc_id and attachment.get('State') == 'available'
            for igw in response.get('InternetGateways', [])
            for attachment in igw.get('Attachments', [])
        )
        return result("Internet Gateway is attached", attached)
    except Exception as e:
        return result(f"Internet Gateway check - ERROR: {e}", False)

def verify_route_tables_present(vpc_id):
    if not vpc_id:
        return result("Route Tables were created", False)
    try:
        route_tables = list_vpc_route_tables(vpc_id)
        return result("Route Tables were created", len(route_tables) >= 2)
    except Exception as e:
        return result(f"Route Table check - ERROR: {e}", False)

def verify_dns_hostnames(vpc_id):
    if not vpc_id:
        return result("DNS Hostnames are enabled", False)
    try:
        attr = ec2.describe_vpc_attribute(VpcId=vpc_id, Attribute='enableDnsHostnames')
        return result("DNS Hostnames are enabled", attr['EnableDnsHostnames']['Value'])
    except Exception as e:
        return result(f"DNS Hostnames check - ERROR: {e}", False)

def verify_dns_resolution(vpc_id):
    if not vpc_id:
        return result("DNS Resolution is enabled", False)
    try:
        attr = ec2.describe_vpc_attribute(VpcId=vpc_id, Attribute='enableDnsSupport')
        return result("DNS Resolution is enabled", attr['EnableDnsSupport']['Value'])
    except Exception as e:
        return result(f"DNS Resolution check - ERROR: {e}", False)

def run_test_cases(credentials):
    global ec2
    ec2 = boto3.client(
        'ec2',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )
    print("=" * 60)
    print("LAB 2.3 VALIDATION: VPC for a PHP Web App (Two Availability Zones)")
    print("=" * 60)
    vpc = locate_custom_vpc()
    vpc_id = vpc.get('VpcId') if vpc else None
    verify_name_tag(vpc)
    verify_cidr(vpc)
    verify_available(vpc)
    verify_two_availability_zones(vpc_id)
    verify_public_subnets(vpc_id)
    verify_private_subnets(vpc_id)
    verify_internet_gateway(vpc_id)
    verify_route_tables_present(vpc_id)
    verify_dns_hostnames(vpc_id)
    verify_dns_resolution(vpc_id)
    print("=" * 60)
