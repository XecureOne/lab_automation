import boto3
import time
import sys
import random

REGION = "ap-south-1"
VPC_ID = "vpc-048641cb6ee426d64"
SUBNET_ID = "subnet-0b2892fad459957a4"
INSTANCE_TYPE = ""

ec2 = boto3.client("ec2", region_name=REGION)

def start_instance(lab, lab_type, student_id, image_id, inst_type, tier):
    global INSTANCE_TYPE
    INSTANCE_TYPE = tier
    if INSTANCE_TYPE:
        pass 
    else:
        print("Invalid lab option!!")
        sys.exit(0)

    LAB_SUBNETS = [
        "subnet-07c48cdb471273634",
        "subnet-0b2892fad459957a4",
        "subnet-0a8911f4c52ccf505"
    ]


    SUBNETS = [
    "subnet-0f1e9833f5bad85a1",
    "subnet-0dc03779a49bb11fc",
    "subnet-0ab2cc904a1af47d2"
    ]

    # Pick one subnet randomly
    selected_subnet = random.choice(SUBNETS) if lab_type == "room" else random.choice(LAB_SUBNETS)
    # selected_subnet = random.choice(SUBNETS)
    
    try:
        response = ec2.get_security_groups_for_vpc(
        VpcId=VPC_ID)

        for i in response['SecurityGroupForVpcs']:
            if i['GroupName'] == f"{student_id} security group":
                raise
    except Exception as e:
        print(e)
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
    
    UserData=lab,
    KeyName="mumb",   

    NetworkInterfaces=[
        {
            "DeviceIndex": 0,
            "SubnetId": selected_subnet,
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
    return response["Reservations"][0]["Instances"][0]["PrivateIpAddress"]

        

# def exec_lab_file(lab,instance_id):
#     commands = [
#     "cd /root",
#     "touch setup.sh",
#     f"""cat > setup.sh <<'EOF'
#     {lab}
#     EOF
#     """,
#     "sudo chmod +x setup.sh",
#     "sudo ./setup.sh",
#     "rm -rf ./setup.sh"
#     ]

#     response = ssm.send_command(
#         InstanceIds=[instance_id],
#         DocumentName="AWS-RunShellScript",
#         Parameters={
#             "commands": commands
#         }
#     )

#     command_id = response["Command"]["CommandId"]
#     print("Command ID:", command_id)

#     # Wait and fetch output
#     time.sleep(3)

#     output = ssm.get_command_invocation(
#         CommandId=command_id,
#         InstanceId=instance_id
#     )

#     print("STDOUT:\n", output["StandardOutputContent"])
#     print("STDERR:\n", output["StandardErrorContent"])