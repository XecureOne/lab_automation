import boto3

s3 = ''

BUCKET_PREFIX = "lab-3-2-"
IMAGES_FOLDER = "images/"
DOCUMENTS_FOLDER = "documents/"

def result(label, passed):
    status = "[PASS]" if passed else "[FAIL]"
    print(f"{status} {label}")
    return passed

# ---------------------------------------------------------------------------
# Every test case performs its own independent AWS lookup. None of them
# reuse an object discovered by another test case, so deleting one resource
# only fails the test case(s) that specifically search for it.
# ---------------------------------------------------------------------------

def _find_lab_bucket_name():
    buckets = s3.list_buckets().get('Buckets', [])
    matching = [b['Name'] for b in buckets if b['Name'].startswith(BUCKET_PREFIX)]
    return matching[0] if len(matching) == 1 else None

def test_single_bucket_exists():
    try:
        buckets = s3.list_buckets().get('Buckets', [])
        matching = [b['Name'] for b in buckets if b['Name'].startswith(BUCKET_PREFIX)]
        return result("Exactly one S3 bucket exists", len(matching) == 1)
    except Exception as e:
        return result(f"S3 bucket lookup - ERROR: {e}", False)

def test_bucket_name_prefix():
    bucket_name = _find_lab_bucket_name()
    if not bucket_name:
        return result(f"Bucket name starts with '{BUCKET_PREFIX}'", False)
    return result(f"Bucket name starts with '{BUCKET_PREFIX}'", bucket_name.startswith(BUCKET_PREFIX))

def _list_bucket_keys(bucket_name):
    keys = []
    paginator = s3.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=bucket_name):
        for obj in page.get('Contents', []):
            keys.append(obj)
    return keys

def test_images_folder_exists():
    bucket_name = _find_lab_bucket_name()
    label = f"Folder named '{IMAGES_FOLDER.rstrip('/')}' exists"
    if not bucket_name:
        return result(label, False)
    try:
        keys = [obj['Key'] for obj in _list_bucket_keys(bucket_name)]
        folder_exists = any(k == IMAGES_FOLDER or k.startswith(IMAGES_FOLDER) for k in keys)
        return result(label, folder_exists)
    except Exception as e:
        return result(f"Images folder check - ERROR: {e}", False)

def test_documents_folder_exists():
    bucket_name = _find_lab_bucket_name()
    label = f"Folder named '{DOCUMENTS_FOLDER.rstrip('/')}' exists"
    if not bucket_name:
        return result(label, False)
    try:
        keys = [obj['Key'] for obj in _list_bucket_keys(bucket_name)]
        folder_exists = any(k == DOCUMENTS_FOLDER or k.startswith(DOCUMENTS_FOLDER) for k in keys)
        return result(label, folder_exists)
    except Exception as e:
        return result(f"Documents folder check - ERROR: {e}", False)

def test_object_inside_images_folder():
    bucket_name = _find_lab_bucket_name()
    label = "At least one object exists inside the 'images' folder"
    if not bucket_name:
        return result(label, False)
    try:
        objects = _list_bucket_keys(bucket_name)
        has_content = any(
            obj['Key'].startswith(IMAGES_FOLDER) and obj['Key'] != IMAGES_FOLDER and obj.get('Size', 0) > 0
            for obj in objects
        )
        return result(label, has_content)
    except Exception as e:
        return result(f"Images folder content check - ERROR: {e}", False)

def test_object_inside_documents_folder():
    bucket_name = _find_lab_bucket_name()
    label = "At least one object exists inside the 'documents' folder"
    if not bucket_name:
        return result(label, False)
    try:
        objects = _list_bucket_keys(bucket_name)
        has_content = any(
            obj['Key'].startswith(DOCUMENTS_FOLDER) and obj['Key'] != DOCUMENTS_FOLDER and obj.get('Size', 0) > 0
            for obj in objects
        )
        return result(label, has_content)
    except Exception as e:
        return result(f"Documents folder content check - ERROR: {e}", False)

def test_bucket_remains_after_upload():
    label = "Bucket remains present after upload/delete activity"
    try:
        bucket_name = _find_lab_bucket_name()
        if not bucket_name:
            return result(label, False)
        s3.head_bucket(Bucket=bucket_name)
        return result(label, True)
    except Exception as e:
        return result(f"{label} - ERROR: {e}", False)

def run_test_cases(credentials):
    global s3
    s3 = boto3.client(
        's3',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )
    print("=" * 55)
    print("LAB 3.2 VALIDATION: Use Amazon S3 to Store and Manage Static Assets")
    print("=" * 55)

    test_single_bucket_exists()
    test_bucket_name_prefix()
    test_images_folder_exists()
    test_documents_folder_exists()
    test_object_inside_images_folder()
    test_object_inside_documents_folder()
    test_bucket_remains_after_upload()

    print("=" * 55)
