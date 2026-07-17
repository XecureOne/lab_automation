import boto3
import ipaddress
from botocore.exceptions import ClientError

def add_security_group_rules(security_group_id: str, ip: str, region: str = "ap-south-1") -> None:
    """
    Adds SSH inbound rule to an EC2 security group for a specific IP.
    """

    # Validate IP
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        raise ValueError("Invalid IP address provided")

    ec2 = boto3.client("ec2", region_name=region)

    inbound_rules = [
        {
            "IpProtocol": "-1",
            "IpRanges": [
                {
                    "CidrIp": f"{ip}/32",
                    "Description": "Allow access from specific IP",
                }
            ],
        }
    ]

    try:
        ec2.authorize_security_group_ingress(
            GroupId=security_group_id,
            IpPermissions=inbound_rules,
        )
        print(f"[✓] Access granted to {ip}/32")

    except ClientError as e:
        if e.response["Error"]["Code"] == "InvalidPermission.Duplicate":
            print("[!] Rule already exists.")
        else:
            print(f"[✗] Error: {e}")
            raise

def add_sg_rules(security_group_id: str, ip: str, region: str = "ap-south-1") -> None:

    # Validate IP
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        raise ValueError("Invalid IP address provided")

    ec2 = boto3.client("ec2", region_name=region)

    inbound_rules = [
        {
        "IpProtocol": "tcp",
        "FromPort": 8443,
        "ToPort": 8443,
        "IpRanges": [
            {
                "CidrIp": f"{ip}/32",
                "Description": "Allow GUI"
            }
        ]
        },
    ]

    try:
        ec2.authorize_security_group_ingress(
            GroupId=security_group_id,
            IpPermissions=inbound_rules,
        )
        print(f"[✓] Access granted to {ip}/32")

    except ClientError as e:
        if e.response["Error"]["Code"] == "InvalidPermission.Duplicate":
            print("[!] Rule already exists.")
        else:
            print(f"[✗] Error: {e}")
            raise

def add_sgid_to_sg(security_group_id: str, source_sg_id: str, region: str = "ap-south-1") -> None:

    ec2 = boto3.client("ec2", region_name=region)

    inbound_rules = [
        {
            "IpProtocol": "-1",
            "UserIdGroupPairs": [
                {
                    "GroupId": source_sg_id,
                    "Description": "Allow all traffic from source security group"
                }
            ]
        }
    ]

    try:
        ec2.authorize_security_group_ingress(
            GroupId=security_group_id,
            IpPermissions=inbound_rules,
        )

    except ClientError as e:
        if e.response["Error"]["Code"] == "InvalidPermission.Duplicate":
            print("[!] Rule already exists.")
        else:
            print(f"[✗] Error: {e}")
            raise



def remove_ingress_rule(sg_id, ip):
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        raise ValueError("Invalid IP address provided")
    try:
        ec2 = boto3.client("ec2", region_name="ap-south-1")
        ec2.revoke_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[
                {
                    "IpProtocol": "-1",
                    "IpRanges": [
                        {
                            "CidrIp": f"{ip}/32",
                            "Description": "Allow access from specific IP",
                        }
                    ],
                }
            ],
        )
        print(f"Removed ingress rule")
    except ClientError as e:
        print(f"Error removing rule: {e}")
        raise


