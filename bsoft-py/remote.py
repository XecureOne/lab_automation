import boto3
import time

INSTANCE_ID = "i-07b916c5f053ede82"   # replace

ssm = boto3.client("ssm")

def send_add(ip,ip2,client):
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
    print("Command ID:", command_id)

    # Wait and fetch output
    time.sleep(3)

    output = ssm.get_command_invocation(
        CommandId=command_id,
        InstanceId=INSTANCE_ID
    )

    print("STDOUT:\n", output["StandardOutputContent"])
    print("STDERR:\n", output["StandardErrorContent"])

def send_del(client):
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
    print("Command ID:", command_id)

    # Wait and fetch output
    time.sleep(3)

    output = ssm.get_command_invocation(
        CommandId=command_id,
        InstanceId=INSTANCE_ID
    )

    print("STDOUT:\n", output["StandardOutputContent"])
    print("STDERR:\n", output["StandardErrorContent"])