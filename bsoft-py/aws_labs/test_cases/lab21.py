import boto3
import json

cp  = ""
cb  = ""
cd  = ""
cw  = ""
ssm = ""
s3  = ""
sns = ""

PIPELINE_NAME  = "PHPAppPipeline"
BUILD_PROJECT  = "PHPAppBuild"
CD_APP         = "PHPWebApp"
CD_GROUP       = "PHPWebAppGroup"
DASHBOARD_NAME = "PHPAppDashboard"
BUCKET_PREFIX  = "php-app-pipeline-artifacts-"
SSM_PREFIX     = "/php-app/"

def result(label, passed):
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}")
    return passed

# ── CodePipeline ─────────────────────────────────────────────────────────────
def test_pipeline():
    try:
        r  = cp.get_pipeline(name=PIPELINE_NAME)
        pl = r.get('pipeline', {})
        result(f"Pipeline '{PIPELINE_NAME}' exists", True)
        stages = [s['name'] for s in pl.get('stages', [])]
        result("Pipeline has Source stage",  'Source'  in stages)
        result("Pipeline has Build stage",   'Build'   in stages)
        result("Pipeline has Deploy stage",  'Deploy'  in stages)
        return True
    except cp.exceptions.PipelineNotFoundException:
        result(f"Pipeline '{PIPELINE_NAME}' exists", False)
        return False
    except Exception as e:
        result(f"Pipeline check ERROR: {e}", False)
        return False

def test_pipeline_state():
    try:
        r      = cp.get_pipeline_state(name=PIPELINE_NAME)
        stages = r.get('stageStates', [])
        for stage in stages:
            name    = stage.get('stageName', '')
            latest  = stage.get('latestExecution', {})
            status  = latest.get('status', 'Unknown')
            result(f"Stage '{name}' latest execution: {status}",
                   status in ['Succeeded', 'InProgress', 'Failed'])
    except Exception as e:
        result(f"Pipeline state check ERROR: {e}", False)

# ── CodeBuild ─────────────────────────────────────────────────────────────────
def test_codebuild_project():
    try:
        r    = cb.batch_get_projects(names=[BUILD_PROJECT])
        proj = r.get('projects', [])
        result(f"CodeBuild project '{BUILD_PROJECT}' exists", len(proj) > 0)
        if proj:
            env = proj[0].get('environment', {})
            result("CodeBuild uses managed image",
                   env.get('type') in ['LINUX_CONTAINER',
                                        'ARM_CONTAINER'])
    except Exception as e:
        result(f"CodeBuild check ERROR: {e}", False)

# ── CodeDeploy ───────────────────────────────────────────────────────────────
def test_codedeploy():
    try:
        cd.get_application(applicationName=CD_APP)
        result(f"CodeDeploy application '{CD_APP}' exists", True)
    except cd.exceptions.ApplicationDoesNotExistException:
        result(f"CodeDeploy application '{CD_APP}' exists", False)
        return
    except Exception as e:
        result(f"CodeDeploy app check ERROR: {e}", False)
        return
    try:
        r  = cd.get_deployment_group(
                 applicationName=CD_APP,
                 deploymentGroupName=CD_GROUP)
        dg = r.get('deploymentGroupInfo', {})
        result(f"Deployment group '{CD_GROUP}' exists", True)
        result("Deployment group has load balancer enabled",
               dg.get('loadBalancerInfo') is not None)
    except cd.exceptions.DeploymentGroupDoesNotExistException:
        result(f"Deployment group '{CD_GROUP}' exists", False)
    except Exception as e:
        result(f"Deployment group check ERROR: {e}", False)

# ── S3 Artifact Bucket ───────────────────────────────────────────────────────
def test_artifact_bucket():
    try:
        r       = s3.list_buckets()
        buckets = [b['Name'] for b in r['Buckets']
                   if b['Name'].startswith(BUCKET_PREFIX)]
        result(f"Artifact bucket starting with '{BUCKET_PREFIX}' exists",
               len(buckets) > 0)
        if buckets:
            b = buckets[0]
            v = s3.get_bucket_versioning(Bucket=b)
            result("Artifact bucket versioning enabled",
                   v.get('Status') == 'Enabled')
            pa = s3.get_public_access_block(Bucket=b)
            c  = pa['PublicAccessBlockConfiguration']
            result("Artifact bucket blocks public access",
                   all([c.get('BlockPublicAcls'),
                        c.get('BlockPublicPolicy'),
                        c.get('RestrictPublicBuckets')]))
    except Exception as e:
        result(f"Artifact bucket check ERROR: {e}", False)

# ── SSM Parameters ───────────────────────────────────────────────────────────
def test_ssm_parameters():
    for name in ['/php-app/db-host', '/php-app/db-name', '/php-app/app-env']:
        try:
            ssm.get_parameter(Name=name, WithDecryption=True)
            result(f"SSM parameter '{name}' exists", True)
        except ssm.exceptions.ParameterNotFound:
            result(f"SSM parameter '{name}' exists", False)
        except Exception as e:
            result(f"SSM parameter '{name}' ERROR: {e}", False)

# ── CloudWatch ───────────────────────────────────────────────────────────────
def test_dashboard():
    try:
        cw.get_dashboard(DashboardName=DASHBOARD_NAME)
        return result(f"CloudWatch Dashboard '{DASHBOARD_NAME}' exists", True)
    except cw.exceptions.DashboardNotFoundError:
        return result(f"CloudWatch Dashboard '{DASHBOARD_NAME}' exists", False)
    except Exception as e:
        return result(f"Dashboard check ERROR: {e}", False)

def test_alarms():
    try:
        r      = cw.describe_alarms(AlarmNamePrefix='php-app-')
        alarms = r.get('MetricAlarms', [])
        result(f"At least 2 CloudWatch alarms configured "
               f"(found {len(alarms)})", len(alarms) >= 2)
        for alarm in alarms:
            state = alarm.get('StateValue', '')
            name  = alarm.get('AlarmName', '')
            result(f"  Alarm '{name}' configured (state={state})",
                   True)
    except Exception as e:
        result(f"Alarm check ERROR: {e}", False)

# ── Recent Deployment ─────────────────────────────────────────────────────────
def test_recent_deployment():
    try:
        r    = cd.list_deployments(
                   applicationName=CD_APP,
                   deploymentGroupName=CD_GROUP,
                   includeOnlyStatuses=['Succeeded'])
        deps = r.get('deployments', [])
        result(f"At least 1 successful deployment exists "
               f"(found {len(deps)})", len(deps) >= 1)
    except Exception as e:
        result(f"Deployment history check ERROR: {e}", False)

# ── Main ─────────────────────────────────────────────────────────────────────
def run_test_cases(credentials):
    global cp,cb,cd,cw,ssm,s3,sns
    cp  = boto3.client('codepipeline',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    cb  = boto3.client('codebuild',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    cd  = boto3.client('codedeploy',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    cw  = boto3.client('cloudwatch',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    ssm = boto3.client('ssm',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    s3  = boto3.client('s3',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    sns = boto3.client('sns',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    print("=" * 60)
    print("LAB 25 VALIDATION: Deploy and Manage Application on AWS")
    print("=" * 60)
    print("\n── CodePipeline ──")
    test_pipeline()
    test_pipeline_state()
    print("\n── CodeBuild ──")
    test_codebuild_project()
    print("\n── CodeDeploy ──")
    test_codedeploy()
    print("\n── Artifact Storage ──")
    test_artifact_bucket()
    print("\n── Configuration ──")
    test_ssm_parameters()
    print("\n── Monitoring ──")
    test_dashboard()
    test_alarms()
    print("\n── Deployment History ──")
    test_recent_deployment()
    print("=" * 60)