import boto3
import json
import permission_trigger
import nuke
from role_credentials import _assume_child_role
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
logger = logging.getLogger(__name__)

client = boto3.client("organizations")

def fetch_account_id(student_id):
    logger.info("Resolving account for student_id=%s", student_id)
    with open("../sample.json","r") as f:
        res = json.loads(f.read())
        account = res.get(student_id).get("account_id")
        logger.info("Resolved student_id=%s to account_id=%s", student_id, account)
        return account

def fetch_account_alias(account_id):
    logger.info("Resolving account alias for account_id=%s", account_id)
    with open("./alias_mapping.json","r") as f:
        res = json.loads(f.read())
        alias = res.get(account_id)
        logger.info("Resolved account_id=%s to alias=%s", account_id, alias)
        return alias

def list_active_accounts():
    logger.info("Listing active AWS Organizations accounts")
    res = client.list_accounts_for_parent(
        ParentId="ou-anaf-fe7lhyxx",
    )
    if res:
        res = res["Accounts"]
        res = [i["Id"] for i in res if i["Status"]=="ACTIVE"]
        print(f"[INFO] Active accounts listed: {len(res)}")
        logger.info("Listed %s active AWS Organizations accounts", len(res))
        # print(res)cl
        return res

def find_first_active_account(active_accounts):
    logger.info("Selecting first unallocated active account")
    with open("./blocklist.json",'r') as f:
        blocklist = json.load(f)["quarantined"]
    with open("../sample.json","r") as f:
        res = json.loads(f.read())
    for i in active_accounts:
        if (i not in [ j.get("account_id") for j in res.values()]) and (i not in blocklist):
            print(f"[OK] Selected account: {i}")
            logger.info("Selected account_id=%s for allocation", i)
            return i
    logger.warning("No unallocated active account found; active=%s quarantined=%s allocated=%s", len(active_accounts), len(blocklist), len(res))

def append2student(student,lab,id):
    logger.info("Appending lab allocation student_id=%s lab_id=%s account_id=%s", student, lab, id)
    with open("../sample.json","r") as f:
        res = json.loads(f.read())
    if res.get(student):
        logger.warning("Student already has allocation student_id=%s", student)
        return False
    res[student]=dict(account_id = id, lab_id = lab)
    print(f"[OK] Allocated account {id} to student {student}")
    with open("../sample.json","w") as ff:
        ff.write(json.dumps(res))
    logger.info("Allocation persisted student_id=%s lab_id=%s account_id=%s", student, lab, id)
    return True

def detach_student():
    logger.info("Detaching student allocation student_id=%s", student_id)
    with open("../sample.json","r") as f:
        res = json.loads(f.read())
        res.pop(student_id)
        with open("../sample.json","w") as ff:
            ff.write(json.dumps(res))
    logger.info("Detached student allocation student_id=%s", student_id)
    
def start_lab():
    logger.info("Starting lab allocation lab_id=%s student_id=%s", lab_id, student_id)
    if append2student(student_id,lab_id,find_first_active_account(list_active_accounts())):
        account_id = fetch_account_id(student_id)
        logger.info("Triggering access for lab_id=%s student_id=%s account_id=%s", lab_id, student_id, account_id)
        permission_trigger.trigger_access_s3(
            account_id=account_id,
            permissions_key=f"{lab_id}.json",
            student_id=student_id,
        )
        logger.info("Lab started lab_id=%s student_id=%s account_id=%s", lab_id, student_id, account_id)
    else:
        logger.warning("Lab start skipped because student already has active session student_id=%s", student_id)
        print(f"[WARN] Student already has an active lab session: {student_id}")

def del_lab():
    logger.info("Stopping lab for student_id=%s", student_id)
    account_id = fetch_account_id(student_id)
    logger.info("Deleting temporary access for student_id=%s account_id=%s", student_id, account_id)
    permission_trigger.delete_access(
        account_id=account_id,
        student_id=student_id,
    )
    alias = fetch_account_alias(account_id)
    logger.info("Fetched alias=%s before cleanup for account_id=%s", alias, account_id)
    creds = _assume_child_role(account_id,_assume_child_role("880690594512",{},"rt_provider_core_backend"))
    logger.info("Acquired child account credentials for cleanup account_id=%s", account_id)
    nuke.nuke(account_id, student_id=student_id)
    detach_student()
    logger.info("Lab stopped for student_id=%s account_id=%s", student_id, account_id)

if __name__ == '__main__':
    lab_id = input("Enter lab id: ")
    student_id = input("Enter student id: ")
    choice = input("Start(S) or Stop(T):")
    start_lab() if choice=="S" else del_lab()

