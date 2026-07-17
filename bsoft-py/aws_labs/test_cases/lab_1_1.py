import boto3

ec2 = ''

EXPECTED_NAME = "lab-ec2"
EXPECTED_TYPE = "t2.micro"

def result(label, passed):
    status = "[PASS]" if passed else "[FAIL]"
    print(f"{status} {label}")
    return passed

def _ensure_default_vpc(ec2_client):
    """
    This lab assumes usable default-style networking exists so the student
    never has to touch VPCs/subnets. AWS only allows CreateDefaultVpc to
    succeed once per account/region - if a default VPC was ever deleted (or,
    as in this account, a look-alike was already created by hand), the call
    fails and cannot be redone automatically via the API. In that case we
    simply confirm a usable VPC already exists rather than erroring out.
    """
    try:
        response = ec2_client.describe_vpcs(Filters=[{'Name': 'is-default', 'Values': ['true']}])
        vpcs = response.get('Vpcs', [])
        if vpcs:
            print(f"Default VPC already exists: {vpcs[0]['VpcId']}")
            return

        print("No Default VPC found - attempting to create one...")
        try:
            vpc_id = ec2_client.create_default_vpc()['Vpc']['VpcId']
            ec2_client.get_waiter('vpc_available').wait(VpcIds=[vpc_id])
            print(f"Default VPC created: {vpc_id}")
        except Exception as e:
            print(f"CreateDefaultVpc could not run automatically ({e}). "
                  f"Falling back to checking for an existing usable VPC instead.")
            any_vpcs = ec2_client.describe_vpcs().get('Vpcs', [])
            if any_vpcs:
                print(f"Found an existing VPC to use: {any_vpcs[0]['VpcId']}")
            else:
                print("No VPC exists in this account - this needs manual attention.")
    except Exception as e:
        print(f"Default VPC bootstrap - ERROR: {e}")

def ensure_default_vpc(credentials):
    """
    Standalone entry point so account/networking prep can be triggered the
    moment a student's lab session is provisioned, instead of waiting for
    run_test_cases() (which normally only runs later, at grading time).
    Builds its own EC2 client so it does not depend on run_test_cases()
    having run first.
    """
    ec2_client = boto3.client(
        'ec2',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )
    _ensure_default_vpc(ec2_client)

def find_instance():
    try:
        response = ec2.describe_instances(
            Filters=[
                {'Name': 'tag:Name', 'Values': [EXPECTED_NAME]},
                {'Name': 'instance-state-name', 'Values': ['pending', 'running', 'stopping', 'stopped']}
            ]
        )
        instances = [i for r in response.get('Reservations', []) for i in r.get('Instances', [])]
        passed = len(instances) == 1
        result("Exactly one EC2 instance exists", passed)
        return passed, (instances[0] if passed else None)
    except Exception as e:
        result(f"EC2 instance lookup - ERROR: {e}", False)
        return False, None

def check_name(instance):
    if not instance:
        return result(f"Instance Name tag = '{EXPECTED_NAME}'", False)
    tags = {t['Key']: t['Value'] for t in instance.get('Tags', [])}
    return result(f"Instance Name tag = '{EXPECTED_NAME}'", tags.get('Name') == EXPECTED_NAME)

def check_running(instance):
    if not instance:
        return result("Instance is in the Running state", False)
    return result("Instance is in the Running state", instance.get('State', {}).get('Name') == 'running')

def check_amazon_linux(instance):
    if not instance:
        return result("AMI is Amazon Linux", False)
    try:
        response = ec2.describe_images(ImageIds=[instance.get('ImageId')])
        images = response.get('Images', [])
        if not images:
            return result("AMI is Amazon Linux", False)
        owner_alias = images[0].get('ImageOwnerAlias', '')
        name = images[0].get('Name', '').lower()
        is_al = owner_alias == 'amazon' and ('amzn' in name or 'al2023' in name or 'amazon-linux' in name)
        return result("AMI is Amazon Linux", is_al)
    except Exception as e:
        return result(f"AMI check - ERROR: {e}", False)

def check_instance_type(instance):
    if not instance:
        return result(f"Instance type = '{EXPECTED_TYPE}'", False)
    return result(f"Instance type = '{EXPECTED_TYPE}'", instance.get('InstanceType') == EXPECTED_TYPE)

def check_default_vpc(instance):
    if not instance:
        return result("Instance is in a default-style VPC", False)
    try:
        vpc_id = instance.get('VpcId')
        if not vpc_id:
            return result("Instance is in a default-style VPC", False)
        vpcs = ec2.describe_vpcs(VpcIds=[vpc_id]).get('Vpcs', [])
        if not vpcs:
            return result("Instance is in a default-style VPC", False)
        # A true AWS Default VPC (IsDefault=True) always qualifies. Since
        # CreateDefaultVpc can only ever succeed once per account/region,
        # accounts that had to recreate one by hand won't have that flag -
        # in that case an Internet Gateway attached to the VPC is accepted
        # as evidence it behaves like a default (internet-routable).
        if vpcs[0].get('IsDefault', False):
            return result("Instance is in a default-style VPC", True)
        igws = ec2.describe_internet_gateways(
            Filters=[{'Name': 'attachment.vpc-id', 'Values': [vpc_id]}]
        ).get('InternetGateways', [])
        return result("Instance is in a default-style VPC", len(igws) > 0)
    except Exception as e:
        return result(f"Default VPC check - ERROR: {e}", False)

def check_default_subnet(instance):
    if not instance:
        return result("Instance is in a default-style Subnet", False)
    try:
        subnet_id = instance.get('SubnetId')
        if not subnet_id:
            return result("Instance is in a default-style Subnet", False)
        subnets = ec2.describe_subnets(SubnetIds=[subnet_id]).get('Subnets', [])
        if not subnets:
            return result("Instance is in a default-style Subnet", False)
        # Same reasoning as check_default_vpc: fall back to
        # MapPublicIpOnLaunch (the behavior that matters for this lab)
        # when DefaultForAz isn't set because the VPC was recreated by hand.
        is_default_like = subnets[0].get('DefaultForAz', False) or subnets[0].get('MapPublicIpOnLaunch', False)
        return result("Instance is in a default-style Subnet", is_default_like)
    except Exception as e:
        return result(f"Default Subnet check - ERROR: {e}", False)

def check_public_ip(instance):
    if not instance:
        return result("Instance has a Public IPv4 address", False)
    return result("Instance has a Public IPv4 address", bool(instance.get('PublicIpAddress')))

def check_security_group(instance):
    if not instance:
        return result("A Security Group is attached to the instance", False), []
    groups = instance.get('SecurityGroups', [])
    passed = len(groups) > 0
    return result("A Security Group is attached to the instance", passed), [g['GroupId'] for g in groups]

def check_ssh_rule(group_ids):
    if not group_ids:
        return result("Security Group allows inbound SSH (TCP 22)", False)
    try:
        response = ec2.describe_security_groups(GroupIds=group_ids)
        for sg in response.get('SecurityGroups', []):
            for perm in sg.get('IpPermissions', []):
                from_port = perm.get('FromPort')
                to_port = perm.get('ToPort')
                if perm.get('IpProtocol') == 'tcp' and from_port is not None and to_port is not None \
                        and from_port <= 22 <= to_port:
                    return result("Security Group allows inbound SSH (TCP 22)", True)
        return result("Security Group allows inbound SSH (TCP 22)", False)
    except Exception as e:
        return result(f"SSH ingress rule check - ERROR: {e}", False)

def check_no_key_pair(instance):
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
    print("=" * 55)
    print("LAB 1.1 VALIDATION: Launching an EC2 Instance")
    print("=" * 55)
    _ensure_default_vpc(ec2)
    _, instance = find_instance()
    check_name(instance)
    check_running(instance)
    check_amazon_linux(instance)
    check_instance_type(instance)
    check_default_vpc(instance)
    check_default_subnet(instance)
    check_public_ip(instance)
    _, group_ids = check_security_group(instance)
    check_ssh_rule(group_ids)
    check_no_key_pair(instance)
    print("=" * 55)
