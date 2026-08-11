import boto3
import time

ec2 = ''

INSTANCE_NAME = "database-server"
VOLUME_NAME = "database-volume"
VOLUME_TYPE = "gp3"
VOLUME_SIZE_GIB = 10
DEVICE_NAME = "/dev/sdf"

def result(label, passed):
    status = "[PASS]" if passed else "[FAIL]"
    print(f"{status} {label}")
    return passed

# ---------------------------------------------------------------------------
# Pre-lab provisioning. This lab is scoped to teach EBS volume creation and
# attachment only, so the EC2 instance is prepared here before the student
# begins - idempotent, so it is safe to call again if the account is
# already provisioned. Standalone and callable on its own (does not depend
# on run_test_cases having run first).
# ---------------------------------------------------------------------------

POLL_INTERVAL_SECONDS = 5
MAX_WAIT_SECONDS = 20 * 60

def _poll_until(check_fn, timeout_message, timeout_seconds=MAX_WAIT_SECONDS, interval_seconds=POLL_INTERVAL_SECONDS):
    """
    Polls check_fn() every interval_seconds until it returns a truthy
    value, then returns that value. Raises TimeoutError with
    timeout_message if timeout_seconds elapses first. No fixed sleeps
    outside of this bounded retry loop.
    """
    deadline = time.time() + timeout_seconds
    while True:
        value = check_fn()
        if value:
            return value
        if time.time() >= deadline:
            raise TimeoutError(timeout_message)
        time.sleep(interval_seconds)

def _ensure_device_name_available(ec2_client, instance_id):
    """
    Confirms DEVICE_NAME ('/dev/sdf') is not already used by an existing
    block device mapping on this instance. Checked immediately before
    attaching. Raises if it's somehow already taken, rather than silently
    picking a different device name.
    """
    instances = ec2_client.describe_instances(InstanceIds=[instance_id]).get('Reservations', [])
    if not instances:
        raise RuntimeError(f"Instance {instance_id} not found while checking device name availability.")
    in_use = {
        mapping['DeviceName']
        for mapping in instances[0]['Instances'][0].get('BlockDeviceMappings', [])
    }
    if DEVICE_NAME in in_use:
        raise RuntimeError(f"Device name '{DEVICE_NAME}' is already in use on instance {instance_id}.")

def _find_instance_for_provisioning(ec2_client):
    instances = [
        i for r in ec2_client.describe_instances(
            Filters=[{'Name': 'tag:Name', 'Values': [INSTANCE_NAME]}]
        ).get('Reservations', []) for i in r.get('Instances', [])
        if i.get('State', {}).get('Name') != 'terminated'
    ]
    return instances[0] if instances else None

def provision_lab_environment(credentials):
    """
    This lab is designed for the STUDENT to launch the EC2 instance
    ("database-server") manually - deployment must NOT create it. Instead,
    this polls (bounded retries, no fixed sleeps) until the student's
    instance exists and reaches Running, then creates the EBS volume in
    the same Availability Zone, waits for it to become Available, attaches
    it to the instance at DEVICE_NAME ('/dev/sdf', confirmed unused
    immediately before attaching), and waits for the attachment to report
    'attached' before returning. Idempotent - safe to call again; each
    phase is skipped if already satisfied.
    """
    ec2_client = boto3.client(
        'ec2',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )

    print(f"Waiting for the student to create EC2 instance '{INSTANCE_NAME}'...")
    instance = _poll_until(
        lambda: _find_instance_for_provisioning(ec2_client),
        timeout_message=f"Timed out waiting for EC2 instance '{INSTANCE_NAME}' to be created.",
    )
    instance_id = instance['InstanceId']
    print(f"Found instance '{INSTANCE_NAME}' ({instance_id}), state = {instance['State']['Name']}.")

    def _instance_is_running():
        current = ec2_client.describe_instances(InstanceIds=[instance_id])
        reservations = current.get('Reservations', [])
        if not reservations:
            return None
        state = reservations[0]['Instances'][0]['State']['Name']
        return reservations[0]['Instances'][0] if state == 'running' else None

    print(f"Waiting for '{INSTANCE_NAME}' to reach the Running state...")
    instance = _poll_until(
        _instance_is_running,
        timeout_message=f"Timed out waiting for EC2 instance '{INSTANCE_NAME}' to reach the Running state.",
    )
    availability_zone = instance['Placement']['AvailabilityZone']
    print(f"Instance '{INSTANCE_NAME}' is Running in {availability_zone}.")

    existing_volumes = ec2_client.describe_volumes(
        Filters=[{'Name': 'tag:Name', 'Values': [VOLUME_NAME]}, {'Name': 'availability-zone', 'Values': [availability_zone]}]
    ).get('Volumes', [])
    if existing_volumes:
        volume_id = existing_volumes[0]['VolumeId']
        print(f"Volume '{VOLUME_NAME}' already exists ({volume_id}).")
    else:
        volume_id = ec2_client.create_volume(
            AvailabilityZone=availability_zone,
            VolumeType=VOLUME_TYPE,
            Size=VOLUME_SIZE_GIB,
            TagSpecifications=[{'ResourceType': 'volume', 'Tags': [{'Key': 'Name', 'Value': VOLUME_NAME}]}],
        )['VolumeId']
        print(f"Created volume '{VOLUME_NAME}' ({volume_id}) in {availability_zone}.")

    def _volume_is_available():
        volumes = ec2_client.describe_volumes(VolumeIds=[volume_id]).get('Volumes', [])
        return volumes[0] if volumes and volumes[0]['State'] == 'available' else None

    print(f"Waiting for volume '{VOLUME_NAME}' to become Available...")
    _poll_until(
        _volume_is_available,
        timeout_message=f"Timed out waiting for EBS volume '{VOLUME_NAME}' to become Available.",
    )
    print(f"Volume '{VOLUME_NAME}' is Available.")

    already_attached = any(
        a.get('InstanceId') == instance_id and a.get('State') in ('attaching', 'attached')
        for a in ec2_client.describe_volumes(VolumeIds=[volume_id]).get('Volumes', [{}])[0].get('Attachments', [])
    )
    if not already_attached:
        _ensure_device_name_available(ec2_client, instance_id)
        ec2_client.attach_volume(VolumeId=volume_id, InstanceId=instance_id, Device=DEVICE_NAME)
        print(f"Requested attachment of '{VOLUME_NAME}' to '{INSTANCE_NAME}' at {DEVICE_NAME}.")

    def _attachment_is_attached():
        volumes = ec2_client.describe_volumes(VolumeIds=[volume_id]).get('Volumes', [])
        if not volumes:
            return False
        attachments = volumes[0].get('Attachments', [])
        return any(a.get('InstanceId') == instance_id and a.get('State') == 'attached' for a in attachments)

    print(f"Waiting for volume '{VOLUME_NAME}' attachment state to become 'attached'...")
    _poll_until(
        _attachment_is_attached,
        timeout_message=f"Timed out waiting for EBS volume '{VOLUME_NAME}' to reach the 'attached' state.",
    )
    print(f"Volume '{VOLUME_NAME}' is attached to '{INSTANCE_NAME}' at {DEVICE_NAME}. Deployment complete.")

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

def test_instance_exists():
    instance = _find_instance()
    return result(f"EC2 instance '{INSTANCE_NAME}' exists", instance is not None)

def test_instance_name():
    instance = _find_instance()
    if not instance:
        return result(f"EC2 instance Name tag = '{INSTANCE_NAME}'", False)
    tags = {t['Key']: t['Value'] for t in instance.get('Tags', [])}
    return result(f"EC2 instance Name tag = '{INSTANCE_NAME}'", tags.get('Name') == INSTANCE_NAME)

def test_instance_running():
    instance = _find_instance()
    if not instance:
        return result("EC2 instance state = Running", False)
    return result("EC2 instance state = Running", instance.get('State', {}).get('Name') == 'running')

def test_instance_uses_default_subnet():
    """
    Independent lookup: re-finds the instance itself rather than depending
    on any other test case's result, retrieves its SubnetId, and checks
    that subnet's DefaultForAz attribute directly via DescribeSubnets.
    """
    instance = _find_instance()
    label = "EC2 instance is using a default subnet"
    if not instance:
        return result(label, False)
    subnet_id = instance.get('SubnetId')
    if not subnet_id:
        return result(label, False)
    try:
        subnets = ec2.describe_subnets(SubnetIds=[subnet_id]).get('Subnets', [])
    except Exception as e:
        return result(f"{label} - ERROR: {e}", False)
    is_default = bool(subnets) and subnets[0].get('DefaultForAz', False) is True
    return result(label, is_default)

def _find_non_root_volume_by_name():
    """
    Independent lookup: finds the volume tagged Name=database-volume,
    excluding any volume that is a root device (device name matches the
    instance's RootDeviceName) so default root volumes are never
    considered.
    """
    volumes = ec2.describe_volumes(Filters=[{'Name': 'tag:Name', 'Values': [VOLUME_NAME]}]).get('Volumes', [])
    non_root = []
    for volume in volumes:
        is_root = False
        for attachment in volume.get('Attachments', []):
            instance_id = attachment.get('InstanceId')
            device = attachment.get('Device')
            if instance_id:
                instances = ec2.describe_instances(InstanceIds=[instance_id]).get('Reservations', [])
                root_device = instances[0]['Instances'][0].get('RootDeviceName') if instances else None
                if device == root_device:
                    is_root = True
        if not is_root:
            non_root.append(volume)
    return non_root[0] if len(non_root) == 1 else None

def test_single_data_volume_exists():
    volume = _find_non_root_volume_by_name()
    return result(f"One additional EBS volume named '{VOLUME_NAME}' exists (root volumes ignored)", volume is not None)

def test_volume_type():
    volume = _find_non_root_volume_by_name()
    if not volume:
        return result(f"Volume type = '{VOLUME_TYPE}'", False)
    return result(f"Volume type = '{VOLUME_TYPE}'", volume.get('VolumeType') == VOLUME_TYPE)

def test_volume_size():
    volume = _find_non_root_volume_by_name()
    if not volume:
        return result(f"Volume size = {VOLUME_SIZE_GIB} GiB", False)
    return result(f"Volume size = {VOLUME_SIZE_GIB} GiB", volume.get('Size') == VOLUME_SIZE_GIB)

def test_volume_state_in_use():
    volume = _find_non_root_volume_by_name()
    if not volume:
        return result("Volume state = in-use", False)
    return result("Volume state = in-use", volume.get('State') == 'in-use')

def test_volume_attached_to_database_server():
    volume = _find_non_root_volume_by_name()
    instance = _find_instance()
    label = f"Volume is attached to '{INSTANCE_NAME}'"
    if not volume or not instance:
        return result(label, False)
    attached_instance_ids = [a.get('InstanceId') for a in volume.get('Attachments', [])]
    return result(label, instance['InstanceId'] in attached_instance_ids)

def test_attachment_state_attached():
    volume = _find_non_root_volume_by_name()
    instance = _find_instance()
    label = "Attachment state = attached"
    if not volume or not instance:
        return result(label, False)
    attachment_states = [
        a.get('State') for a in volume.get('Attachments', []) if a.get('InstanceId') == instance['InstanceId']
    ]
    return result(label, 'attached' in attachment_states)

def test_device_name():
    volume = _find_non_root_volume_by_name()
    label = f"Device name = '{DEVICE_NAME}'"
    if not volume:
        return result(label, False)
    device_names = [a.get('Device') for a in volume.get('Attachments', [])]
    return result(label, DEVICE_NAME in device_names)

def run_test_cases(credentials):
    global ec2
    ec2 = boto3.client(
        'ec2',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )
    print("=" * 60)
    print("LAB 3.3 VALIDATION: Set Up Amazon EBS Volumes for Databases and Critical Data")
    print("=" * 60)

    test_instance_exists()
    test_instance_name()
    test_instance_running()
    test_instance_uses_default_subnet()
    test_single_data_volume_exists()
    test_volume_type()
    test_volume_size()
    test_volume_state_in_use()
    test_volume_attached_to_database_server()
    test_attachment_state_attached()
    test_device_name()

    print("=" * 60)
