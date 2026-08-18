import boto3
from role_credentials import _child_creds
import importlib
import json
import sys
import os
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
logger = logging.getLogger(__name__)

lab = input("Enter the lab: ")
sys.path.append(os.path.abspath("./test_cases/"))
logger.info("Loading validation module for lab=%s", lab)
module = importlib.import_module(lab)

def fetch_account_id(student_id):
    logger.info("Resolving account for student_id=%s", student_id)
    with open("../sample.json","r") as f:
            res = json.loads(f.read())
            account = res.get(student_id).get("account_id")
            logger.info("Resolved student_id=%s to account_id=%s", student_id, account)
            return account

def start():
    student_id = input("Enter student id:")
    logger.info("Starting validation for lab=%s student_id=%s", lab, student_id)
    result = module.run_test_cases(_child_creds(fetch_account_id(student_id)))
    logger.info("Validation finished for lab=%s student_id=%s result=%s", lab, student_id, result)
    if result is not None:
        print(f"[RESULT] {result}")

def test(account_id):
    logger.info("Starting validation for lab=%s account_id=%s", lab, account_id)
    result = module.run_test_cases(_child_creds(account_id))
    logger.info("Validation finished for lab=%s account_id=%s result=%s", lab, account_id, result)
    if result is not None:
        print(f"[RESULT] {result}")

if __name__ == '__main__':
    start()
