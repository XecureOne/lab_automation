import boto3

ec2 = ""

AMI_NAME      = "php-web-server-ami"
LT_NAME       = "php-web-lt"
NEW_INSTANCE  = "php-web-server-from-ami"

def result(label, passed):
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}")
    return passed

def validate_ami():
    try:
        r = ec2.describe_images(
            Owners=['self'],
            Filters=[{'Name': 'name',  'Values': [AMI_NAME]},
                     {'Name': 'state', 'Values': ['available']}]
        )
        images = r.get('Images', [])
        ami = images[0] if images else None
        result(f"Custom AMI '{AMI_NAME}' exists and available", ami is not None)
    except Exception as e:
        result(f"AMI check ERROR: {e}", False)
        return None

    if not ami:
        result("AMI is private (not public)", False)
        return None

    result("AMI is private (not public)", not ami.get('Public', True))

    return ami

def validate_launch_template(ami):
    try:
        r = ec2.describe_launch_templates(
            Filters=[{'Name': 'launch-template-name', 'Values': [LT_NAME]}]
        )
        lts = r.get('LaunchTemplates', [])
        lt = lts[0] if lts else None
        result(f"Launch Template '{LT_NAME}' exists", lt is not None)
    except Exception as e:
        result(f"Launch Template check ERROR: {e}", False)
        return None

    if not ami or not lt:
        result("Launch Template references custom AMI", False)
        return lt

    try:
        r = ec2.describe_launch_template_versions(
            LaunchTemplateId=lt['LaunchTemplateId'],
            Versions=['$Latest']
        )
        versions = r.get('LaunchTemplateVersions', [])
        lt_ami_id = versions[0].get('LaunchTemplateData', {}).get('ImageId', '') if versions else ''
        result("Launch Template references custom AMI",
               lt_ami_id == ami.get('ImageId', ''))
    except Exception as e:
        result(f"Launch Template AMI check ERROR: {e}", False)

    return lt

def validate_instance():
    try:
        r = ec2.describe_instances(Filters=[
            {'Name': 'tag:Name',            'Values': [NEW_INSTANCE]},
            {'Name': 'instance-state-name', 'Values': ['running']}
        ])
        instances = [
            inst
            for res in r.get('Reservations', [])
            for inst in res['Instances']
        ]
        instance = instances[0] if instances else None
        result(f"Instance '{NEW_INSTANCE}' launched and running", instance is not None)
        return instance
    except Exception as e:
        result(f"Instance check ERROR: {e}", False)
        return None

def run_test_cases(credentials):
    global ec2
    ec2 = boto3.client('ec2',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    print("=" * 60)
    print("LAB 3_5 VALIDATION: Custom AMI Creation and Launch")
    print("=" * 60)
    ami = validate_ami()
    print()
    validate_launch_template(ami)
    print()
    validate_instance()
    print("=" * 60)
