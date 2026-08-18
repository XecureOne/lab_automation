import boto3
import time

INSTANCE_ID = "i-0e7718168aa89ab2b"   # replace

ssm = boto3.client("ssm")


def _ctx(student_id=None):
    return f" student_id={student_id}" if student_id else ""


def _print_command_result(command_id, output, student_id=None):
    print(f"[OK]{_ctx(student_id)} SSM command sent: {command_id}")
    status = output.get("Status")
    print(f"[INFO]{_ctx(student_id)} SSM command status: {status}")
    stderr = output.get("StandardErrorContent", "").strip()
    if stderr:
        print(f"[WARN]{_ctx(student_id)} SSM command stderr: {stderr}")


def send_uni_add(ip,client, student_id=None):
    commands = []
    commands.append(f"sudo vpnctl add {client} {ip}")
    commands.append("sudo vpnctl list")
    commands.append("sudo systemctl restart wg-quick@wg0")

    response = ssm.send_command(
        InstanceIds=[INSTANCE_ID],
        DocumentName="AWS-RunShellScript",
        Parameters={
            "commands": commands
        }
    )

    command_id = response["Command"]["CommandId"]

    # Wait and fetch output
    time.sleep(3)

    output = ssm.get_command_invocation(
        CommandId=command_id,
        InstanceId=INSTANCE_ID
    )

    _print_command_result(command_id, output, student_id=student_id)

def send_add(ip,ip2,client, student_id=None):
    # Command to run
    commands = []
    commands.append(f"sudo vpnctl add {client} {ip}")
    commands.append(f"sudo vpnctl add {client} {ip2}")
    commands.append("sudo vpnctl list")
    commands.append("sudo systemctl restart wg-quick@wg0")

    response = ssm.send_command(
        InstanceIds=[INSTANCE_ID],
        DocumentName="AWS-RunShellScript",
        Parameters={
            "commands": commands
        }
    )

    command_id = response["Command"]["CommandId"]

    # Wait and fetch output
    time.sleep(3)

    output = ssm.get_command_invocation(
        CommandId=command_id,
        InstanceId=INSTANCE_ID
    )

    _print_command_result(command_id, output, student_id=student_id)

def send_del(client, student_id=None):
    # Command to run
    commands = []
    commands.append(f"sudo vpnctl delete {client}")
    commands.append("sudo vpnctl list")
    commands.append("sudo systemctl restart wg-quick@wg0")

    response = ssm.send_command(
        InstanceIds=[INSTANCE_ID],
        DocumentName="AWS-RunShellScript",
        Parameters={
            "commands": commands
        }
    )

    command_id = response["Command"]["CommandId"]

    # Wait and fetch output
    time.sleep(3)

    output = ssm.get_command_invocation(
        CommandId=command_id,
        InstanceId=INSTANCE_ID
    )

    _print_command_result(command_id, output, student_id=student_id)

def send_uni_del(client,ip, student_id=None):
    # Command to run
    commands = []
    commands.append(f"sudo vpnctl delete {client} {ip}")
    commands.append("sudo vpnctl list")
    commands.append("sudo systemctl restart wg-quick@wg0")

    response = ssm.send_command(
        InstanceIds=[INSTANCE_ID],
        DocumentName="AWS-RunShellScript",
        Parameters={
            "commands": commands
        }
    )

    command_id = response["Command"]["CommandId"]

    # Wait and fetch output
    time.sleep(3)

    output = ssm.get_command_invocation(
        CommandId=command_id,
        InstanceId=INSTANCE_ID
    )

    _print_command_result(command_id, output, student_id=student_id)

def check(student_id=None):
    commands = []
    commands.append(f"echo hi")

    response = ssm.send_command(
        InstanceIds=[INSTANCE_ID],
        DocumentName="AWS-RunShellScript",
        Parameters={
            "commands": commands
        }
    )

    command_id = response["Command"]["CommandId"]

    # Wait and fetch output
    time.sleep(3)

    output = ssm.get_command_invocation(
        CommandId=command_id,
        InstanceId=INSTANCE_ID
    )

    _print_command_result(command_id, output, student_id=student_id)
