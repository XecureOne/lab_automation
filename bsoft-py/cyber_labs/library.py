import boto3

REGION = "ap-south-1"
VPC_ID = "vpc-0cbebf51c551f38ce"
SUBNET_ID = "subnet-0fc21fe8536929506"
INSTANCE_TYPE = "t2.micro"

ec2 = boto3.client("ec2", region_name=REGION)

def start_instance(student_id,image_id):
    try:
        response = ec2.get_security_groups_for_vpc(
        VpcId=VPC_ID)

        for i in response['SecurityGroupForVpcs']:
            if i['GroupName'] == f"{student_id} security group":
                raise
    except Exception as e:
        print("Active lab session found...clear up all your previous sessions!!")
        return
    sg_response = ec2.create_security_group(
    GroupName=f"{student_id} security group",
    Description=f"{student_id} security group",
    VpcId=VPC_ID
    )
    security_group_id = sg_response["GroupId"]
    print("Created Security Group:", security_group_id)

    response = ec2.run_instances(
    ImageId=f'{image_id}',
    InstanceType=INSTANCE_TYPE,
    MinCount=1,
    MaxCount=1,

    NetworkInterfaces=[
        {
            "DeviceIndex": 0,
            "SubnetId": SUBNET_ID,
            "AssociatePublicIpAddress": False,
            "Groups": [security_group_id]
        }
    ]
)
    instance = response["Instances"][0]

    instance_id = instance['InstanceId']
    private_ip = instance['PrivateIpAddress']
    print("Instance Launched:", instance_id)
    return { "instance_id": instance_id,"sg_id": security_group_id, "ip": private_ip}


def stop_instance(instance_id):
    flag = False
    print("Initiated Termination of instance!!")
    response = ec2.describe_instances(
    InstanceIds=[instance_id]
    )
    security_groups = response["Reservations"][0]["Instances"][0]["SecurityGroups"]
    if response:
        ec2.terminate_instances(
        InstanceIds=[instance_id]
        )
        waiter = ec2.get_waiter('instance_terminated')
        waiter.wait(
            InstanceIds=[instance_id]
        )
        flag = True
        print(f"Terminated instance: {instance_id}")

    for i in security_groups:
        ec2.delete_security_group(
        GroupId=i["GroupId"]
        )
        print(f"Deleted security group {i['GroupId']}")
    return flag

        
