import json
import lib
import assign2sg
import remote
import find_ip
import cluster_add


def ipadd_of(student_id):
    with open("/home/phoenball/bsoft/leaky/static_ips.json","r") as f:
        return json.load(f).get(student_id)


def cluster(client):
    STACK_NAME = ""
    with open("/home/phoenball/bsoft/leaky/clusters.json","r") as f:
        i = json.load(f)
        STACK_NAME = i.get(client)["clu_stack"]
        if STACK_NAME:
            if lib.delete_stack(STACK_NAME):
                i.pop(client)
            with open("/home/phoenball/bsoft/leaky/clusters.json","w") as ff:
                ff.write(json.dumps(i))
            print("Deleted Cluster")
                    

def service(client):
    STACK_NAME = ""
    with open("/home/phoenball/bsoft/leaky/clusters.json","r") as f:
        i = json.load(f).get(client)
        if i and i["serv_stack"]: 
            STACK_NAME = i["serv_stack"]
            lib.scale_asg_from_arn(i["asg"],0)
        else:
            print("Cluster not found for the student id")
        if lib.delete_stack(STACK_NAME):
            i.pop("serv_stack")
            with open("/home/phoenball/bsoft/leaky/clusters.json","w") as ff:
                ff.write(json.dumps(i))
            print("[!!] Exiting..")
            client_ip = ipadd_of(client)
            remote.send_del(client_ip)
    

if __name__ == "__main__":
    choice = input("[*] Deletion : Cluster(C) or Service(S)?? ")
    client = str(input("Enter student id:"))
    cluster(client) if choice == "C" else service(client)