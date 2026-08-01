import boto3

ec2 = ''

INSTANCE_NAME = "web-server"
INSTANCE_TYPE = "t2.micro"
SECURITY_GROUP_NAME = "web-server-sg"
SSH_PORT = 22
EXPECTED_VOLUME_SIZE_GIB = 8
EXPECTED_VOLUME_TYPE = "gp3"

def result(label, passed):
    status = "[PASS]" if passed else "[FAIL]"
    print(f"{status} {label}")
    return passed

# ---------------------------------------------------------------------------
# Every test case performs its own independent AWS lookup by Name tag. None
# of them reuse an object discovered by another test case, so deleting one
# resource only fails the test case(s) that specifically search for it.
# ---------------------------------------------------------------------------

def _find_instance():
    response = ec2.describe_instances(
        Filters=[
            {'Name': 'tag:Name', 'Values': [INSTANCE_NAME]},
            {'Name': 'instance-state-name', 'Values': ['pending', 'running', 'stopping', 'stopped']},
        ]
    )
    instances = [i for r in response.get('Reservations', []) for i in r.get('Instances', [])]
    return instances[0] if len(instances) == 1 else None

def test_single_instance_exists():
    try:
        response = ec2.describe_instances(
            Filters=[
                {'Name': 'tag:Name', 'Values': [INSTANCE_NAME]},
                {'Name': 'instance-state-name', 'Values': ['pending', 'running', 'stopping', 'stopped']},
            ]
        )
        instances = [i for r in response.get('Reservations', []) for i in r.get('Instances', [])]
        return result(f"Exactly one EC2 instance named '{INSTANCE_NAME}' exists", len(instances) == 1)
    except Exception as e:
        return result(f"EC2 instance lookup - ERROR: {e}", False)

def test_instance_name_tag():
    instance = _find_instance()
    if not instance:
        return result(f"Instance Name tag = '{INSTANCE_NAME}'", False)
    tags = {t['Key']: t['Value'] for t in instance.get('Tags', [])}
    return result(f"Instance Name tag = '{INSTANCE_NAME}'", tags.get('Name') == INSTANCE_NAME)

def test_instance_running():
    instance = _find_instance()
    if not instance:
        return result("Instance is in the Running state", False)
    return result("Instance is in the Running state", instance.get('State', {}).get('Name') == 'running')

def test_amazon_linux_ami():
    instance = _find_instance()
    if not instance:
        return result("AMI is Amazon Linux", False)
    try:
        images = ec2.describe_images(ImageIds=[instance.get('ImageId')]).get('Images', [])
        if not images:
            return result("AMI is Amazon Linux", False)
        owner_alias = images[0].get('ImageOwnerAlias', '')
        name = images[0].get('Name', '').lower()
        is_al = owner_alias == 'amazon' and ('amzn' in name or 'al2023' in name or 'amazon-linux' in name)
        return result("AMI is Amazon Linux", is_al)
    except Exception as e:
        return result(f"AMI check - ERROR: {e}", False)

def test_instance_type():
    instance = _find_instance()
    if not instance:
        return result(f"Instance type = '{INSTANCE_TYPE}'", False)
    return result(f"Instance type = '{INSTANCE_TYPE}'", instance.get('InstanceType') == INSTANCE_TYPE)

def test_instance_in_default_vpc():
    instance = _find_instance()
    if not instance:
        return result("Instance is in the Default VPC", False)
    try:
        vpc_id = instance.get('VpcId')
        if not vpc_id:
            return result("Instance is in the Default VPC", False)
        vpcs = ec2.describe_vpcs(VpcIds=[vpc_id]).get('Vpcs', [])
        is_default = bool(vpcs) and vpcs[0].get('IsDefault', False)
        return result("Instance is in the Default VPC", is_default)
    except Exception as e:
        return result(f"Default VPC check - ERROR: {e}", False)

def test_public_ip_assigned():
    instance = _find_instance()
    if not instance:
        return result("Instance has a Public IPv4 address", False)
    return result("Instance has a Public IPv4 address", bool(instance.get('PublicIpAddress')))

def test_security_group_attached():
    instance = _find_instance()
    if not instance:
        return result("A Security Group is attached to the instance", False)
    groups = instance.get('SecurityGroups', [])
    return result("A Security Group is attached to the instance", len(groups) > 0)

def test_security_group_allows_ssh():
    instance = _find_instance()
    label = f"Security Group allows inbound SSH (TCP {SSH_PORT})"
    if not instance:
        return result(label, False)
    try:
        group_ids = [g['GroupId'] for g in instance.get('SecurityGroups', [])]
        if not group_ids:
            return result(label, False)
        groups = ec2.describe_security_groups(GroupIds=group_ids).get('SecurityGroups', [])
        allows_ssh = any(
            perm.get('IpProtocol') == 'tcp'
            and perm.get('FromPort') is not None and perm.get('ToPort') is not None
            and perm.get('FromPort') <= SSH_PORT <= perm.get('ToPort')
            for sg in groups
            for perm in sg.get('IpPermissions', [])
        )
        return result(label, allows_ssh)
    except Exception as e:
        return result(f"Security Group SSH rule check - ERROR: {e}", False)

def test_root_volume_size_and_type():
    instance = _find_instance()
    label = f"Root volume = {EXPECTED_VOLUME_SIZE_GIB} GiB, type = {EXPECTED_VOLUME_TYPE}"
    if not instance:
        return result(label, False)
    try:
        mappings = instance.get('BlockDeviceMappings', [])
        root_device = instance.get('RootDeviceName')
        volume_id = next(
            (m['Ebs']['VolumeId'] for m in mappings if m.get('DeviceName') == root_device and m.get('Ebs')),
            None,
        )
        if not volume_id:
            return result(label, False)
        volumes = ec2.describe_volumes(VolumeIds=[volume_id]).get('Volumes', [])
        if not volumes:
            return result(label, False)
        size_ok = volumes[0].get('Size') == EXPECTED_VOLUME_SIZE_GIB
        type_ok = volumes[0].get('VolumeType') == EXPECTED_VOLUME_TYPE
        return result(label, size_ok and type_ok)
    except Exception as e:
        return result(f"Root volume check - ERROR: {e}", False)

def test_no_key_pair():
    instance = _find_instance()
    if not instance:
        return result("No Key Pair attached (expected for this lab)", False)
    return result("No Key Pair attached (expected for this lab)", not instance.get('KeyName'))

def run_test_cases(credentials):
    global ec2
    ec2 = boto3.client(
        'ec2',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )
    print("=" * 60)
    print("LAB 3.1 VALIDATION: Create and Configure EC2 Instances")
    print("=" * 60)

    test_single_instance_exists()
    test_instance_name_tag()
    test_instance_running()
    test_amazon_linux_ami()
    test_instance_type()
    test_instance_in_default_vpc()
    test_public_ip_assigned()
    test_security_group_attached()
    test_security_group_allows_ssh()
    test_root_volume_size_and_type()
    test_no_key_pair()

    print("=" * 60)
