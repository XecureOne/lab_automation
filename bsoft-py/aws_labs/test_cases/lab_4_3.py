import boto3

dynamodb = ''

TABLE_NAME = "customer-table"
PARTITION_KEY_NAME = "CustomerID"
PARTITION_KEY_TYPE = "S"
CUSTOMER_1_ID = "C001"
CUSTOMER_2_ID = "C002"
REQUIRED_ATTRIBUTES = ["Name", "Department", "Country", "Email", "Phone"]
EXPECTED_ITEM_COUNT = 2

def result(label, passed):
    status = "[PASS]" if passed else "[FAIL]"
    print(f"{status} {label}")
    return passed

def skip(label):
    print(f"[SKIP] {label}")
    return None

# ---------------------------------------------------------------------------
# Each check below performs its own independent AWS lookup. run_test_cases
# sequences them to match the student's workflow and skips any check whose
# prerequisite resource was not found, instead of reporting a misleading
# FAIL for something that was never reachable in the first place.
# ---------------------------------------------------------------------------

def _describe_table():
    try:
        return dynamodb.describe_table(TableName=TABLE_NAME).get('Table')
    except dynamodb.exceptions.ResourceNotFoundException:
        return None
    except Exception:
        return None

def test_table_exists():
    return result(f"DynamoDB table '{TABLE_NAME}' exists", _describe_table() is not None)

def test_table_name():
    table = _describe_table()
    return result(f"Table Name = '{TABLE_NAME}'", bool(table) and table.get('TableName') == TABLE_NAME)

def test_table_status_active():
    table = _describe_table()
    return result("Table Status = Active", bool(table) and table.get('TableStatus') == 'ACTIVE')

def _partition_key_schema(table):
    for key in table.get('KeySchema', []):
        if key.get('KeyType') == 'HASH':
            return key.get('AttributeName')
    return None

def test_partition_key_name():
    table = _describe_table()
    label = f"Partition Key = '{PARTITION_KEY_NAME}'"
    if not table:
        return result(label, False)
    return result(label, _partition_key_schema(table) == PARTITION_KEY_NAME)

def test_partition_key_type():
    table = _describe_table()
    label = "Partition Key Type = String"
    if not table:
        return result(label, False)
    key_name = _partition_key_schema(table)
    attribute_type = next(
        (a.get('AttributeType') for a in table.get('AttributeDefinitions', []) if a.get('AttributeName') == key_name),
        None,
    )
    return result(label, attribute_type == PARTITION_KEY_TYPE)

def _get_item(customer_id):
    response = dynamodb.get_item(TableName=TABLE_NAME, Key={PARTITION_KEY_NAME: {'S': customer_id}})
    return response.get('Item')

def test_item_c001_exists():
    label = f"Item '{CUSTOMER_1_ID}' exists"
    try:
        return result(label, _get_item(CUSTOMER_1_ID) is not None)
    except Exception as e:
        return result(f"{label} - ERROR: {e}", False)

def test_item_c001_has_required_attributes():
    label = f"Item '{CUSTOMER_1_ID}' contains all required attributes"
    try:
        item = _get_item(CUSTOMER_1_ID)
        if not item:
            return result(label, False)
        has_all = all(attr in item for attr in REQUIRED_ATTRIBUTES)
        return result(label, has_all)
    except Exception as e:
        return result(f"{label} - ERROR: {e}", False)

def test_item_c002_exists():
    label = f"Item '{CUSTOMER_2_ID}' exists"
    try:
        return result(label, _get_item(CUSTOMER_2_ID) is not None)
    except Exception as e:
        return result(f"{label} - ERROR: {e}", False)

def test_item_c002_has_required_attributes():
    label = f"Item '{CUSTOMER_2_ID}' contains all required attributes"
    try:
        item = _get_item(CUSTOMER_2_ID)
        if not item:
            return result(label, False)
        has_all = all(attr in item for attr in REQUIRED_ATTRIBUTES)
        return result(label, has_all)
    except Exception as e:
        return result(f"{label} - ERROR: {e}", False)

def test_total_item_count():
    label = f"Total number of items = {EXPECTED_ITEM_COUNT}"
    try:
        response = dynamodb.scan(TableName=TABLE_NAME, Select='COUNT')
        return result(label, response.get('Count') == EXPECTED_ITEM_COUNT)
    except Exception as e:
        return result(f"{label} - ERROR: {e}", False)

def run_test_cases(credentials):
    global dynamodb
    dynamodb = boto3.client(
        'dynamodb',
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )
    print("=" * 60)
    print("LAB 4.3 VALIDATION: Creating an Amazon DynamoDB Table")
    print("=" * 60)

    table_exists = test_table_exists()
    if table_exists:
        test_table_name()
        table_active = test_table_status_active()
        test_partition_key_name()
        test_partition_key_type()

        if table_active:
            c001_exists = test_item_c001_exists()
            if c001_exists:
                test_item_c001_has_required_attributes()
            else:
                skip(f"Item '{CUSTOMER_1_ID}' contains all required attributes")

            c002_exists = test_item_c002_exists()
            if c002_exists:
                test_item_c002_has_required_attributes()
            else:
                skip(f"Item '{CUSTOMER_2_ID}' contains all required attributes")

            test_total_item_count()
        else:
            skip(f"Item '{CUSTOMER_1_ID}' exists")
            skip(f"Item '{CUSTOMER_1_ID}' contains all required attributes")
            skip(f"Item '{CUSTOMER_2_ID}' exists")
            skip(f"Item '{CUSTOMER_2_ID}' contains all required attributes")
            skip(f"Total number of items = {EXPECTED_ITEM_COUNT}")
    else:
        skip(f"Table Name = '{TABLE_NAME}'")
        skip("Table Status = Active")
        skip(f"Partition Key = '{PARTITION_KEY_NAME}'")
        skip("Partition Key Type = String")
        skip(f"Item '{CUSTOMER_1_ID}' exists")
        skip(f"Item '{CUSTOMER_1_ID}' contains all required attributes")
        skip(f"Item '{CUSTOMER_2_ID}' exists")
        skip(f"Item '{CUSTOMER_2_ID}' contains all required attributes")
        skip(f"Total number of items = {EXPECTED_ITEM_COUNT}")

    print("=" * 60)
