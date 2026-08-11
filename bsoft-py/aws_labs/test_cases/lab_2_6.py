import boto3

ec2 = ""
s3 = ""

ENDPOINT_NAME = "php-s3-endpoint"
BUCKET_PREFIX = "php-app-assets-"
VPC_NAME = "php-app-vpc"

def result(label, passed):
    status = "[PASS]" if passed else "[FAIL]"
    print(f"{status} {label}")
    return passed

def get_vpc():
    try:
        r = ec2.describe_vpcs(
            Filters=[{'Name': 'tag:Name', 'Values': [VPC_NAME]},
                     {'Name': 'state', 'Values': ['available']}]
        )
        vpcs = r.get('Vpcs', [])
        return vpcs[0]['VpcId'] if vpcs else None
    except:
        return None

def test_endpoint_exists(vpc_id):
    try:
        filters = [{'Name': 'vpc-id', 'Values': [vpc_id]},
                   {'Name': 'service-name', 'Values': [f'com.amazonaws.*s3']},
                   {'Name': 'vpc-endpoint-state', 'Values': ['available']}] \
            if vpc_id else \
            [{'Name': 'tag:Name', 'Values': [ENDPOINT_NAME]},
             {'Name': 'vpc-endpoint-state', 'Values': ['available']}]
        r = ec2.describe_vpc_endpoints(Filters=[
            {'Name': 'tag:Name', 'Values': [ENDPOINT_NAME]},
            {'Name': 'vpc-endpoint-state', 'Values': ['available']}
        ])
        endpoints = r.get('VpcEndpoints', [])
        exists = len(endpoints) > 0
        result(f"VPC Endpoint '{ENDPOINT_NAME}' exists and is available", exists)
        return endpoints[0] if exists else None
    except Exception as e:
        result(f"Endpoint existence check - ERROR: {e}", False)
        return None

def test_endpoint_is_gateway(endpoint):
    if not endpoint:
        return result("Endpoint is Gateway type (not Interface)", False)
    ep_type = endpoint.get('VpcEndpointType', '')
    return result(f"Endpoint type is Gateway", ep_type == 'Gateway')

def test_endpoint_for_s3(endpoint):
    if not endpoint:
        return result("Endpoint service is S3", False)
    svc = endpoint.get('ServiceName', '')
    return result(f"Endpoint service is S3 ({svc})", 's3' in svc.lower())

def test_route_table_has_prefix_list(endpoint):
    if not endpoint:
        return result("Private route table has S3 prefix list route", False)
    try:
        rt_ids = endpoint.get('RouteTableIds', [])
        has_routes = len(rt_ids) > 0
        result(f"Endpoint is associated with {len(rt_ids)} route table(s)", has_routes)
        for rt_id in rt_ids:
            r = ec2.describe_route_tables(RouteTableIds=[rt_id])
            for rt in r.get('RouteTables', []):
                tags = {t['Key']: t['Value'] for t in rt.get('Tags', [])}
                name = tags.get('Name', rt_id)
                for route in rt.get('Routes', []):
                    if route.get('GatewayId', '').startswith('vpce-') or \
                       (route.get('DestinationPrefixListId', '') and
                        'pl-' in route.get('DestinationPrefixListId', '')):
                        result(f"Route table '{name}' has S3 prefix list route via endpoint",
                               True)
                        return True
        return result("Route table has S3 prefix list route", False)
    except Exception as e:
        return result(f"Prefix list route check - ERROR: {e}", False)

def test_s3_bucket_exists():
    try:
        r = s3.list_buckets()
        buckets = [b['Name'] for b in r.get('Buckets', [])
                   if b['Name'].startswith(BUCKET_PREFIX)]
        exists = len(buckets) > 0
        result(f"S3 test bucket starting with '{BUCKET_PREFIX}' exists", exists)
        return buckets[0] if exists else None
    except Exception as e:
        result(f"S3 bucket check - ERROR: {e}", False)
        return None

def test_s3_bucket_public_access(bucket_name):
    if not bucket_name:
        return result("S3 bucket has public access blocked", False)
    try:
        r = s3.get_public_access_block(Bucket=bucket_name)
        config = r.get('PublicAccessBlockConfiguration', {})
        all_blocked = all([
            config.get('BlockPublicAcls', False),
            config.get('IgnorePublicAcls', False),
            config.get('BlockPublicPolicy', False),
            config.get('RestrictPublicBuckets', False)
        ])
        return result("S3 bucket blocks all public access", all_blocked)
    except Exception as e:
        return result(f"Public access block check - ERROR: {e}", False)

def test_test_file_exists(bucket_name):
    if not bucket_name:
        return result("Test file 'test-asset.txt' exists in bucket", False)
    try:
        s3.head_object(Bucket=bucket_name, Key='test-asset.txt')
        return result("Test file 'test-asset.txt' exists in S3 bucket", True)
    except s3.exceptions.ClientError as e:
        if e.response['Error']['Code'] == '404':
            return result("Test file 'test-asset.txt' exists in S3 bucket", False)
        return result(f"Test file check - ERROR: {e}", False)
    except Exception as e:
        return result(f"Test file check - ERROR: {e}", False)

def run_test_cases(credentials):
    global ec2,s3
    ec2 = boto3.client('ec2',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    s3 = boto3.client('s3',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )
    print("=" * 65)
    print("LAB 2_6 VALIDATION: VPC Endpoint for S3")
    print("=" * 65)
    vpc_id = get_vpc()
    result("VPC php-app-vpc found", vpc_id is not None)
    endpoint = test_endpoint_exists(vpc_id)
    test_endpoint_is_gateway(endpoint)
    test_endpoint_for_s3(endpoint)
    test_route_table_has_prefix_list(endpoint)
    bucket_name = test_s3_bucket_exists()
    test_s3_bucket_public_access(bucket_name)
    test_test_file_exists(bucket_name)
    print("=" * 65)