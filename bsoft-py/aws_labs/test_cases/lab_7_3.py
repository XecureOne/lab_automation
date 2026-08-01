import boto3

eb        = ""
cw        = ""
asg_client = ""
sns       = ""

ENV_NAME       = "PHPWebApp-prod"
APP_NAME       = "PHPWebApplication"
DASHBOARD_NAME = "BeanstalkPHPDashboard"
ALARM_PREFIX   = "PHPApp-"
SNS_TOPIC_NAME = "eb-php-alerts"

EXPECTED_ALARMS = [
    "PHPApp-HighCPU",
    "PHPApp-EnvironmentDegraded"
]

def result(label, passed):
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}")
    return passed

def get_environment():
    try:
        r    = eb.describe_environments(
                   ApplicationName=APP_NAME,
                   EnvironmentNames=[ENV_NAME])
        envs = r.get('Environments', [])
        return envs[0] if envs else None
    except Exception:
        return None

def test_environment_health():
    env = get_environment()
    if not env:
        return result(f"Environment '{ENV_NAME}' found", False)
    status = env.get('Status', '')
    return result(f"Environment status = Ready (got {status})",
                  status == 'Ready')

def test_scaling_config():
    try:
        r       = eb.describe_configuration_settings(
                      ApplicationName=APP_NAME,
                      EnvironmentName=ENV_NAME)
        options = {}
        for cfg in r.get('ConfigurationSettings', []):
            for opt in cfg.get('OptionSettings', []):
                key = f"{opt['Namespace']}::{opt['OptionName']}"
                options[key] = opt.get('Value', '')

        ns_asg = 'aws:autoscaling:asg'
        min_v  = options.get(f'{ns_asg}::MinSize', '0')
        result(f"ASG MinSize configured (got {min_v})", int(min_v) >= 1)
    except Exception as e:
        result(f"Scaling config check ERROR: {e}", False)

def test_cloudwatch_dashboard():
    try:
        cw.get_dashboard(DashboardName=DASHBOARD_NAME)
        return result(f"CloudWatch Dashboard '{DASHBOARD_NAME}' exists", True)
    except cw.exceptions.DashboardNotFoundError:
        return result(f"CloudWatch Dashboard '{DASHBOARD_NAME}' exists", False)
    except Exception as e:
        return result(f"Dashboard check ERROR: {e}", False)

def test_alarms():
    try:
        r      = cw.describe_alarms(AlarmNamePrefix=ALARM_PREFIX)
        alarms = {a['AlarmName']: a
                  for a in r.get('MetricAlarms', [])}
        for name in EXPECTED_ALARMS:
            result(f"CloudWatch Alarm '{name}' exists", name in alarms)
            if name in alarms:
                a     = alarms[name]
                state = a.get('StateValue', '')
                actions = a.get('AlarmActions', [])
                result(f"  Alarm '{name}' has SNS action",
                       any('sns' in act.lower() for act in actions))
    except Exception as e:
        result(f"Alarm check ERROR: {e}", False)

def test_sns_topic():
    try:
        r      = sns.list_topics()
        topics = r.get('Topics', [])
        found  = any(SNS_TOPIC_NAME in t.get('TopicArn', '')
                     for t in topics)
        result(f"SNS topic '{SNS_TOPIC_NAME}' exists", found)
        if found:
            arn  = next(t['TopicArn'] for t in topics
                        if SNS_TOPIC_NAME in t['TopicArn'])
            subs = sns.list_subscriptions_by_topic(TopicArn=arn)
            result("SNS topic has at least 1 subscription",
                   len(subs.get('Subscriptions', [])) > 0)
    except Exception as e:
        result(f"SNS topic check ERROR: {e}", False)

def test_managed_updates():
    try:
        r       = eb.describe_configuration_settings(
                      ApplicationName=APP_NAME,
                      EnvironmentName=ENV_NAME)
        options = {}
        for cfg in r.get('ConfigurationSettings', []):
            for opt in cfg.get('OptionSettings', []):
                key = f"{opt['Namespace']}::{opt['OptionName']}"
                options[key] = opt.get('Value', '')
        ns  = 'aws:elasticbeanstalk:managedactions'
        mpu = options.get(f'{ns}::ManagedActionsEnabled', 'false')
        result("Managed platform updates enabled",
               mpu.lower() == 'true')
    except Exception as e:
        result(f"Managed updates check ERROR: {e}", False)

def run_test_cases(credentials):
    global eb,cw,asg_client,sns
    eb        = boto3.client('elasticbeanstalk',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    cw        = boto3.client('cloudwatch',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    asg_client = boto3.client('autoscaling',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    sns       = boto3.client('sns',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    print("=" * 65)
    print("LAB 7_3 VALIDATION: Beanstalk Auto Scaling and CloudWatch")
    print("=" * 65)
    test_environment_health()
    test_scaling_config()
    print()
    test_cloudwatch_dashboard()
    test_alarms()
    test_sns_topic()
    print()
    test_managed_updates()
    print("=" * 65)