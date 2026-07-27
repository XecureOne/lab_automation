import boto3
import subprocess
import json

eks  = ""
iam  = ""
ec2  = ""
logs = ""

CLUSTER_NAME  = "php-eks-cluster"
NODEGROUP_NAME = "php-nodes"
NAMESPACE     = "php-app"

def result(label, passed):
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}")
    return passed

def run_kubectl(args):
    try:
        r = subprocess.run(
            ['kubectl'] + args,
            capture_output=True, text=True, timeout=30
        )
        return r.returncode == 0, r.stdout, r.stderr
    except FileNotFoundError:
        return False, '', 'kubectl not found'
    except Exception as e:
        return False, '', str(e)

# ── EKS Cluster ──────────────────────────────────────────────────────────────
def test_cluster_exists():
    try:
        r       = eks.describe_cluster(name=CLUSTER_NAME)
        cluster = r.get('cluster', {})
        status  = cluster.get('status', '')
        result(f"EKS Cluster '{CLUSTER_NAME}' exists", True)
        result(f"Cluster status = ACTIVE (got {status})",
               status == 'ACTIVE')
        logging = cluster.get('logging', {})
        enabled = logging.get('clusterLogging', [{}])[0]\
                         .get('enabled', False)
        result("Control plane logging enabled", enabled)
        return cluster
    except eks.exceptions.ResourceNotFoundException:
        result(f"EKS Cluster '{CLUSTER_NAME}' exists", False)
        return None
    except Exception as e:
        result(f"Cluster check ERROR: {e}", False)
        return None

def test_cluster_version(cluster):
    if not cluster:
        return
    version = cluster.get('version', '')
    major, minor = version.split('.') if '.' in version else ('0', '0')
    result(f"Kubernetes version ≥ 1.28 (got {version})",
           int(minor) >= 28)

def test_nodegroup():
    try:
        r  = eks.describe_nodegroup(
                 clusterName=CLUSTER_NAME,
                 nodegroupName=NODEGROUP_NAME)
        ng = r.get('nodegroup', {})
        result(f"Nodegroup '{NODEGROUP_NAME}' exists", True)
        result(f"Nodegroup status = ACTIVE (got {ng.get('status')})",
               ng.get('status') == 'ACTIVE')
        sc = ng.get('scalingConfig', {})
        result(f"Min size ≥ 1 (got {sc.get('minSize')})",
               sc.get('minSize', 0) >= 1)
        result(f"Max size ≥ 2 (got {sc.get('maxSize')})",
               sc.get('maxSize', 0) >= 2)
        result("Nodes in private networking",
               ng.get('amiType', '').startswith('AL2') or
               bool(ng.get('subnets')))
    except eks.exceptions.ResourceNotFoundException:
        result(f"Nodegroup '{NODEGROUP_NAME}' exists", False)
    except Exception as e:
        result(f"Nodegroup check ERROR: {e}", False)

def test_addons():
    expected = ['vpc-cni', 'coredns', 'kube-proxy', 'aws-ebs-csi-driver']
    try:
        r      = eks.list_addons(clusterName=CLUSTER_NAME)
        addons = r.get('addons', [])
        for addon in expected:
            result(f"EKS addon '{addon}' installed",
                   addon in addons)
    except Exception as e:
        result(f"Add-on check ERROR: {e}", False)

# ── kubectl-based checks ─────────────────────────────────────────────────────
def test_nodes_ready():
    ok, out, err = run_kubectl([
        'get', 'nodes',
        '-o', 'jsonpath={.items[*].status.conditions[-1].type}'
    ])
    if not ok:
        return result("kubectl get nodes succeeded", False)
    statuses = out.strip().split()
    ready    = all(s == 'Ready' for s in statuses)
    result(f"All nodes are Ready ({len(statuses)} nodes)", ready)

def test_namespace_exists():
    ok, out, _ = run_kubectl([
        'get', 'namespace', NAMESPACE,
        '-o', 'jsonpath={.metadata.name}'
    ])
    return result(f"Namespace '{NAMESPACE}' exists",
                  ok and NAMESPACE in out)

def test_deployment():
    ok, out, _ = run_kubectl([
        '-n', NAMESPACE, 'get', 'deployment', 'php-web',
        '-o', 'jsonpath={.status.readyReplicas}'
    ])
    if not ok:
        return result("Deployment 'php-web' exists", False)
    ready = int(out.strip()) if out.strip().isdigit() else 0
    result("Deployment 'php-web' exists", ok)
    result(f"Deployment has ≥ 1 ready replica (got {ready})",
           ready >= 1)

def test_service():
    ok, out, _ = run_kubectl([
        '-n', NAMESPACE, 'get', 'service', 'php-web-svc',
        '-o', 'jsonpath={.spec.type}'
    ])
    result("Service 'php-web-svc' exists", ok)
    result("Service type = ClusterIP",
           out.strip() == 'ClusterIP')

def test_ingress():
    ok, out, _ = run_kubectl([
        '-n', NAMESPACE, 'get', 'ingress', 'php-web-ingress',
        '-o', 'jsonpath={.status.loadBalancer.ingress[0].hostname}'
    ])
    result("Ingress 'php-web-ingress' exists", ok)
    hostname = out.strip()
    result("Ingress has ALB hostname assigned", bool(hostname))
    return hostname

def test_hpa():
    ok, out, _ = run_kubectl([
        '-n', NAMESPACE, 'get', 'hpa', 'php-web-hpa',
        '-o', 'jsonpath={.spec.minReplicas},{.spec.maxReplicas}'
    ])
    result("HPA 'php-web-hpa' exists", ok)
    if ok and out.strip():
        parts = out.strip().split(',')
        if len(parts) == 2:
            min_r = int(parts[0]) if parts[0].isdigit() else 0
            max_r = int(parts[1]) if parts[1].isdigit() else 0
            result(f"HPA minReplicas ≥ 1 (got {min_r})", min_r >= 1)
            result(f"HPA maxReplicas ≥ 4 (got {max_r})", max_r >= 4)

def test_irsa_service_account():
    ok, out, _ = run_kubectl([
        '-n', NAMESPACE, 'get', 'serviceaccount', 'php-app-sa',
        '-o', 'jsonpath={.metadata.annotations}'
    ])
    result("ServiceAccount 'php-app-sa' exists", ok)
    if ok:
        result("ServiceAccount has IRSA annotation",
               'eks.amazonaws.com/role-arn' in out)

def test_cloudwatch_logs():
    log_group = f"/aws/eks/{CLUSTER_NAME}/cluster"
    try:
        r      = logs.describe_log_groups(
                     logGroupNamePrefix=log_group)
        groups = r.get('logGroups', [])
        return result(f"EKS control plane log group exists",
                      len(groups) > 0)
    except Exception as e:
        return result(f"Log group check ERROR: {e}", False)

# ── OIDC ─────────────────────────────────────────────────────────────────────
def test_oidc_provider(cluster):
    if not cluster:
        return result("OIDC provider configured for cluster", False)
    oidc_issuer = cluster.get('identity', {}).get(
                      'oidc', {}).get('issuer', '')
    result("OIDC issuer configured on cluster", bool(oidc_issuer))
    if oidc_issuer:
        try:
            provider_url = oidc_issuer.replace('https://', '')
            r      = iam.list_open_id_connect_providers()
            exists = any(
                provider_url in p['Arn']
                for p in r.get('OpenIDConnectProviderList', [])
            )
            result("OIDC provider registered in IAM for IRSA", exists)
        except Exception as e:
            result(f"OIDC IAM check ERROR: {e}", False)

# ── Main ─────────────────────────────────────────────────────────────────────
def run_test_cases(credentials):
    global eks,iam,ec2,logs
    eks  = boto3.client('eks',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    iam  = boto3.client('iam',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    ec2  = boto3.client('ec2',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    logs = boto3.client('logs',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    print("=" * 65)
    print("LAB 31 VALIDATION: Amazon EKS Kubernetes Deployments")
    print("=" * 65)
    print("\n── EKS Cluster ──")
    cluster = test_cluster_exists()
    test_cluster_version(cluster)
    test_oidc_provider(cluster)
    test_nodegroup()
    test_addons()
    print("\n── Kubernetes Resources (kubectl) ──")
    test_nodes_ready()
    test_namespace_exists()
    test_deployment()
    test_service()
    hostname = test_ingress()
    test_hpa()
    test_irsa_service_account()
    print("\n── Logging ──")
    test_cloudwatch_logs()
    print("=" * 65)
    if hostname:
        print(f"\nApplication URL: http://{hostname}")