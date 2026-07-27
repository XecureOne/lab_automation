import boto3
import json

ecr = ""
sts = ""

REPO_NAME = "php-web-app"

def result(label, passed):
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}")
    return passed

def get_account_id():
    try:
        return sts.get_caller_identity()['Account']
    except:
        return None

def get_repo():
    try:
        r     = ecr.describe_repositories(repositoryNames=[REPO_NAME])
        repos = r.get('repositories', [])
        result(f"ECR repository '{REPO_NAME}' exists", len(repos) > 0)
        return repos[0] if repos else None
    except ecr.exceptions.RepositoryNotFoundException:
        result(f"ECR repository '{REPO_NAME}' exists", False)
        return None
    except Exception as e:
        result(f"Repository check ERROR: {e}", False)
        return None

def test_repo_settings(repo):
    if not repo:
        return
    mut  = repo.get('imageTagMutability', '')
    enc  = repo.get('encryptionConfiguration', {}).get('encryptionType', '')
    result("Image tag immutability = IMMUTABLE",
           mut == 'IMMUTABLE')
    result("Repository encryption configured",
           bool(enc))

def test_scan_on_push(repo):
    if not repo:
        return result("Scan on push enabled", False)
    try:
        r   = ecr.describe_repositories(repositoryNames=[REPO_NAME])
        cfg = r['repositories'][0].get(
                  'imageScanningConfiguration', {})
        scan = cfg.get('scanOnPush', False)
        return result("Scan on push is ENABLED", scan)
    except Exception as e:
        return result(f"Scan config check ERROR: {e}", False)

def test_images_exist():
    try:
        r      = ecr.list_images(repositoryName=REPO_NAME)
        images = r.get('imageIds', [])
        result(f"At least 1 image pushed (found {len(images)})",
               len(images) >= 1)
        tags   = [i.get('imageTag', '') for i in images]
        result("Image tag '1.0' exists",   '1.0'    in tags)
        result("Image tag 'latest' exists", 'latest' in tags)
        return images
    except Exception as e:
        result(f"Image list check ERROR: {e}", False)
        return []

def test_scan_findings(images):
    if not images:
        return result("Scan findings available for at least 1 image", False)
    tag = next((i.get('imageTag') for i in images
                if i.get('imageTag') == '1.0'), None)
    if not tag:
        return result("Image 1.0 found for scan check", False)
    try:
        r        = ecr.describe_image_scan_findings(
                       repositoryName=REPO_NAME,
                       imageId={'imageTag': tag})
        status   = r.get('imageScanStatus', {}).get('status', '')
        result(f"Image scan status = COMPLETE (got {status})",
               status == 'COMPLETE')
        findings = r.get('imageScanFindings', {})
        counts   = findings.get('findingSeverityCounts', {})
        critical = counts.get('CRITICAL', 0)
        high     = counts.get('HIGH', 0)
        result(f"Scan complete: CRITICAL={critical}, HIGH={high}",
               True)
        if critical > 0:
            print(f"  WARNING: {critical} CRITICAL vulnerabilities found!")
    except ecr.exceptions.ScanNotFoundException:
        result("Image scan results available", False)
    except Exception as e:
        result(f"Scan findings check ERROR: {e}", False)

def test_lifecycle_policy():
    try:
        r   = ecr.get_lifecycle_policy(repositoryName=REPO_NAME)
        pol = r.get('lifecyclePolicyText', '')
        result("Lifecycle policy exists on repository", bool(pol))
        if pol:
            p = json.loads(pol)
            rules = p.get('rules', [])
            result(f"Lifecycle policy has {len(rules)} rule(s) "
                   f"(expect ≥ 1)", len(rules) >= 1)
            if rules:
                action = rules[0].get('action', {}).get('type', '')
                result("Lifecycle rule action = expire",
                       action == 'expire')
    except ecr.exceptions.LifecyclePolicyNotFoundException:
        result("Lifecycle policy exists on repository", False)
    except Exception as e:
        result(f"Lifecycle policy check ERROR: {e}", False)

def test_repository_policy():
    try:
        r   = ecr.get_repository_policy(repositoryName=REPO_NAME)
        pol = r.get('policyText', '')
        result("Repository policy exists", bool(pol))
        if pol:
            p    = json.loads(pol)
            stmts = p.get('Statement', [])
            ecs  = any('ecs' in str(s.get('Principal', '')).lower()
                       for s in stmts)
            result("Repository policy allows ECS tasks principal", ecs)
    except ecr.exceptions.RepositoryPolicyNotFoundException:
        result("Repository policy exists", False)
    except Exception as e:
        result(f"Repository policy check ERROR: {e}", False)

def run_test_cases(credentials):
    global ecr,sts
    ecr = boto3.client('ecr',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    sts = boto3.client('sts',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"]
    )
    print("=" * 65)
    print("LAB 29 VALIDATION: ECR Setup and Container Image Push")
    print("=" * 65)
    repo = get_repo()
    test_repo_settings(repo)
    test_scan_on_push(repo)
    images = test_images_exist()
    test_scan_findings(images)
    test_lifecycle_policy()
    test_repository_policy()
    print("=" * 65)