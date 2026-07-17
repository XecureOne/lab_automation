import json
import lib
import assign2sg
import remote
import find_ip
import cluster_add
import secrets
import string

def generate_id():
    length = 3
    first_char = secrets.choice(string.ascii_letters)
    rest_chars = string.ascii_letters + string.digits

    return first_char + ''.join(secrets.choice(rest_chars) for _ in range(length - 1))


def template_sel(choice):
    with open(f"/home/carpediem/bsoft/leaky/{choice}.json", "r") as f:
        template = f.read()
    return template

# 959782869917.dkr.ecr.ap-south-1.amazonaws.com/rooms/leaky
# 959782869917.dkr.ecr.ap-south-1.amazonaws.com/box/kali-box


PARAMETERS1 = [
    {"ParameterKey": "ECSClusterName", "ParameterValue": ""},
    {"ParameterKey": "ECSAutoScalingGroupName", "ParameterValue": ""},
    {"ParameterKey": "StudentId", "ParameterValue": ""}
]

PARAMETERS2 = [
    {"ParameterKey": "ClusterArn",  "ParameterValue": ""},
    {"ParameterKey": "AppTaskDefinitionArn", "ParameterValue": ""}
]

def ipadd_of(student_id):
    with open("/home/carpediem/bsoft/leaky/static_ips.json","r") as f:
        return json.load(f).get(student_id)

def cluster(client):
    STACK_NAME = f"{generate_id()+client+generate_id()}"
    template = template_sel("cluster")
    PARAMETERS1[0]["ParameterValue"] = f"clu_{client}"
    PARAMETERS1[1]["ParameterValue"] = f"asg_{client}"
    PARAMETERS1[2]["ParameterValue"] = client
    out = lib.create_stack(STACK_NAME,PARAMETERS1,template)
    if out: 
        clu = next((o['OutputValue'] for o in out if o['OutputKey'] == "ECSClusterArn"), None)
        asg = next((o['OutputValue'] for o in out if o['OutputKey'] == "ECSAutoScalingGroupArn"), None)
        cluster_add.add_cluster(client,clu,asg,STACK_NAME)

def service(client):
    STACK_NAME = f"{generate_id()+client+generate_id()}"
    template = template_sel("service")
    room = input("Enter the room:")
    with open("/home/carpediem/bsoft/leaky/clusters.json","r") as f:
        i = json.load(f).get(client)
        with open("/home/carpediem/bsoft/leaky/task_def.json","r") as ff:
            j = json.load(ff)
            PARAMETERS2[0]["ParameterValue"] = f"{i["clu"]}"
            PARAMETERS2[1]["ParameterValue"] = f"{j.get(room)}"
        lib.scale_asg_from_arn(i["asg"],1)
    out = lib.create_stack(STACK_NAME,PARAMETERS2,template)
    cluster_add.add_service(client,STACK_NAME)
    if out:
        ip = ''
        client_ip = ipadd_of(client)
        for i in out:
            if i['OutputKey'].startswith('Container'):
                assign2sg.add_security_group_rules(i['OutputValue'],client_ip)
            else:
                ip = find_ip.get_task_private_ips(PARAMETERS2[0]["ParameterValue"], i['OutputValue'])
        remote.send_uni_add(ip,client_ip)


if __name__ == "__main__":
    choice = input("[*] Deployment : Cluster(C) or Service(S)?? ")
    client = str(input("Enter student id:"))
    cluster(client) if choice == "C" else service(client)

            
# ECSClusterArn: arn:aws:ecs:ap-south-1:959782869917:cluster/s
            
# ECSClusterArn: arn:aws:ecs:ap-south-1:959782869917:cluster/student-011
#   App2TaskDefinitionArn: arn:aws:ecs:ap-south-1:959782869917:task-definition/app2:25
#   AppTaskDefinitionArn: arn:aws:ecs:ap-south-1:959782869917:task-definition/app:25
