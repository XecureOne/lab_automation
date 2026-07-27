import boto3

ec2 = ""

AMI_NAME      = "php-web-server-ami"
LT_NAME       = "php-web-lt"
NEW_INSTANCE  = "php-web-server-from-ami"

def result(label, passed):
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}")
    return passed

def test_ami_exists():
    try:
        r = ec2.describe_images(
            Owners=['self'],
            Filters=[{'Name': 'name',  'Values': [AMI_NAME]},
                     {'Name': 'state', 'Values': ['available']}]
        )
        images = r.get('Images', [])
        result(f"Custom AMI '{AMI_NAME}' exists and available", len(images) > 0)
        return images[0] if images else None
    except Exception as e:
        result(f"AMI check ERROR: {e}", False)
        return None

def test_ami_private(ami):
    if not ami:
        return result("AMI is private (not public)", False)
    public = ami.get('Public', True)
    return result("AMI visibility is Private", not public)

def test_ami_tags(ami):
    if not ami:
        return result("AMI has Project tag", False)
    tags = {t['Key']: t['Value'] for t in ami.get('Tags', [])}
    result("AMI has tag Project=PHP-WebApp",
           tags.get('Project') == 'PHP-WebApp')
    result("AMI has tag Version=1.0",
           tags.get('Version') == '1.0')

def test_ami_has_snapshot(ami):
    if not ami:
        return result("AMI backed by encrypted EBS snapshot", False)
    for mapping in ami.get('BlockDeviceMappings', []):
        ebs = mapping.get('Ebs', {})
        if ebs:
            encrypted = ebs.get('Encrypted', False)
            snap_id   = ebs.get('SnapshotId', '')
            result(f"AMI root snapshot ({snap_id}) exists", bool(snap_id))
            return result("AMI root snapshot is encrypted", encrypted)
    return result("AMI has EBS block device mapping", False)

def test_launch_template_exists():
    try:
        r = ec2.describe_launch_templates(
            Filters=[{'Name': 'launch-template-name', 'Values': [LT_NAME]}]
        )
        lts = r.get('LaunchTemplates', [])
        result(f"Launch Template '{LT_NAME}' exists", len(lts) > 0)
        return lts[0] if lts else None
    except Exception as e:
        result(f"Launch Template check ERROR: {e}", False)
        return None

def test_lt_uses_custom_ami(ami, lt):
    if not ami or not lt:
        return result("Launch Template references custom AMI", False)
    try:
        r = ec2.describe_launch_template_versions(
            LaunchTemplateId=lt['LaunchTemplateId'],
            Versions=['$Latest']
        )
        versions = r.get('LaunchTemplateVersions', [])
        if not versions:
            return result("Launch Template has versions", False)
        data       = versions[0].get('LaunchTemplateData', {})
        lt_ami_id  = data.get('ImageId', '')
        custom_ami = ami.get('ImageId', '')
        return result(f"Launch Template uses custom AMI ID ({custom_ami})",
                      lt_ami_id == custom_ami)
    except Exception as e:
        return result(f"LT AMI check ERROR: {e}", False)

def test_new_instance_running():
    try:
        r = ec2.describe_instances(Filters=[
            {'Name': 'tag:Name',            'Values': [NEW_INSTANCE]},
            {'Name': 'instance-state-name', 'Values': ['running']}
        ])
        found = any(r.get('Reservations', []))
        return result(f"Instance '{NEW_INSTANCE}' launched and running", found)
    except Exception as e:
        return result(f"New instance check ERROR: {e}", False)

def test_new_instance_tag(ami):
    if not ami:
        return result("New instance has LaunchedFrom tag", False)
    try:
        r = ec2.describe_instances(Filters=[
            {'Name': 'tag:Name',            'Values': [NEW_INSTANCE]},
            {'Name': 'instance-state-name', 'Values': ['running']}
        ])
        for res in r.get('Reservations', []):
            for inst in res['Instances']:
                image_id = inst.get('ImageId', '')
                custom   = ami.get('ImageId', '')
                return result(
                    f"New instance uses custom AMI ({custom})",
                    image_id == custom)
        return result(f"New instance found for AMI check", False)
    except Exception as e:
        return result(f"New instance AMI check ERROR: {e}", False)

def run_test_cases(credentials):
    global ec2
    ec2 = boto3.client('ec2',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    print("=" * 60)
    print("LAB 14 VALIDATION: Custom AMI Creation and Launch")
    print("=" * 60)
    ami = test_ami_exists()
    test_ami_private(ami)
    test_ami_tags(ami)
    test_ami_has_snapshot(ami)
    print()
    lt = test_launch_template_exists()
    test_lt_uses_custom_ami(ami, lt)
    print()
    test_new_instance_running()
    test_new_instance_tag(ami)
    print("=" * 60)