import json


def add_cluster(student_id,clu,asg,clu_stack):
    with open("/home/carpediem/bsoft/leaky/clusters.json", "r") as f:
        cl = json.load(f)
        if student_id not in cl.keys():
            cl[student_id] = dict({ "clu": clu, "asg": asg, "clu_stack": clu_stack})
            with open("/home/carpediem/bsoft/leaky/clusters.json","w") as ff:
                ff.write(json.dumps(cl))
                print(f"[OK] student_id={student_id} Cluster recorded")
        else:
            print(f"[INFO] student_id={student_id} Cluster already recorded")


def add_service(student_id,serv_stack):
    with open("/home/carpediem/bsoft/leaky/clusters.json", "r") as f:
        cl = json.load(f)
        if student_id in cl.keys():
            cl[student_id]["serv_stack"] = serv_stack
            with open("/home/carpediem/bsoft/leaky/clusters.json","w") as ff:
                ff.write(json.dumps(cl))
                print(f"[OK] student_id={student_id} Service stack recorded")
        else:
            print(f"[WARN] student_id={student_id} Cannot record service stack; cluster not found")

def add_static_ip(student_id):
    cl=''
    with open("/home/carpediem/bsoft/leaky/static_ips.json", "r") as f:
        cl = json.load(f)
        l = int(next(reversed(cl.values())).split(".")[-1])
        if student_id not in cl.keys():
            cl[student_id] = f"10.0.128.{l+1}"
            with open("./static_ips.json","w") as ff:
                ff.write(json.dumps(cl))
        else:
            print(f"[INFO] student_id={student_id} Static IP already recorded")
