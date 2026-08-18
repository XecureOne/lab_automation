import json
import lib
import assign2sg
import remote
import find_ip
import cluster_add


def ipadd_of(student_id):
    with open("/home/carpediem/bsoft/leaky/static_ips.json","r") as f:
        return json.load(f).get(student_id)


def cluster(client):
    print(f"[INFO] student_id={client} Starting cluster deletion")
    STACK_NAME = ""
    with open("/home/carpediem/bsoft/leaky/clusters.json","r") as f:
        i = json.load(f)
        STACK_NAME = i.get(client)["clu_stack"]
        if STACK_NAME:
            if lib.delete_stack(STACK_NAME, student_id=client):
                i.pop(client)
            with open("/home/carpediem/bsoft/leaky/clusters.json","w") as ff:
                ff.write(json.dumps(i))
            print(f"[DONE] student_id={client} Cluster deleted")
                    

def service(client):
    print(f"[INFO] student_id={client} Starting service deletion")
    STACK_NAME = ""
    with open("/home/carpediem/bsoft/leaky/clusters.json","r") as f:
        i = json.load(f)
        if i.get(client) and i.get(client)["serv_stack"]: 
            STACK_NAME = i.get(client)["serv_stack"]
            lib.scale_asg_from_arn(i.get(client)["asg"],0, student_id=client)
        else:
            print(f"[WARN] student_id={client} No active cluster found")
        if lib.delete_stack(STACK_NAME, student_id=client):
            i.get(client).pop("serv_stack")
            with open("/home/carpediem/bsoft/leaky/clusters.json","w") as ff:
                ff.write(json.dumps(i))
            print(f"[DONE] student_id={client} Service deleted")
            client_ip = ipadd_of(client)
            remote.send_del(client_ip, student_id=client)
    

if __name__ == "__main__":
    choice = input("Deletion: Cluster(C) or Service(S)? ")
    client = str(input("Enter student id:"))
    cluster(client) if choice == "C" else service(client)
