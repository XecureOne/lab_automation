import boto3
import urllib.request
import json

eb  = ""
s3  = ""
iam = ""
ec2 = ""

APP_NAME     = "PHPWebApplication"
ENV_NAMES    = ["PHPWebApp-prod", "PHPWebApp-staging"]
SERVICE_ROLE = "aws-elasticbeanstalk-service-role"
EC2_ROLE     = "aws-elasticbeanstalk-ec2-role"

def result(label, passed):
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}")
    return passed

def test_application_exists():
    try:
        r    = eb.describe_applications(ApplicationNames=[APP_NAME])
        apps = r.get('Applications', [])
        result(f"EB Application '{APP_NAME}' exists", len(apps) > 0)
        return apps[0] if apps else None
    except Exception as e:
        result(f"EB Application check ERROR: {e}", False)
        return None

def test_application_versions(app):
    if not app:
        return result("Application has ≥ 2 versions", False)
    try:
        r     = eb.describe_application_versions(
                    ApplicationName=APP_NAME)
        vers  = r.get('ApplicationVersions', [])
        result(f"Application has {len(vers)} version(s) (expect ≥ 1)",
               len(vers) >= 1)
        labels = [v['VersionLabel'] for v in vers]
        result("Version 'v1' exists", 'v1' in labels)
    except Exception as e:
        result(f"Application version check ERROR: {e}", False)

def test_environments():
    results = {}
    for env_name in ENV_NAMES:
        try:
            r    = eb.describe_environments(
                       ApplicationName=APP_NAME,
                       EnvironmentNames=[env_name])
            envs = r.get('Environments', [])
            if envs:
                env = envs[0]
                status = env.get('Status', '')
                health = env.get('Health', '')
                result(f"Environment '{env_name}' exists "
                       f"(status={status}, health={health})",
                       status in ['Ready', 'Updating'])
                result(f"Environment '{env_name}' health is Green or Ok",
                       health in ['Green', 'Ok'])
                results[env_name] = env
            else:
                result(f"Environment '{env_name}' exists", False)
        except Exception as e:
            result(f"Environment '{env_name}' check ERROR: {e}", False)
    return results

def test_environment_tier(envs):
    for name, env in envs.items():
        tier = env.get('Tier', {}).get('Name', '')
        result(f"'{name}' is WebServer tier", tier == 'WebServer')

def test_environment_platform(envs):
    for name, env in envs.items():
        platform = env.get('SolutionStackName', '')
        result(f"'{name}' uses PHP platform",
               'PHP' in platform or 'php' in platform.lower())

def test_environment_cname(envs):
    for name, env in envs.items():
        cname = env.get('CNAME', '')
        result(f"'{name}' has CNAME configured", bool(cname))

def test_environment_url(envs):
    for name, env in envs.items():
        cname = env.get('CNAME', '')
        if not cname:
            result(f"'{name}' URL accessible", False)
            continue
        url = f"http://{cname}"
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                body = resp.read().decode('utf-8', errors='ignore')
                result(f"'{name}' HTTP response = 200",
                       resp.getcode() == 200)
                result(f"'{name}' serves PHP application",
                       'Elastic Beanstalk' in body or 'PHP' in body)
        except Exception as e:
            result(f"'{name}' URL test ERROR: {e}", False)

def test_iam_roles():
    for role_name in [SERVICE_ROLE, EC2_ROLE]:
        try:
            iam.get_role(RoleName=role_name)
            result(f"IAM role '{role_name}' exists", True)
        except iam.exceptions.NoSuchEntityException:
            result(f"IAM role '{role_name}' exists", False)
        except Exception as e:
            result(f"IAM role '{role_name}' check ERROR: {e}", False)

def test_artifact_bucket():
    try:
        r       = s3.list_buckets()
        buckets = [b['Name'] for b in r['Buckets']
                   if b['Name'].startswith('elasticbeanstalk-')]
        result("Beanstalk S3 artifact bucket exists",
               len(buckets) > 0)
        if buckets:
            result(f"Artifact bucket: {buckets[0]}", True)
    except Exception as e:
        result(f"Artifact bucket check ERROR: {e}", False)

def test_enhanced_health(envs):
    for name, env in envs.items():
        health_status = env.get('HealthStatus', '')
        result(f"'{name}' enhanced health status configured",
               bool(health_status))

def run_test_cases(credentials):
    global eb,s3,iam,ec2
    eb  = boto3.client('elasticbeanstalk',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    s3  = boto3.client('s3',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    iam = boto3.client('iam',
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
    print("LAB 26 VALIDATION: Elastic Beanstalk Deployment")
    print("=" * 65)
    app = test_application_exists()
    test_application_versions(app)
    print()
    envs = test_environments()
    test_environment_tier(envs)
    test_environment_platform(envs)
    test_environment_cname(envs)
    test_environment_url(envs)
    test_enhanced_health(envs)
    print()
    test_iam_roles()
    test_artifact_bucket()
    print("=" * 65)