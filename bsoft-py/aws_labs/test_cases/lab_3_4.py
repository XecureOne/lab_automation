import boto3

elbv2 = ""
ec2   = ""

ALB_NAME   = "php-app-alb"
TG_NAMES   = ["php-web-tg", "php-api-tg"]
INSTANCES  = ["php-web-server", "php-web-server-2"]

def result(label, passed):
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}")
    return passed

def get_alb():
    try:
        r   = elbv2.describe_load_balancers(Names=[ALB_NAME])
        alb = r['LoadBalancers'][0] if r.get('LoadBalancers') else None
        result(f"ALB '{ALB_NAME}' exists", alb is not None)
        return alb
    except elbv2.exceptions.LoadBalancerNotFoundException:
        result(f"ALB '{ALB_NAME}' exists", False)
        return None
    except Exception as e:
        result(f"ALB check ERROR: {e}", False)
        return None

def test_alb_state(alb):
    if not alb:
        return
    result("ALB state = active",
           alb.get('State', {}).get('Code') == 'active')
    result("ALB is internet-facing",
           alb.get('Scheme') == 'internet-facing')
    result("ALB spans ≥ 2 AZs",
           len(alb.get('AvailabilityZones', [])) >= 2)

def test_target_groups():
    results = {}
    for name in TG_NAMES:
        try:
            r  = elbv2.describe_target_groups(Names=[name])
            tg = r['TargetGroups'][0] if r.get('TargetGroups') else None
            result(f"Target group '{name}' exists", tg is not None)
            results[name] = tg
        except elbv2.exceptions.TargetGroupNotFoundException:
            result(f"Target group '{name}' exists", False)
            results[name] = None
        except Exception as e:
            result(f"TG '{name}' check ERROR: {e}", False)
            results[name] = None
    return results

def test_target_health(tgs):
    for name, tg in tgs.items():
        if not tg:
            result(f"'{name}' has ≥ 1 registered target", False)
            continue
        try:
            r       = elbv2.describe_target_health(
                          TargetGroupArn=tg['TargetGroupArn'])
            healths = r.get('TargetHealthDescriptions', [])
            result(f"'{name}' has ≥ 1 registered target", len(healths) >= 1)
        except Exception as e:
            result(f"Target health '{name}' ERROR: {e}", False)

def test_listeners_and_rules(alb):
    if not alb:
        return
    try:
        r         = elbv2.describe_listeners(
                        LoadBalancerArn=alb['LoadBalancerArn'])
        listeners = r.get('Listeners', [])
        http_l    = [l for l in listeners
                     if l.get('Port') == 80 and l.get('Protocol') == 'HTTP']
        result("HTTP listener on port 80 exists", len(http_l) > 0)
        if http_l:
            rules_r = elbv2.describe_rules(
                          ListenerArn=http_l[0]['ListenerArn'])
            rules   = rules_r.get('Rules', [])
            path_rules = [
                r for r in rules
                for c in r.get('Conditions', [])
                if c.get('Field') == 'path-pattern'
            ]
            result("Path-based routing rule exists on listener",
                   len(path_rules) > 0)
            if path_rules:
                vals = path_rules[0]['Conditions'][0].get('Values', [])
                result("Path rule targets '/api/*'",
                       any('/api/' in v or '/api*' in v for v in vals))
    except Exception as e:
        result(f"Listener/rule check ERROR: {e}", False)

def test_sticky_sessions(tgs):
    tg = tgs.get("php-web-tg")
    if not tg:
        return result("php-web-tg has sticky sessions enabled", False)
    try:
        r     = elbv2.describe_target_group_attributes(
                    TargetGroupArn=tg['TargetGroupArn'])
        attrs = {a['Key']: a['Value'] for a in r.get('Attributes', [])}
        result("Stickiness enabled on php-web-tg",
               attrs.get('stickiness.enabled') == 'true')
    except Exception as e:
        result(f"Stickiness check ERROR: {e}", False)

def run_test_cases(credentials):
    global elbv2,ec2
    elbv2 = boto3.client('elbv2',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    ec2   = boto3.client('ec2',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    print("=" * 60)
    print("LAB 3_4 VALIDATION: ELB Configuration")
    print("=" * 60)
    alb = get_alb()
    test_alb_state(alb)
    tgs = test_target_groups()
    test_target_health(tgs)
    test_listeners_and_rules(alb)
    test_sticky_sessions(tgs)
    print("=" * 60)