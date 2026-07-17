import boto3
import json
import time
from botocore.exceptions import ClientError

# ─── Configuration ────────────────────────────────────────────────────────────

AWS_REGION  = "ap-south-1"


def scale_asg_from_arn(asg_name, desired_capacity, region="ap-south-1"):
    autoscaling = boto3.client("autoscaling", region_name=region)


    # Update desired capacity
    response = autoscaling.update_auto_scaling_group(
        AutoScalingGroupName=asg_name,
        DesiredCapacity=desired_capacity
    )

    return response

# ─── Helper: poll stack status ────────────────────────────────────────────────

def wait_for_stack(cf_client, stack_name, target_status="CREATE_COMPLETE"):
    terminal_states = {
        "CREATE_COMPLETE", "CREATE_FAILED", "ROLLBACK_COMPLETE",
        "ROLLBACK_FAILED", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE",
        "UPDATE_ROLLBACK_FAILED", "DELETE_COMPLETE", "DELETE_FAILED",
    }
    print(f"\nWaiting for stack '{stack_name}' to reach {target_status}...")
    while True:
        try:
            response = cf_client.describe_stacks(StackName=stack_name)
            status   = response["Stacks"][0]["StackStatus"]
            reason   = response["Stacks"][0].get("StackStatusReason", "")
            print(f"  Current status: {status}")
            if status in terminal_states:
                return status, reason
        except ClientError as e:
            print(f"  describe_stacks error: {e}")
            return "NOT_FOUND", str(e)
        time.sleep(10)

# ─── Main ─────────────────────────────────────────────────────────────────────

def create_stack(stack_name,param,template):
    cf_client = boto3.client("cloudformation", region_name=AWS_REGION)

    # ── Check if stack already exists ─────────────────────────────────────────
    try:
        existing        = cf_client.describe_stacks(StackName=stack_name)
        existing_status = existing["Stacks"][0]["StackStatus"]
        print(f"Stack '{stack_name}' already exists with status: {existing_status}")
        return
    except ClientError as e:
        if e.response["Error"]["Code"] != "ValidationError":
            raise
        pass

    # ── Create the stack ──────────────────────────────────────────────────────
    print(f"Creating stack '{stack_name}' in {AWS_REGION}...")
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
        print(f"Stack creation initiated.\nStack ID: {stack_id}")
    except ClientError as e:
        print(f"Failed to create stack: {e}")
        raise

    # ── Wait and report outcome ───────────────────────────────────────────────
    final_status, reason = wait_for_stack(cf_client, stack_name)

    if final_status == "CREATE_COMPLETE":
        stacks  = cf_client.describe_stacks(StackName=stack_name)["Stacks"]
        outputs = stacks[0].get("Outputs", [])
        print(f"\nStack '{stack_name}' created successfully!")
        if outputs:
            print("\nStack outputs:")
            for out in outputs:
                print(f"  {out['OutputKey']}: {out['OutputValue']}")
            return outputs
    else:
        print(f"\nStack creation ended with status: {final_status}")
        if reason:
            print(f"Reason: {reason}")


def delete_stack(stack_name):
    cf_client = boto3.client("cloudformation", region_name=AWS_REGION)
    try:
        existing = cf_client.describe_stacks(StackName=stack_name)
        existing_status = existing["Stacks"][0]["StackStatus"]
        print(f"Stack '{stack_name}' exists with status: {existing_status}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ValidationError":
            print(f"Stack '{stack_name}' does not exist.")
            return
        else:
            raise

    print(f"Deleting stack '{stack_name}'...")
    try:
        cf_client.delete_stack(StackName=stack_name)
        print("Stack deletion initiated.")
    except ClientError as e:
        print(f"Failed to delete stack: {e}")
        return False

    print(f"\nStack '{stack_name}' deleted successfully!")
    return True

