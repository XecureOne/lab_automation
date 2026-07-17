import sys
import os
import remote
import json
import boto3
import assign2sg

module_dir = os.path.join(os.path.dirname(__file__),"cyber_labs")
sys.path.insert(0,module_dir)

import library

def fetch_instance_id(student_id):
    with open("/home/carpediem/bsoft/bsoft-py/box.json","r") as f:
        res = json.load(f).get(student_id)
        if res:
            return res.get("instance_id")

def store(student_id,instance_id):
    with open("/home/carpediem/bsoft/bsoft-py/box.json","r") as f:
        res = json.load(f)
        res[student_id] = { "instance_id": instance_id }
        with open("/home/carpediem/bsoft/bsoft-py/box.json","w") as ff:
            ff.write(json.dumps(res))

def delete(student_id):
    with open("/home/carpediem/bsoft/bsoft-py/box.json","r") as f:
        res = json.load(f)
        res.pop(student_id)
        with open("/home/carpediem/bsoft/bsoft-py/box.json","w") as ff:
            ff.write(json.dumps(res))


def fetch_static_ip(student_id):
    with open("/home/carpediem/bsoft/leaky/static_ips.json","r") as f:
        res = json.load(f).get(student_id)
        if res:
            return res

def retrieve_sg(stack_name):
    client = boto3.client("cloudformation",region_name='ap-south-1')
    stacks  = client.describe_stacks(StackName=stack_name)["Stacks"]
    outputs = stacks[0].get("Outputs", [])
    if outputs:
        print("\nStack outputs:")
        for out in outputs:
            print(f"  {out['OutputKey']}: {out['OutputValue']}")
    for i in outputs:
            if i['OutputKey'].startswith('Container'):
                return i['OutputValue']

def retreive_stack(client):
    with open("/home/carpediem/bsoft/leaky/clusters.json","r") as f:
        i = json.load(f)
        if i.get(client) and i.get(client)["serv_stack"]: 
            STACK_NAME = i.get(client)["serv_stack"]
            return STACK_NAME
    

def deploy(student):
    dat = library.start_instance("","room",student,"ami-083f389196406f0ca","GUI")
    if dat:
        client_ip = fetch_static_ip(student)
        print("Started AttackINstance!!!!!")

        serv_stack = retreive_stack(student)
        if serv_stack:
            sg_id = retrieve_sg(serv_stack)
            assign2sg.add_security_group_rules(sg_id,dat["ip"])
            assign2sg.add_sgid_to_sg(dat["sg_id"],sg_id)
            print("Alternate sg assigned!!")

            assign2sg.add_sg_rules(dat["sg_id"],client_ip)
            remote.send_uni_add(dat["ip"],client_ip)
            print("Access granted to user!!!")

            store(student,dat["instance_id"])
            print("[**] Deployed!!!!")
        else:
            print(f"Service Stack not found!!..No active room service found for {student}")

def destroy(student):
    ip = library.stop_instance(fetch_instance_id(student))
    if ip:
        client_ip = fetch_static_ip(student)
        print("Successfully destroyed AttackINstance")

        serv_stack = retreive_stack(student)
        if serv_stack:
            sg_id = retrieve_sg(retreive_stack(student))
            assign2sg.remove_ingress_rule(sg_id,ip)
            print("Room SG removed!!!")

            remote.send_uni_del(client_ip,ip)
            print("Access revoked!!")

            delete(student)
            print("[**] Destroyed!!!!")
        else:
            print(f"Service Stack not found!!..No active room service found for {student}")



if __name__ == '__main__':
    student = input("Enter the student id : ")
    choice = input("Enter your choice : Deploy(D) or Destroy(T)")
    deploy(student) if choice == 'D' else destroy(student)

