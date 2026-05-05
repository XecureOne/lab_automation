import json
import lib
import assign2sg
import remote
import find_ip
import cluster_add

def template_sel(choice):
    with open(f"/home/phoenball/leaky/{choice}.json", "r") as f:
        template = f.read()
    return template

# 959782869917.dkr.ecr.ap-south-1.amazonaws.com/rooms/leaky
# 959782869917.dkr.ecr.ap-south-1.amazonaws.com/box/kali-box


PARAMETERS1 = [
    {"ParameterKey": "ECSClusterName", "ParameterValue": ""},
    {"ParameterKey": "ECSAutoScalingGroupName", "ParameterValue": ""}
]

PARAMETERS2 = [
    {"ParameterKey": "ClusterArn",  "ParameterValue": ""},
    {"ParameterKey": "AppTaskDefinitionArn", "ParameterValue": ""},
    {"ParameterKey": "App2TaskDefinitionArn",  "ParameterValue": ""}
]

def ipadd_of(student_id):
    with open("/home/phoenball/leaky/static_ips.json","r") as f:
        return json.load(f).get(student_id)

def set_param(student_id,choice):
    if choice == 'C':
        PARAMETERS1[0]["ParameterValue"] = f"clu_{student_id}"
        PARAMETERS1[1]["ParameterValue"] = f"asg_{student_id}"
    else:
        room = input("Enter the room:")
        with open("/home/phoenball/leaky/clusters.json","r") as f:
            i = json.load(f).get(student_id)
            with open("/home/phoenball/leaky/task_def.json","r") as ff:
                j = json.load(ff)
                PARAMETERS2[0]["ParameterValue"] = f"{i["clu"]}"
                PARAMETERS2[1]["ParameterValue"] = f"{j.get(room)}"
                PARAMETERS2[2]["ParameterValue"] = f"{j.get('kali_box')}"
            lib.scale_asg_from_arn(i["asg"],2)

if __name__ == "__main__":
    choice = input("[*] Deployment : Cluster(C) or Service(S)?? ")
    client = str(input("Enter student id:"))
    STACK_NAME  = f"{client}" if choice == "C" else f"{client}-dep"
    template = template_sel("cluster" if choice == "C" else "service")
    set_param(client,choice)
    PARAMETERS = PARAMETERS1 if choice == "C" else PARAMETERS2
    out = lib.create_stack(STACK_NAME,PARAMETERS,template)
    if (choice == "C" and out):
        clu = next((o['OutputValue'] for o in out if o['OutputKey'] == "ECSClusterArn"), None)
        asg = next((o['OutputValue'] for o in out if o['OutputKey'] == "ECSAutoScalingGroupArn"), None)
        cluster_add.add_cluster(client,clu,asg)
    if (choice == "S" and out):
        ip = []
        client_ip = ipadd_of(client)
        for i in out:
            if i['OutputKey'].startswith('Container'):
                assign2sg.add_security_group_rules(i['OutputValue'],client_ip)
            else:
                ip.append(find_ip.get_task_private_ips(PARAMETERS[0]["ParameterValue"], i['OutputValue']))
        remote.send(ip[0],ip[1],client_ip)

            
# ECSClusterArn: arn:aws:ecs:ap-south-1:959782869917:cluster/student-011
#   App2TaskDefinitionArn: arn:aws:ecs:ap-south-1:959782869917:task-definition/app2:25
#   AppTaskDefinitionArn: arn:aws:ecs:ap-south-1:959782869917:task-definition/app:25
