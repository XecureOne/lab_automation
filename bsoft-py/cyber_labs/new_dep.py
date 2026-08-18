import boto3
import json
import library
import sys
import os
import time

sys.path.append(os.path.abspath(".."))

import assign2sg
import remote

ec2 = boto3.resource('ec2')

def fetch_lab_image(lab_type):
    with open("../../leaky/lab_images.json","r") as f:
        res = json.load(f)
        if res:
            return res.get(lab_type)

def fetch_lab_file(lab_id):
    with open(f"./labs/{lab_id}.sh","r") as f:
        if f:
            return f.read()

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

def get_lab_details(lab_id):
    with open("./labs.json","r") as f:
        res = json.load(f).get(lab_id)
        if res:
            return res

def start_lab():
    lab_id = input("Enter the lab id: ")
    lab_details = get_lab_details(lab_id)
    lab_class = lab_details["class"]
    print(f"[INFO] student_id={student_id} lab_id={lab_id} Starting cyber lab ({lab_class})")
    if (lab_class == "bash" or lab_class == "ami"):
        lab_type = lab_details["type"]
        image_file = fetch_lab_file(lab_id) if lab_class=="bash" else ""
        image_id = fetch_lab_image(lab_type) if lab_class=="bash" else lab_details["id"]
        tier = lab_details["tier"]
        dat = library.start_instance(image_file,"lab",student_id,image_id,lab_type,tier)
        ip = fetch_static_ip(student_id)
        if dat:
            store(lab_id,dat["instance_id"])
            # time.sleep(120)
            # library.exec_lab_file(image_file,dat["instance_id"])
            assign2sg.add_security_group_rules(dat["sg_id"],ip, student_id=student_id)
            remote.send_uni_add(dat.get("ip"), ip, student_id=student_id)
            print(f"[DONE] student_id={student_id} lab_id={lab_id} Cyber lab started")

def stop_lab():
    inst_id = fetch_instance_id(student_id)
    ip = fetch_static_ip(student_id)
    if inst_id:
        if library.stop_instance(inst_id, student_id=student_id):
            print(f"[OK] student_id={student_id} Lab instance stopped")
            with open("./store.json","r") as f:
                res = json.load(f)
                res.pop(student_id)
                with open("./store.json", "w") as ff:
                    ff.write(json.dumps(res))
                remote.send_del(ip, student_id=student_id)
                print(f"[DONE] student_id={student_id} Cyber lab stopped")
    else:
        print(f"[WARN] student_id={student_id} No active session found")

if __name__ == '__main__':
    student_id = input("Enter student id: ")
    choice = input("Start(S) or Stop(T): ")
    start_lab() if choice == 'S' else stop_lab()
