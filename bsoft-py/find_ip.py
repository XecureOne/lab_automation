import boto3

def get_task_private_ips(cluster_name, service_name, region="ap-south-1"):
    ecs = boto3.client("ecs", region_name=region)
    ec2 = boto3.client("ec2", region_name=region)

    private_ips = []

    # 1. List running tasks for the service
    tasks_response = ecs.list_tasks(
        cluster=cluster_name,
        serviceName=service_name,
        desiredStatus="RUNNING"
    )

    task_arns = tasks_response.get("taskArns", [])
    if not task_arns:
        print("No running tasks found.")
        return private_ips

    # 2. Describe tasks
    tasks_desc = ecs.describe_tasks(
        cluster=cluster_name,
        tasks=task_arns
    )

    for task in tasks_desc["tasks"]:
        # 3. Extract ENI ID from attachments
        for attachment in task.get("attachments", []):
            if attachment["type"] == "ElasticNetworkInterface":
                eni_id = None

                for detail in attachment["details"]:
                    if detail["name"] == "networkInterfaceId":
                        eni_id = detail["value"]

                if eni_id:
                    # 4. Get private IP from ENI
                    eni_desc = ec2.describe_network_interfaces(
                        NetworkInterfaceIds=[eni_id]
                    )

                    for eni in eni_desc["NetworkInterfaces"]:
                        private_ips.append(eni["PrivateIpAddress"])

    return private_ips[0]