import boto3

s3 = ''

BUCKET_PREFIX = "lab-3-7-"
IMAGES_FOLDER = "images/"
DOCUMENTS_FOLDER = "documents/"
REPORT_COPY_NAME = "report-copy"

def result(label, passed):
    status = "[PASS]" if passed else "[FAIL]"
    print(f"{status} {label}")
    return passed

def skip(label):
    print(f"[SKIP] {label}")
    return None

# ---------------------------------------------------------------------------
# Each check below performs its own independent AWS lookup. If the bucket
# itself was never created, every dependent check is marked [SKIP] instead
# of [FAIL], since there is nothing meaningful to inspect.
# ---------------------------------------------------------------------------

def _find_lab_bucket_name():
    buckets = s3.list_buckets().get('Buckets', [])
    matching = [b['Name'] for b in buckets if b['Name'].startswith(BUCKET_PREFIX)]
    return matching[0] if len(matching) == 1 else None

def test_bucket_created():
    return result("S3 Bucket created", _find_lab_bucket_name() is not None)

def test_bucket_name_prefix():
    bucket_name = _find_lab_bucket_name()
    if not bucket_name:
        return result(f"Bucket name begins with '{BUCKET_PREFIX}'", False)
    return result(f"Bucket name begins with '{BUCKET_PREFIX}'", bucket_name.startswith(BUCKET_PREFIX))

def _list_bucket_objects(bucket_name):
    objects = []
    paginator = s3.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=bucket_name):
        objects.extend(page.get('Contents', []))
    return objects

def _folder_exists(bucket_name, folder_prefix):
    keys = [obj['Key'] for obj in _list_bucket_objects(bucket_name)]
    return any(k == folder_prefix or k.startswith(folder_prefix) for k in keys)

def test_images_folder_exists():
    bucket_name = _find_lab_bucket_name()
    label = f"Folder '{IMAGES_FOLDER.rstrip('/')}' exists"
    if not bucket_name:
        return result(label, False)
    try:
        return result(label, _folder_exists(bucket_name, IMAGES_FOLDER))
    except Exception as e:
        return result(f"{label} - ERROR: {e}", False)

def test_documents_folder_exists():
    bucket_name = _find_lab_bucket_name()
    label = f"Folder '{DOCUMENTS_FOLDER.rstrip('/')}' exists"
    if not bucket_name:
        return result(label, False)
    try:
        return result(label, _folder_exists(bucket_name, DOCUMENTS_FOLDER))
    except Exception as e:
        return result(f"{label} - ERROR: {e}", False)

def _documents_folder_objects(bucket_name):
    objects = _list_bucket_objects(bucket_name)
    return [
        obj for obj in objects
        if obj['Key'].startswith(DOCUMENTS_FOLDER) and obj['Key'] != DOCUMENTS_FOLDER and obj.get('Size', 0) >= 0
    ]

def test_document_renamed_to_report_copy():
    """
    The console's "Rename" action performs a copy-to-new-key plus delete
    of the old key internally, so from the API's point of view this looks
    identical to a plain object existing under the new name - there is
    only one object left under documents/ and its key contains
    'report-copy'.
    """
    bucket_name = _find_lab_bucket_name()
    label = f"Document renamed successfully to '{REPORT_COPY_NAME}'"
    if not bucket_name:
        return result(label, False)
    try:
        docs = _documents_folder_objects(bucket_name)
        renamed = len(docs) == 1 and REPORT_COPY_NAME in docs[0]['Key'].rsplit('/', 1)[-1]
        return result(label, renamed)
    except Exception as e:
        return result(f"{label} - ERROR: {e}", False)

def test_images_folder_empty():
    bucket_name = _find_lab_bucket_name()
    label = "Images folder contains no objects (uploaded image deleted)"
    if not bucket_name:
        return result(label, False)
    try:
        objects = _list_bucket_objects(bucket_name)
        remaining = [
            obj for obj in objects
            if obj['Key'].startswith(IMAGES_FOLDER) and obj['Key'] != IMAGES_FOLDER and obj.get('Size', 0) > 0
        ]
        return result(label, len(remaining) == 0)
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
    print("=" * 60)
    print("LAB 3.7 VALIDATION: Create an S3 Bucket, Upload, Rename and Delete Objects")
    print("=" * 60)

    bucket_created = test_bucket_created()
    if bucket_created:
        test_bucket_name_prefix()
        test_images_folder_exists()
        test_documents_folder_exists()
        test_document_renamed_to_report_copy()
        test_images_folder_empty()
    else:
        skip(f"Bucket name begins with '{BUCKET_PREFIX}'")
        skip(f"Folder '{IMAGES_FOLDER.rstrip('/')}' exists")
        skip(f"Folder '{DOCUMENTS_FOLDER.rstrip('/')}' exists")
        skip(f"Document renamed successfully to '{REPORT_COPY_NAME}'")
        skip("Images folder contains no objects (uploaded image deleted)")

    print("=" * 60)
