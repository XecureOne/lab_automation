import boto3
import subprocess
import tempfile
import os

ec2 = ''
ec2_instance_connect = ''

VPC_A_NAME = "lab-vpc-a"
VPC_A_CIDR = "10.0.0.0/16"
SUBNET_A_NAME = "public-subnet-a"
SUBNET_A_CIDR = "10.0.1.0/24"
INSTANCE_A_NAME = "web-server-a"

VPC_B_NAME = "lab-vpc-b"
VPC_B_CIDR = "10.1.0.0/16"
SUBNET_B_NAME = "public-subnet-b"
SUBNET_B_CIDR = "10.1.1.0/24"
INSTANCE_B_NAME = "web-server-b"

SSH_USER = "ec2-user"

def result(label, passed):
    status = "[PASS]" if passed else "[FAIL]"
    print(f"{status} {label}")
    return passed

# ---------------------------------------------------------------------------
# Every test case below performs its own independent AWS lookup. None of
# them accept or reuse an object discovered by another test case, so
# deleting or breaking one resource only fails the test case(s) that
# specifically search for that resource.
# ---------------------------------------------------------------------------

def test_two_custom_vpcs_exist():
    try:
        vpcs = ec2.describe_vpcs(Filters=[{'Name': 'is-default', 'Values': ['false']}]).get('Vpcs', [])
        return result("Two custom VPCs exist", len(vpcs) == 2)
    except Exception as e:
        return result(f"Custom VPC count check - ERROR: {e}", False)

def test_vpc_a_name():
    try:
        vpcs = ec2.describe_vpcs(Filters=[{'Name': 'tag:Name', 'Values': [VPC_A_NAME]}]).get('Vpcs', [])
        return result(f"VPC '{VPC_A_NAME}' exists", len(vpcs) == 1)
    except Exception as e:
        return result(f"VPC '{VPC_A_NAME}' lookup - ERROR: {e}", False)

def test_vpc_a_cidr():
    try:
        vpcs = ec2.describe_vpcs(Filters=[{'Name': 'tag:Name', 'Values': [VPC_A_NAME]}]).get('Vpcs', [])
        cidr_ok = bool(vpcs) and vpcs[0].get('CidrBlock') == VPC_A_CIDR
        return result(f"VPC '{VPC_A_NAME}' CIDR = '{VPC_A_CIDR}'", cidr_ok)
    except Exception as e:
        return result(f"VPC '{VPC_A_NAME}' CIDR check - ERROR: {e}", False)

def test_vpc_b_name():
    try:
        vpcs = ec2.describe_vpcs(Filters=[{'Name': 'tag:Name', 'Values': [VPC_B_NAME]}]).get('Vpcs', [])
        return result(f"VPC '{VPC_B_NAME}' exists", len(vpcs) == 1)
    except Exception as e:
        return result(f"VPC '{VPC_B_NAME}' lookup - ERROR: {e}", False)

def test_vpc_b_cidr():
    try:
        vpcs = ec2.describe_vpcs(Filters=[{'Name': 'tag:Name', 'Values': [VPC_B_NAME]}]).get('Vpcs', [])
        cidr_ok = bool(vpcs) and vpcs[0].get('CidrBlock') == VPC_B_CIDR
        return result(f"VPC '{VPC_B_NAME}' CIDR = '{VPC_B_CIDR}'", cidr_ok)
    except Exception as e:
        return result(f"VPC '{VPC_B_NAME}' CIDR check - ERROR: {e}", False)

def test_subnet_a_cidr():
    try:
        subnets = ec2.describe_subnets(Filters=[{'Name': 'tag:Name', 'Values': [SUBNET_A_NAME]}]).get('Subnets', [])
        cidr_ok = bool(subnets) and subnets[0].get('CidrBlock') == SUBNET_A_CIDR
        return result(f"Subnet '{SUBNET_A_NAME}' CIDR = '{SUBNET_A_CIDR}'", cidr_ok)
    except Exception as e:
        return result(f"Subnet '{SUBNET_A_NAME}' check - ERROR: {e}", False)

def test_subnet_b_cidr():
    try:
        subnets = ec2.describe_subnets(Filters=[{'Name': 'tag:Name', 'Values': [SUBNET_B_NAME]}]).get('Subnets', [])
        cidr_ok = bool(subnets) and subnets[0].get('CidrBlock') == SUBNET_B_CIDR
        return result(f"Subnet '{SUBNET_B_NAME}' CIDR = '{SUBNET_B_CIDR}'", cidr_ok)
    except Exception as e:
        return result(f"Subnet '{SUBNET_B_NAME}' check - ERROR: {e}", False)

def test_internet_gateway_attached_to_vpc_a():
    try:
        vpcs = ec2.describe_vpcs(Filters=[{'Name': 'tag:Name', 'Values': [VPC_A_NAME]}]).get('Vpcs', [])
        if not vpcs:
            return result(f"Internet Gateway attached to {VPC_A_NAME}", False)
        igws = ec2.describe_internet_gateways(
            Filters=[{'Name': 'attachment.vpc-id', 'Values': [vpcs[0]['VpcId']]}]
        ).get('InternetGateways', [])
        attached = any(a.get('State') == 'available' for igw in igws for a in igw.get('Attachments', []))
        return result(f"Internet Gateway attached to {VPC_A_NAME}", attached)
    except Exception as e:
        return result(f"Internet Gateway check for {VPC_A_NAME} - ERROR: {e}", False)

def test_internet_gateway_attached_to_vpc_b():
    try:
        vpcs = ec2.describe_vpcs(Filters=[{'Name': 'tag:Name', 'Values': [VPC_B_NAME]}]).get('Vpcs', [])
        if not vpcs:
            return result(f"Internet Gateway attached to {VPC_B_NAME}", False)
        igws = ec2.describe_internet_gateways(
            Filters=[{'Name': 'attachment.vpc-id', 'Values': [vpcs[0]['VpcId']]}]
        ).get('InternetGateways', [])
        attached = any(a.get('State') == 'available' for igw in igws for a in igw.get('Attachments', []))
        return result(f"Internet Gateway attached to {VPC_B_NAME}", attached)
    except Exception as e:
        return result(f"Internet Gateway check for {VPC_B_NAME} - ERROR: {e}", False)

def test_route_table_configured_for_vpc_a():
    try:
        subnets = ec2.describe_subnets(Filters=[{'Name': 'tag:Name', 'Values': [SUBNET_A_NAME]}]).get('Subnets', [])
        if not subnets:
            return result(f"Route Table configured for {VPC_A_NAME} (internet access)", False)
        subnet_id = subnets[0]['SubnetId']
        vpc_id = subnets[0]['VpcId']
        route_tables = ec2.describe_route_tables(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]).get('RouteTables', [])
        associated = [
            rt for rt in route_tables
            if any(a.get('SubnetId') == subnet_id for a in rt.get('Associations', []))
            or any(a.get('Main') for a in rt.get('Associations', []))
        ]
        has_internet_route = any(
            route.get('DestinationCidrBlock') == '0.0.0.0/0' and str(route.get('GatewayId', '')).startswith('igw-')
            for rt in associated
            for route in rt.get('Routes', [])
        )
        return result(f"Route Table configured for {VPC_A_NAME} (internet access)", has_internet_route)
    except Exception as e:
        return result(f"Route Table check for {VPC_A_NAME} - ERROR: {e}", False)

def test_route_table_configured_for_vpc_b():
    try:
        subnets = ec2.describe_subnets(Filters=[{'Name': 'tag:Name', 'Values': [SUBNET_B_NAME]}]).get('Subnets', [])
        if not subnets:
            return result(f"Route Table configured for {VPC_B_NAME} (internet access)", False)
        subnet_id = subnets[0]['SubnetId']
        vpc_id = subnets[0]['VpcId']
        route_tables = ec2.describe_route_tables(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]).get('RouteTables', [])
        associated = [
            rt for rt in route_tables
            if any(a.get('SubnetId') == subnet_id for a in rt.get('Associations', []))
            or any(a.get('Main') for a in rt.get('Associations', []))
        ]
        has_internet_route = any(
            route.get('DestinationCidrBlock') == '0.0.0.0/0' and str(route.get('GatewayId', '')).startswith('igw-')
            for rt in associated
            for route in rt.get('Routes', [])
        )
        return result(f"Route Table configured for {VPC_B_NAME} (internet access)", has_internet_route)
    except Exception as e:
        return result(f"Route Table check for {VPC_B_NAME} - ERROR: {e}", False)

def test_instance_exists_in_vpc_a():
    try:
        vpcs = ec2.describe_vpcs(Filters=[{'Name': 'tag:Name', 'Values': [VPC_A_NAME]}]).get('Vpcs', [])
        if not vpcs:
            return result(f"One EC2 instance exists in {VPC_A_NAME}", False)
        response = ec2.describe_instances(
            Filters=[
                {'Name': 'vpc-id', 'Values': [vpcs[0]['VpcId']]},
                {'Name': 'instance-state-name', 'Values': ['pending', 'running']},
            ]
        )
        instances = [i for r in response.get('Reservations', []) for i in r.get('Instances', [])]
        return result(f"One EC2 instance exists in {VPC_A_NAME}", len(instances) == 1)
    except Exception as e:
        return result(f"Instance check for {VPC_A_NAME} - ERROR: {e}", False)

def test_instance_exists_in_vpc_b():
    try:
        vpcs = ec2.describe_vpcs(Filters=[{'Name': 'tag:Name', 'Values': [VPC_B_NAME]}]).get('Vpcs', [])
        if not vpcs:
            return result(f"One EC2 instance exists in {VPC_B_NAME}", False)
        response = ec2.describe_instances(
            Filters=[
                {'Name': 'vpc-id', 'Values': [vpcs[0]['VpcId']]},
                {'Name': 'instance-state-name', 'Values': ['pending', 'running']},
            ]
        )
        instances = [i for r in response.get('Reservations', []) for i in r.get('Instances', [])]
        return result(f"One EC2 instance exists in {VPC_B_NAME}", len(instances) == 1)
    except Exception as e:
        return result(f"Instance check for {VPC_B_NAME} - ERROR: {e}", False)

def _find_peering_connection():
    vpcs_a = ec2.describe_vpcs(Filters=[{'Name': 'tag:Name', 'Values': [VPC_A_NAME]}]).get('Vpcs', [])
    vpcs_b = ec2.describe_vpcs(Filters=[{'Name': 'tag:Name', 'Values': [VPC_B_NAME]}]).get('Vpcs', [])
    if not vpcs_a or not vpcs_b:
        return None
    connections = ec2.describe_vpc_peering_connections(
        Filters=[
            {'Name': 'requester-vpc-info.vpc-id', 'Values': [vpcs_a[0]['VpcId']]},
            {'Name': 'accepter-vpc-info.vpc-id', 'Values': [vpcs_b[0]['VpcId']]},
        ]
    ).get('VpcPeeringConnections', [])
    return connections[0] if len(connections) == 1 else None

def test_peering_connection_exists():
    peering = _find_peering_connection()
    return result("VPC Peering Connection exists", peering is not None)

def test_peering_connection_active():
    peering = _find_peering_connection()
    active = bool(peering) and peering.get('Status', {}).get('Code') == 'active'
    return result("Peering Connection status = Active", active)

def test_route_table_a_routes_to_peer():
    try:
        subnets = ec2.describe_subnets(Filters=[{'Name': 'tag:Name', 'Values': [SUBNET_A_NAME]}]).get('Subnets', [])
        peering = _find_peering_connection()
        label = f"Route Table in {VPC_A_NAME} routes {VPC_B_CIDR} via peering"
        if not subnets or not peering:
            return result(label, False)
        route_tables = ec2.describe_route_tables(
            Filters=[{'Name': 'vpc-id', 'Values': [subnets[0]['VpcId']]}]
        ).get('RouteTables', [])
        has_route = any(
            route.get('DestinationCidrBlock') == VPC_B_CIDR
            and route.get('VpcPeeringConnectionId') == peering['VpcPeeringConnectionId']
            for rt in route_tables
            for route in rt.get('Routes', [])
        )
        return result(label, has_route)
    except Exception as e:
        return result(f"Peering route check for {VPC_A_NAME} - ERROR: {e}", False)

def test_route_table_b_routes_to_peer():
    try:
        subnets = ec2.describe_subnets(Filters=[{'Name': 'tag:Name', 'Values': [SUBNET_B_NAME]}]).get('Subnets', [])
        peering = _find_peering_connection()
        label = f"Route Table in {VPC_B_NAME} routes {VPC_A_CIDR} via peering"
        if not subnets or not peering:
            return result(label, False)
        route_tables = ec2.describe_route_tables(
            Filters=[{'Name': 'vpc-id', 'Values': [subnets[0]['VpcId']]}]
        ).get('RouteTables', [])
        has_route = any(
            route.get('DestinationCidrBlock') == VPC_A_CIDR
            and route.get('VpcPeeringConnectionId') == peering['VpcPeeringConnectionId']
            for rt in route_tables
            for route in rt.get('Routes', [])
        )
        return result(label, has_route)
    except Exception as e:
        return result(f"Peering route check for {VPC_B_NAME} - ERROR: {e}", False)

def test_security_group_a_allows_icmp():
    try:
        vpcs = ec2.describe_vpcs(Filters=[{'Name': 'tag:Name', 'Values': [VPC_A_NAME]}]).get('Vpcs', [])
        if not vpcs:
            return result(f"Security Group in {VPC_A_NAME} allows ICMP", False)
        groups = ec2.describe_security_groups(
            Filters=[{'Name': 'vpc-id', 'Values': [vpcs[0]['VpcId']]}]
        ).get('SecurityGroups', [])
        allows_icmp = any(perm.get('IpProtocol') in ('icmp', '-1') for sg in groups for perm in sg.get('IpPermissions', []))
        return result(f"Security Group in {VPC_A_NAME} allows ICMP", allows_icmp)
    except Exception as e:
        return result(f"Security Group ICMP check for {VPC_A_NAME} - ERROR: {e}", False)

def test_security_group_b_allows_icmp():
    try:
        vpcs = ec2.describe_vpcs(Filters=[{'Name': 'tag:Name', 'Values': [VPC_B_NAME]}]).get('Vpcs', [])
        if not vpcs:
            return result(f"Security Group in {VPC_B_NAME} allows ICMP", False)
        groups = ec2.describe_security_groups(
            Filters=[{'Name': 'vpc-id', 'Values': [vpcs[0]['VpcId']]}]
        ).get('SecurityGroups', [])
        allows_icmp = any(perm.get('IpProtocol') in ('icmp', '-1') for sg in groups for perm in sg.get('IpPermissions', []))
        return result(f"Security Group in {VPC_B_NAME} allows ICMP", allows_icmp)
    except Exception as e:
        return result(f"Security Group ICMP check for {VPC_B_NAME} - ERROR: {e}", False)

def _find_running_instance(name):
    response = ec2.describe_instances(
        Filters=[
            {'Name': 'tag:Name', 'Values': [name]},
            {'Name': 'instance-state-name', 'Values': ['running']},
        ]
    )
    instances = [i for r in response.get('Reservations', []) for i in r.get('Instances', [])]
    return instances[0] if instances else None

def push_temporary_ssh_key(instance, key_path):
    subprocess.run(
        ['ssh-keygen', '-t', 'rsa', '-b', '2048', '-f', key_path, '-N', '', '-q'],
        check=True,
    )
    with open(f"{key_path}.pub") as f:
        public_key = f.read()
    ec2_instance_connect.send_ssh_public_key(
        InstanceId=instance['InstanceId'],
        InstanceOSUser=SSH_USER,
        SSHPublicKey=public_key,
        AvailabilityZone=instance['Placement']['AvailabilityZone'],
    )

def run_remote_ping(public_ip, private_target_ip, key_path):
    completed = subprocess.run(
        [
            'ssh',
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'ConnectTimeout=10',
            '-i', key_path,
            f'{SSH_USER}@{public_ip}',
            'ping', '-c', '4', private_target_ip,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return completed.returncode, completed.stdout, completed.stderr

def test_end_to_end_connectivity():
    """
    Independently re-discovers both instances by tag Name (does not reuse
    the objects looked up by test_instance_exists_in_vpc_a/b above), pushes
    a temporary SSH key to web-server-a via EC2 Instance Connect, and pings
    web-server-b's private IP from inside web-server-a. PASS requires 0%
    packet loss.
    """
    label = "EC2 instances can communicate through the VPC Peering Connection"
    instance_a = _find_running_instance(INSTANCE_A_NAME)
    instance_b = _find_running_instance(INSTANCE_B_NAME)

    if not instance_a or not instance_b:
        print(f"[FAIL] {label}")
        print("Reason:")
        print("  - One or both instances (web-server-a, web-server-b) were not found in a Running state")
        return False

    public_ip_a = instance_a.get('PublicIpAddress')
    private_ip_b = instance_b.get('PrivateIpAddress')

    if not public_ip_a or not private_ip_b:
        print(f"[FAIL] {label}")
        print("Reason:")
        print("  - web-server-a has no public IP, or web-server-b has no private IP")
        return False

    with tempfile.TemporaryDirectory() as tmp_dir:
        key_path = os.path.join(tmp_dir, "lab_2_5_key")
        try:
            push_temporary_ssh_key(instance_a, key_path)
            returncode, stdout, stderr = run_remote_ping(public_ip_a, private_ip_b, key_path)
        except Exception as e:
            print(f"[FAIL] {label}")
            print("Reason:")
            print(f"  - Could not establish a connection to run the ping test: {e}")
            return False

    passed = returncode == 0 and "0% packet loss" in stdout

    if passed:
        print(f"[PASS] {label}")
    else:
        print(f"[FAIL] Connectivity test failed")
        print("Reason:")
        print("  - Ping unsuccessful")
        print("  - Route table misconfiguration")
        print("  - Security Group blocking ICMP")
        print("  - Network ACL blocking ICMP")

    print("Captured output:")
    print(stdout)
    if stderr:
        print(stderr)

    return passed

def run_test_cases(credentials):
    global ec2, ec2_instance_connect
    ec2 = boto3.client(
        'ec2',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )
    ec2_instance_connect = boto3.client(
        'ec2-instance-connect',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )
    print("=" * 60)
    print("LAB 2.5 VALIDATION: VPC Peering Connection Between Two VPCs")
    print("=" * 60)

    test_two_custom_vpcs_exist()
    test_vpc_a_name()
    test_vpc_a_cidr()
    test_vpc_b_name()
    test_vpc_b_cidr()
    test_subnet_a_cidr()
    test_subnet_b_cidr()
    test_internet_gateway_attached_to_vpc_a()
    test_internet_gateway_attached_to_vpc_b()
    test_route_table_configured_for_vpc_a()
    test_route_table_configured_for_vpc_b()
    test_instance_exists_in_vpc_a()
    test_instance_exists_in_vpc_b()
    test_peering_connection_exists()
    test_peering_connection_active()
    test_route_table_a_routes_to_peer()
    test_route_table_b_routes_to_peer()
    test_security_group_a_allows_icmp()
    test_security_group_b_allows_icmp()
    test_end_to_end_connectivity()

    print("=" * 60)
