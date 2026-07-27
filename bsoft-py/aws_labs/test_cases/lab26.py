import boto3
import urllib.request

ecs    = ""
elbv2  = ""
iam    = ""
logs   = ""
appas  = ""

CLUSTER_NAME    = "php-app-cluster"
SERVICE_NAME    = "php-web-service"
TASK_FAMILY     = "php-web-task"
ALB_NAME        = "php-ecs-alb"
TG_NAME         = "php-ecs-tg"
EXEC_ROLE       = "ecsTaskExecutionRole"
TASK_ROLE       = "php-app-task-role"

def result(label, passed):
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}")
    return passed

def test_cluster_exists():
    try:
        r        = ecs.describe_clusters(clusters=[CLUSTER_NAME])
        clusters = [c for c in r.get('clusters', [])
                    if c.get('status') == 'ACTIVE']
        result(f"ECS Cluster '{CLUSTER_NAME}' exists and ACTIVE",
               len(clusters) > 0)
        if clusters:
            settings = clusters[0].get('settings', [])
            ci = any(s.get('name') == 'containerInsights' and
                     s.get('value') == 'enabled'
                     for s in settings)
            result("Container Insights enabled on cluster", ci)
        return clusters[0] if clusters else None
    except Exception as e:
        result(f"Cluster check ERROR: {e}", False)
        return None

def test_task_definition():
    try:
        r   = ecs.describe_task_definition(
                  taskDefinition=TASK_FAMILY)
        td  = r.get('taskDefinition', {})
        result(f"Task Definition '{TASK_FAMILY}' exists", True)
        result("Task uses FARGATE launch type",
               'FARGATE' in td.get('requiresCompatibilities', []))
        containers = td.get('containerDefinitions', [])
        result(f"Task has {len(containers)} container(s) (expect 1)",
               len(containers) >= 1)
        if containers:
            c       = containers[0]
            log_cfg = c.get('logConfiguration', {})
            result("Container uses awslogs driver",
                   log_cfg.get('logDriver') == 'awslogs')
            result("Container exposes port 80",
                   any(pm.get('containerPort') == 80
                       for pm in c.get('portMappings', [])))
        result("Task execution role configured",
               bool(td.get('executionRoleArn')))
        result("Task role configured",
               bool(td.get('taskRoleArn')))
        return td
    except ecs.exceptions.ClientException:
        result(f"Task Definition '{TASK_FAMILY}' exists", False)
        return None
    except Exception as e:
        result(f"Task definition check ERROR: {e}", False)
        return None

def test_service():
    try:
        r    = ecs.describe_services(
                   cluster=CLUSTER_NAME,
                   services=[SERVICE_NAME])
        svcs = [s for s in r.get('services', [])
                if s.get('status') == 'ACTIVE']
        result(f"ECS Service '{SERVICE_NAME}' exists and ACTIVE",
               len(svcs) > 0)
        if not svcs:
            return None
        svc = svcs[0]
        result(f"Service desired count ≥ 1 "
               f"(got {svc.get('desiredCount')})",
               svc.get('desiredCount', 0) >= 1)
        result(f"Service running count ≥ 1 "
               f"(got {svc.get('runningCount')})",
               svc.get('runningCount', 0) >= 1)
        deploy_cfg = svc.get('deploymentConfiguration', {})
        cb  = svc.get('deploymentController', {})
        result("Deployment type = ECS (rolling)",
               cb.get('type', '') in ['ECS', ''])
        lbs = svc.get('loadBalancers', [])
        result("Service has load balancer attached", len(lbs) > 0)
        return svc
    except Exception as e:
        result(f"Service check ERROR: {e}", False)
        return None

def test_tasks_running():
    try:
        r     = ecs.list_tasks(
                    cluster=CLUSTER_NAME,
                    serviceName=SERVICE_NAME,
                    desiredStatus='RUNNING')
        tasks = r.get('taskArns', [])
        result(f"At least 1 task in RUNNING state (found {len(tasks)})",
               len(tasks) >= 1)
        if tasks:
            details = ecs.describe_tasks(
                          cluster=CLUSTER_NAME,
                          tasks=tasks[:3])
            for t in details.get('tasks', []):
                health = t.get('healthStatus', '')
                result(f"  Task health = HEALTHY (got {health})",
                       health in ['HEALTHY', 'UNKNOWN'])
    except Exception as e:
        result(f"Task health check ERROR: {e}", False)

def test_alb_and_tg():
    try:
        r   = elbv2.describe_load_balancers(Names=[ALB_NAME])
        alb = r['LoadBalancers'][0] if r.get('LoadBalancers') else None
        result(f"ALB '{ALB_NAME}' exists", alb is not None)
        if alb:
            result("ALB state = active",
                   alb.get('State', {}).get('Code') == 'active')
        r2  = elbv2.describe_target_groups(Names=[TG_NAME])
        tg  = r2['TargetGroups'][0] if r2.get('TargetGroups') else None
        result(f"Target Group '{TG_NAME}' exists", tg is not None)
        if tg:
            result("TG target type = ip (required for Fargate)",
                   tg.get('TargetType') == 'ip')
            r3   = elbv2.describe_target_health(
                       TargetGroupArn=tg['TargetGroupArn'])
            healths = r3.get('TargetHealthDescriptions', [])
            all_ok  = all(h['TargetHealth']['State'] == 'healthy'
                          for h in healths)
            result(f"All TG targets healthy ({len(healths)} targets)",
                   all_ok and len(healths) > 0)
        if alb:
            region = boto3.session.Session().region_name
            url    = f"http://{alb['DNSName']}"
            try:
                with urllib.request.urlopen(url, timeout=15) as resp:
                    result("ALB URL returns HTTP 200",
                           resp.getcode() == 200)
            except Exception as e:
                result(f"ALB URL test ERROR: {e}", False)
    except elbv2.exceptions.LoadBalancerNotFoundException:
        result(f"ALB '{ALB_NAME}' exists", False)
    except Exception as e:
        result(f"ALB/TG check ERROR: {e}", False)

def test_service_autoscaling():
    try:
        resource_id = f"service/{CLUSTER_NAME}/{SERVICE_NAME}"
        r = appas.describe_scalable_targets(
                ServiceNamespace='ecs',
                ResourceIds=[resource_id])
        targets = r.get('ScalableTargets', [])
        result("Service auto scaling target registered",
               len(targets) > 0)
        if targets:
            t = targets[0]
            result(f"Min capacity ≥ 1 (got {t.get('MinCapacity')})",
                   t.get('MinCapacity', 0) >= 1)
            result(f"Max capacity ≥ 3 (got {t.get('MaxCapacity')})",
                   t.get('MaxCapacity', 0) >= 3)
        r2       = appas.describe_scaling_policies(
                       ServiceNamespace='ecs',
                       ResourceId=resource_id)
        policies = r2.get('ScalingPolicies', [])
        result(f"At least 1 scaling policy exists "
               f"(found {len(policies)})", len(policies) >= 1)
    except Exception as e:
        result(f"Service auto scaling check ERROR: {e}", False)

def test_cloudwatch_logs():
    log_group = "/ecs/php-web-task"
    try:
        r      = logs.describe_log_groups(
                     logGroupNamePrefix=log_group)
        groups = r.get('logGroups', [])
        return result(f"CloudWatch Log Group '{log_group}' exists",
                      len(groups) > 0)
    except Exception as e:
        return result(f"Log group check ERROR: {e}", False)

def test_iam_roles():
    for role in [EXEC_ROLE, TASK_ROLE]:
        try:
            iam.get_role(RoleName=role)
            result(f"IAM role '{role}' exists", True)
        except iam.exceptions.NoSuchEntityException:
            result(f"IAM role '{role}' exists", False)
        except Exception as e:
            result(f"Role '{role}' check ERROR: {e}", False)

def run_test_cases(credentials):
    global ecs,elbv2,iam,logs,appas
    ecs    = boto3.client('ecs',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    elbv2  = boto3.client('elbv2',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    iam    = boto3.client('iam',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    logs   = boto3.client('logs',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    appas  = boto3.client('application-autoscaling',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    print("=" * 65)
    print("LAB 30 VALIDATION: Amazon ECS with Fargate")
    print("=" * 65)
    print("\n── Cluster ──")
    test_cluster_exists()
    print("\n── Task Definition ──")
    test_task_definition()
    print("\n── Service ──")
    test_service()
    test_tasks_running()
    print("\n── Load Balancer ──")
    test_alb_and_tg()
    print("\n── Auto Scaling ──")
    test_service_autoscaling()
    print("\n── Logging and IAM ──")
    test_cloudwatch_logs()
    test_iam_roles()
    print("=" * 65)