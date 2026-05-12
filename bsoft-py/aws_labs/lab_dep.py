import boto3
import json
import permission_trigger
import nuke
from role_credentials import _assume_child_role
import sys

client = boto3.client("organizations")

def fetch_account_id(student_id):
    with open("../sample.json","r") as f:
        res = json.loads(f.read())
        return res.get(student_id).get("account_id")

def fetch_account_alias(account_id):
    with open("./alias_mapping.json","r") as f:
        res = json.loads(f.read())
        return res.get(account_id)

def list_active_accounts():
    res = client.list_accounts_for_parent(
        ParentId="ou-joyg-0snv4uu2",
    )
    if res:
        res = res["Accounts"]
        res = [i["Id"] for i in res if i["Status"]=="ACTIVE"]
        print("Active accounts listed!!")
        # print(res)cl
        return res

def find_first_active_account(active_accounts):
    with open("../sample.json","r") as f:
        res = json.loads(f.read())
    print(res.values())
    for i in active_accounts:
        if i not in [ j.get("account_id") for j in res.values()]:
            print(f"Found the first active account!! {i}")
            return i

def append2student(student,lab,id):
    with open("../sample.json","r") as f:
        res = json.loads(f.read())
    if res.get(student):
        return False
    res[student]=dict(account_id = id, lab_id = lab)
    print(f"Appended {student} to {id}")
    with open("../sample.json","w") as ff:
        ff.write(json.dumps(res))
    return True

def detach_student():
    with open("../sample.json","r") as f:
        res = json.loads(f.read())
        res.pop(student_id)
    with open("../sample.json","w") as ff:
        ff.write(json.dumps(res))
    
def start_lab():
    if append2student(student_id,lab_id,find_first_active_account(list_active_accounts())):
        account_id = fetch_account_id(student_id)
        permission_trigger.trigger_access_s3(
            account_id=account_id,
            permissions_key=f"{lab_id}.json",
        )
    else:
        print("Student already has an active lab session!!")

def del_lab():
    account_id = fetch_account_id(student_id)
    permission_trigger.delete_access(
        account_id=account_id
    )
    alias = fetch_account_alias(account_id)
    creds = _assume_child_role(account_id,_assume_child_role("959782869917",{},"rt_provider_core_backend"))
    nuke.nuke(account_id,alias,creds)
    detach_student()

if __name__ == '__main__':
    lab_id = input("Enter lab id: ")
    student_id = input("Enter student id: ")
    choice = input("Start(S) or Stop(T):")
    start_lab() if choice=="S" else del_lab()




