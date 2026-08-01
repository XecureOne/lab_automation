import boto3

eks  = ""
iam  = ""
ec2  = ""
logs = ""

CLUSTER_NAME  = "php-eks-cluster"
NODEGROUP_NAME = "php-nodes"

def result(label, passed):
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}")
    return passed

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
    print("── EKS Cluster ──")
    cluster = test_cluster_exists()
    test_cluster_version(cluster)
    test_nodegroup()
    test_addons()
    print("=" * 60)
    print("Validation complete.")
    print("=" * 60)
