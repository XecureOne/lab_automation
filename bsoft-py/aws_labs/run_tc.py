import boto3
from role_credentials import _child_iam
import importlib
import json

lab = input("Enter the lab: ")
student_id = input("Enter student id:")
module = importlib.import_module(lab)

def fetch_account_id(student_id):
    with open("../sample.json","r") as f:
            res = json.loads(f.read())
            return res.get(student_id).get("account_id")

print(module.run_test_cases(_child_iam(fetch_account_id(student_id))))