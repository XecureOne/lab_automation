import boto3
from role_credentials import _child_creds
import importlib
import json
import sys
import os

lab = input("Enter the lab: ")
sys.path.append(os.path.abspath("./test_cases/"))
module = importlib.import_module(lab)

def fetch_account_id(student_id):
    with open("../sample.json","r") as f:
            res = json.loads(f.read())
            return res.get(student_id).get("account_id")

<<<<<<< HEAD
print(module.run_test_cases(_child_creds(fetch_account_id(student_id))))
=======
def start():
    student_id = input("Enter student id:")
    print(module.run_test_cases(_child_creds(fetch_account_id(student_id))))

def test(account_id):
    print(module.run_test_cases(_child_creds(account_id)))

if __name__ == '__main__':
    start()
>>>>>>> 6c06db2 (iam issue cleared out)
