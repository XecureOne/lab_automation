import boto3
import json

cfn = ""
ec2 = ""

VPC_STACK_NAME = "php-app-vpc-stack"
EC2_STACK_NAME = "php-app-ec2-stack"

EXPECTED_VPC_OUTPUTS = [
    "VpcId", "PublicSubnetAId",
    "PublicSubnetBId", "PrivateSubnetAId"
]

def result(label, passed):
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}")
    return passed

def get_stack(name):
    try:
        r      = cfn.describe_stacks(StackName=name)
        stacks = r.get('Stacks', [])
        return stacks[0] if stacks else None
    except cfn.exceptions.ClientError:
        return None
    except Exception:
        return None

def test_stack_exists(name):
    stack = get_stack(name)
    if not stack:
        result(f"Stack '{name}' exists", False)
        return None
    status = stack.get('StackStatus', '')
    result(f"Stack '{name}' exists", True)
    result(f"Stack '{name}' status = CREATE_COMPLETE (got {status})",
           status in ['CREATE_COMPLETE', 'UPDATE_COMPLETE'])
    return stack

def test_termination_protection(stack, name):
    if not stack:
        return result(f"'{name}' termination protection enabled", False)
    tp = stack.get('EnableTerminationProtection', False)
    return result(f"'{name}' termination protection enabled", tp)

def test_vpc_stack_outputs(stack):
    if not stack:
        return result("VPC stack has required outputs", False)
    outputs = {o['OutputKey']: o['OutputValue']
               for o in stack.get('Outputs', [])}
    for key in EXPECTED_VPC_OUTPUTS:
        result(f"VPC stack output '{key}' exists", key in outputs)
    return outputs

def test_vpc_stack_exports(stack):
    if not stack:
        return
    exports_r = cfn.list_exports()
    exports   = {e['Name']: e['Value']
                 for e in exports_r.get('Exports', [])}
    expected_exports = [
        f"{VPC_STACK_NAME}-VpcId",
        f"{VPC_STACK_NAME}-PublicSubnetA",
        f"{VPC_STACK_NAME}-PublicSubnetB",
        f"{VPC_STACK_NAME}-PrivateSubnetA"
    ]
    for exp in expected_exports:
        result(f"CFN export '{exp}' available", exp in exports)

def test_vpc_resource_created(stack):
    if not stack:
        return result("VPC created by stack", False)
    try:
        r     = cfn.list_stack_resources(StackName=VPC_STACK_NAME)
        ress  = {res['LogicalResourceId']: res
                 for res in r.get('StackResourceSummaries', [])}
        result("VPC resource (VPC) in stack",
               'VPC' in ress)
        result("InternetGateway resource in stack",
               'InternetGateway' in ress)
        result("PublicSubnetA resource in stack",
               'PublicSubnetA' in ress)
        result("PublicSubnetB resource in stack",
               'PublicSubnetB' in ress)
        result("PrivateSubnetA resource in stack",
               'PrivateSubnetA' in ress)
    except Exception as e:
        result(f"Stack resource check ERROR: {e}", False)

def test_ec2_stack_resources(stack):
    if not stack:
        return
    try:
        r    = cfn.list_stack_resources(StackName=EC2_STACK_NAME)
        ress = {res['LogicalResourceId']: res
                for res in r.get('StackResourceSummaries', [])}
        result("WebServerSG resource in ec2 stack",
               'WebServerSG' in ress)
        result("WebServerInstance resource in ec2 stack",
               'WebServerInstance' in ress)
    except Exception as e:
        result(f"EC2 stack resource check ERROR: {e}", False)

def test_stack_policy(stack_name):
    try:
        r      = cfn.get_stack_policy(StackName=stack_name)
        policy = r.get('StackPolicyBody', '')
        result(f"Stack policy set on '{stack_name}'", bool(policy))
        if policy:
            pol = json.loads(policy)
            stmts = pol.get('Statement', [])
            has_deny = any(s.get('Effect') == 'Deny' for s in stmts)
            result("Stack policy has at least one Deny statement",
                   has_deny)
    except Exception as e:
        result(f"Stack policy check ERROR: {e}", False)

def test_drift_detection():
    try:
        r      = cfn.describe_stack_resource_drifts(
                     StackName=EC2_STACK_NAME)
        drifts = r.get('StackResourceDrifts', [])
        result("Drift detection has been run on ec2 stack",
               True)
        for d in drifts:
            logical = d.get('LogicalResourceId', '')
            status  = d.get('StackResourceDriftStatus', '')
            result(f"  Resource '{logical}' drift status = {status}",
                   status in ['MODIFIED', 'IN_SYNC', 'DELETED', 'NOT_CHECKED'])
    except cfn.exceptions.ClientError as e:
        if 'does not exist' in str(e):
            result("Drift detection run (no results yet)", False)
        else:
            result(f"Drift detection check ERROR: {e}", False)

def run_test_cases(credentials):
    global cfn,ec2
    cfn = boto3.client('cloudformation',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    ec2 = boto3.client('ec2',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    print("=" * 65)
    print("LAB 28 VALIDATION: CloudFormation Infrastructure as Code")
    print("=" * 65)
    print("\n── VPC Stack ──")
    vpc_stack = test_stack_exists(VPC_STACK_NAME)
    test_termination_protection(vpc_stack, VPC_STACK_NAME)
    test_vpc_stack_outputs(vpc_stack)
    test_vpc_stack_exports(vpc_stack)
    test_vpc_resource_created(vpc_stack)
    print("\n── EC2 Stack ──")
    ec2_stack = test_stack_exists(EC2_STACK_NAME)
    test_ec2_stack_resources(ec2_stack)
    print("\n── Stack Governance ──")
    test_stack_policy(VPC_STACK_NAME)
    test_drift_detection()
    print("=" * 65)