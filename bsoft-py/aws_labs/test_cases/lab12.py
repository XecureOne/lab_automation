import boto3

asg_client = ""
cw_client  = ""
elbv2      = ""

ASG_NAME = "php-web-asg"
TG_NAME  = "php-web-tg"

def result(label, passed):
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}")
    return passed

def get_asg():
    try:
        r    = asg_client.describe_auto_scaling_groups(
                   AutoScalingGroupNames=[ASG_NAME])
        asgs = r.get('AutoScalingGroups', [])
        result(f"ASG '{ASG_NAME}' exists", len(asgs) > 0)
        return asgs[0] if asgs else None
    except Exception as e:
        result(f"ASG check ERROR: {e}", False)
        return None

def test_capacity(asg):
    if not asg:
        return
    result("ASG MinSize = 1",     asg.get('MinSize')     == 1)
    result("ASG DesiredCapacity = 2", asg.get('DesiredCapacity') == 2)
    result("ASG MaxSize = 4",     asg.get('MaxSize')     == 4)

def test_health_check_type(asg):
    if not asg:
        return result("ASG uses ELB health checks", False)
    hc = asg.get('HealthCheckType', '')
    return result(f"ASG health check type = ELB (got {hc})", hc == 'ELB')

def test_multi_az_subnets(asg):
    if not asg:
        return result("ASG spans multiple AZs", False)
    zones = asg.get('AvailabilityZones', [])
    return result(f"ASG spans {len(zones)} AZs (expect ≥ 2)", len(zones) >= 2)

def test_target_group_attached(asg):
    if not asg:
        return result("ASG attached to target group", False)
    tg_arns = asg.get('TargetGroupARNs', [])
    result("ASG has ≥ 1 target group attached", len(tg_arns) > 0)
    return tg_arns

def test_scaling_policies():
    try:
        r       = asg_client.describe_policies(
                      AutoScalingGroupName=ASG_NAME)
        policies = r.get('ScalingPolicies', [])
        types    = {p['PolicyName']: p.get('PolicyType', '') for p in policies}
        result("At least 2 scaling policies exist", len(policies) >= 2)
        tt_policies = [p for p in policies
                       if p.get('PolicyType') == 'TargetTrackingScaling']
        result("Target Tracking Scaling policy exists",
               len(tt_policies) > 0)
        if tt_policies:
            tt = tt_policies[0]
            cfg = tt.get('TargetTrackingConfiguration', {})
            metric = cfg.get('PredefinedMetricSpecification', {})
            result("Target Tracking metric = ASGAverageCPUUtilization",
                   metric.get('PredefinedMetricType')
                   == 'ASGAverageCPUUtilization')
            result("Target Tracking value = 50.0",
                   cfg.get('TargetValue') == 50.0)
        step_policies = [p for p in policies
                         if p.get('PolicyType') == 'StepScaling']
        result("Step Scaling policy exists", len(step_policies) > 0)
    except Exception as e:
        result(f"Scaling policy check ERROR: {e}", False)

def test_cw_alarms():
    try:
        r      = cw_client.describe_alarms(
                     AlarmNamePrefix='TargetTracking-' + ASG_NAME)
        alarms = r.get('MetricAlarms', [])
        result("CloudWatch alarms exist for ASG scaling",
               len(alarms) > 0)
        for alarm in alarms:
            state = alarm.get('StateValue', '')
            name  = alarm.get('AlarmName', '')
            result(f"  Alarm '{name}' state={state}",
                   state in ['OK', 'ALARM', 'INSUFFICIENT_DATA'])
    except Exception as e:
        result(f"CloudWatch alarm check ERROR: {e}", False)

def test_instances_in_service(asg):
    if not asg:
        return
    instances = asg.get('Instances', [])
    in_service = [i for i in instances
                  if i.get('LifecycleState') == 'InService']
    result(f"ASG has {len(in_service)} InService instance(s) (expect ≥ 2)",
           len(in_service) >= 2)

def test_asg_tag(asg):
    if not asg:
        return result("ASG instances tagged ManagedBy=ASG", False)
    tags = {t['Key']: t['Value'] for t in asg.get('Tags', [])}
    return result("ASG has tag ManagedBy=ASG",
                  tags.get('ManagedBy') == 'ASG')

def run_test_cases(credentials):
    global asg_client, cw_client, elbv2
    asg_client = boto3.client('autoscaling',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    cw_client  = boto3.client('cloudwatch',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    elbv2      = boto3.client('elbv2',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    print("=" * 60)
    print("LAB 16 VALIDATION: Auto Scaling Group with Scaling Policies")
    print("=" * 60)
    asg = get_asg()
    test_capacity(asg)
    test_health_check_type(asg)
    test_multi_az_subnets(asg)
    test_target_group_attached(asg)
    test_instances_in_service(asg)
    test_asg_tag(asg)
    print()
    test_scaling_policies()
    test_cw_alarms()
    print("=" * 60)