import boto3
import json

client = boto3.client("organizations")

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

    # print(res.values())
    for i in active_accounts:
        if i not in res.values():
            print(f"Found the first active account!! {i}")
            return i


def append2student(student,lab,id):
    with open("../sample.json","r") as f:
        res = json.loads(f.read())
    res[student]=dict(account_id = id, lab_id = lab)
    print(f"Appended {student} to {id}")
    with open("../sample.json","w") as ff:
        ff.write(json.dumps(res))

append2student("student01","lab1",find_first_active_account(list_active_accounts()))




