import json


def add_cluster(student_id,clu,asg):
    with open("/home/phoenball/leaky/clusters.json", "r") as f:
        cl = json.load(f)
        if student_id not in cl.keys():
            cl[student_id] = dict({ "clu": clu, "asg": asg})
            with open("/home/phoenball/leaky/clusters.json","w") as ff:
                ff.write(json.dumps(cl))
                print("Added cluster arn!!")
        else:
            print('[*] Not appended')

def add_static_ip(student_id):
    cl=''
    with open("/home/phoenball/leaky/static_ips.json", "r") as f:
        cl = json.load(f)
        l = int(next(reversed(cl.values())).split(".")[-1])
        if student_id not in cl.keys():
            cl[student_id] = f"10.0.128.{l+1}"
            with open("./static_ips.json","w") as ff:
                ff.write(json.dumps(cl))
        else:
            print('[*] Not appended')
