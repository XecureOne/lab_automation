import boto3
import json
import time
from botocore.exceptions import ClientError

# ─── Configuration ────────────────────────────────────────────────────────────

AWS_REGION  = "ap-south-1"


def _ctx(student_id=None):
    return f" student_id={student_id}" if student_id else ""


def scale_asg_from_arn(asg_name, desired_capacity, region="ap-south-1", student_id=None):
    autoscaling = boto3.client("autoscaling", region_name=region)


    # Update desired capacity
    response = autoscaling.update_auto_scaling_group(
        AutoScalingGroupName=asg_name,
        DesiredCapacity=desired_capacity
    )

    return response

# ─── Helper: poll stack status ────────────────────────────────────────────────

def wait_for_stack(cf_client, stack_name, target_status="CREATE_COMPLETE", student_id=None):
    terminal_states = {
        "CREATE_COMPLETE", "CREATE_FAILED", "ROLLBACK_COMPLETE",
        "ROLLBACK_FAILED", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE",
        "UPDATE_ROLLBACK_FAILED", "DELETE_COMPLETE", "DELETE_FAILED",
    }
    print(f"[INFO]{_ctx(student_id)} Waiting for stack {stack_name} to reach {target_status}")
    while True:
        try:
            response = cf_client.describe_stacks(StackName=stack_name)
            status   = response["Stacks"][0]["StackStatus"]
            reason   = response["Stacks"][0].get("StackStatusReason", "")
            print(f"[INFO]{_ctx(student_id)} Stack {stack_name} status: {status}")
            if status in terminal_states:
                return status, reason
        except ClientError as e:
            print(f"[ERROR]{_ctx(student_id)} Could not describe stack {stack_name}: {e}")
            return "NOT_FOUND", str(e)
        time.sleep(10)

# ─── Main ─────────────────────────────────────────────────────────────────────

def create_stack(stack_name,param,template, student_id=None):
    cf_client = boto3.client("cloudformation", region_name=AWS_REGION)

    # ── Check if stack already exists ─────────────────────────────────────────
    try:
        existing        = cf_client.describe_stacks(StackName=stack_name)
        existing_status = existing["Stacks"][0]["StackStatus"]
        print(f"[INFO]{_ctx(student_id)} Stack already exists: {stack_name} ({existing_status})")
        return
    except ClientError as e:
        if e.response["Error"]["Code"] != "ValidationError":
            raise
        pass

    # ── Create the stack ──────────────────────────────────────────────────────
    print(f"[INFO]{_ctx(student_id)} Creating stack {stack_name} in {AWS_REGION}")
    try:
        response = cf_client.create_stack(
            StackName=stack_name,
            TemplateBody=template,
            Parameters=param,
            Capabilities=["CAPABILITY_NAMED_IAM"],
            OnFailure="ROLLBACK",
            EnableTerminationProtection=False,
        )
        stack_id = response["StackId"]
        print(f"[OK]{_ctx(student_id)} Stack creation initiated: {stack_id}")
    except ClientError as e:
        print(f"[ERROR]{_ctx(student_id)} Failed to create stack {stack_name}: {e}")
        raise

    # ── Wait and report outcome ───────────────────────────────────────────────
    final_status, reason = wait_for_stack(cf_client, stack_name, student_id=student_id)

    if final_status == "CREATE_COMPLETE":
        stacks  = cf_client.describe_stacks(StackName=stack_name)["Stacks"]
        outputs = stacks[0].get("Outputs", [])
        print(f"[DONE]{_ctx(student_id)} Stack created: {stack_name}")
        if outputs:
            print(f"[INFO]{_ctx(student_id)} Stack outputs:")
            for out in outputs:
                print(f"  {out['OutputKey']}: {out['OutputValue']}")
            return outputs
    else:
        print(f"[WARN]{_ctx(student_id)} Stack creation ended with status {final_status}: {stack_name}")
        if reason:
            print(f"[WARN]{_ctx(student_id)} Reason: {reason}")


def delete_stack(stack_name, student_id=None):
    cf_client = boto3.client("cloudformation", region_name=AWS_REGION)
    try:
        existing = cf_client.describe_stacks(StackName=stack_name)
        existing_status = existing["Stacks"][0]["StackStatus"]
        print(f"[INFO]{_ctx(student_id)} Stack exists: {stack_name} ({existing_status})")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ValidationError":
            print(f"[INFO]{_ctx(student_id)} Stack does not exist: {stack_name}")
            return
        else:
            raise

    print(f"[INFO]{_ctx(student_id)} Deleting stack {stack_name}")
    try:
        cf_client.delete_stack(StackName=stack_name)
        print(f"[OK]{_ctx(student_id)} Stack deletion initiated: {stack_name}")
    except ClientError as e:
        print(f"[ERROR]{_ctx(student_id)} Failed to delete stack {stack_name}: {e}")
        return False

    print(f"[DONE]{_ctx(student_id)} Stack delete request accepted: {stack_name}")
    return True
