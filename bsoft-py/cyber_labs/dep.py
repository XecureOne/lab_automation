import boto3
import json
import library
import sys
import os

sys.path.append(os.path.abspath(".."))

import assign2sg
import remote


ec2 = boto3.resource('ec2')


def fetch_lab_image(lab_id):
    with open("../../leaky/lab_images.json","r") as f:
        res = json.load(f)
        if res:
            return res.get(lab_id)

def fetch_lab_id(student_id):
    with open("./store.json","r") as f:
        res = json.load(f).get(student_id)
        if res:
            return res.get("lab_id")

def fetch_instance_id(student_id):
    with open("./store.json","r") as f:
        res = json.load(f).get(student_id)
        if res:
            return res.get("instance_id")

def store(lab_id, instance_id):
    with open("./store.json","r") as f:
        res = json.load(f)
        res[student_id] = { "instance_id": instance_id, "lab_id": lab_id }
        with open("./store.json","w") as ff:
            ff.write(json.dumps(res))

def fetch_static_ip(student_id):
    with open("../../leaky/static_ips.json","r") as f:
        res = json.load(f).get(student_id)
        if res:
            return res

def start_lab():
    lab_id = input("Enter the lab id: ")
    image_id = fetch_lab_image(lab_id)
    dat = library.start_instance(student_id,image_id)
    ip = fetch_static_ip(student_id)
    if dat:
        store(lab_id,dat["instance_id"])
        assign2sg.add_security_group_rules(dat["sg_id"],ip)
        remote.send_uni_add(dat.get("ip"), ip)

def stop_lab():
    inst_id = fetch_instance_id(student_id)
    ip = fetch_static_ip(student_id)
    if inst_id:
        if library.stop_instance(inst_id):
            print("Stopped lab instance!!")
            with open("./store.json","r") as f:
                res = json.load(f)
                res.pop(student_id)
                with open("./store.json", "w") as ff:
                    ff.write(json.dumps(res))
                remote.send_del(ip)
    else:
        print("No active session found!!")

if __name__ == '__main__':
    student_id = input("Enter student id: ")
    choice = input("Start(S) or Stop(T): ")
    start_lab() if choice == 'S' else stop_lab()
    