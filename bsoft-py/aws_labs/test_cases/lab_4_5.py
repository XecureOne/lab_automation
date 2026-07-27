import boto3

dynamodb = ''

TABLE_NAME = "employee-table"
PARTITION_KEY_NAME = "EmployeeID"
PARTITION_KEY_TYPE = "S"
EMPLOYEE_1_ID = "EMP001"
EMPLOYEE_2_ID = "EMP002"
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

def _get_item(employee_id):
    response = dynamodb.get_item(TableName=TABLE_NAME, Key={PARTITION_KEY_NAME: {'S': employee_id}})
    return response.get('Item')

def test_employee_1_exists():
    label = f"Item '{EMPLOYEE_1_ID}' exists"
    try:
        return result(label, _get_item(EMPLOYEE_1_ID) is not None)
    except Exception as e:
        return result(f"{label} - ERROR: {e}", False)

def test_employee_2_exists():
    label = f"Item '{EMPLOYEE_2_ID}' exists"
    try:
        return result(label, _get_item(EMPLOYEE_2_ID) is not None)
    except Exception as e:
        return result(f"{label} - ERROR: {e}", False)

def test_total_item_count():
    label = f"Total number of items = {EXPECTED_ITEM_COUNT}"
    try:
        response = dynamodb.scan(TableName=TABLE_NAME, Select='COUNT')
        return result(label, response.get('Count') == EXPECTED_ITEM_COUNT)
    except Exception as e:
        return result(f"{label} - ERROR: {e}", False)

def _query_by_employee_id(employee_id):
    return dynamodb.query(
        TableName=TABLE_NAME,
        KeyConditionExpression="#pk = :id",
        ExpressionAttributeNames={"#pk": PARTITION_KEY_NAME},
        ExpressionAttributeValues={":id": {"S": employee_id}},
    )

def test_query_employee_1_returns_one_item():
    label = f"Query for {PARTITION_KEY_NAME} = {EMPLOYEE_1_ID} returns exactly one item"
    try:
        response = _query_by_employee_id(EMPLOYEE_1_ID)
        return result(label, response.get('Count') == 1)
    except Exception as e:
        return result(f"{label} - ERROR: {e}", False)

def test_query_employee_2_returns_one_item():
    label = f"Query for {PARTITION_KEY_NAME} = {EMPLOYEE_2_ID} returns exactly one item"
    try:
        response = _query_by_employee_id(EMPLOYEE_2_ID)
        return result(label, response.get('Count') == 1)
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
    print("LAB 4.5 VALIDATION: Querying Data from Amazon DynamoDB")
    print("=" * 60)

    table_exists = test_table_exists()
    if table_exists:
        test_table_name()
        table_active = test_table_status_active()
        test_partition_key_name()
        test_partition_key_type()

        if table_active:
            employee_1_exists = test_employee_1_exists()
            employee_2_exists = test_employee_2_exists()
            test_total_item_count()

            if employee_1_exists:
                test_query_employee_1_returns_one_item()
            else:
                skip(f"Query for {PARTITION_KEY_NAME} = {EMPLOYEE_1_ID} returns exactly one item")

            if employee_2_exists:
                test_query_employee_2_returns_one_item()
            else:
                skip(f"Query for {PARTITION_KEY_NAME} = {EMPLOYEE_2_ID} returns exactly one item")
        else:
            skip(f"Item '{EMPLOYEE_1_ID}' exists")
            skip(f"Item '{EMPLOYEE_2_ID}' exists")
            skip(f"Total number of items = {EXPECTED_ITEM_COUNT}")
            skip(f"Query for {PARTITION_KEY_NAME} = {EMPLOYEE_1_ID} returns exactly one item")
            skip(f"Query for {PARTITION_KEY_NAME} = {EMPLOYEE_2_ID} returns exactly one item")
    else:
        skip(f"Table Name = '{TABLE_NAME}'")
        skip("Table Status = Active")
        skip(f"Partition Key = '{PARTITION_KEY_NAME}'")
        skip("Partition Key Type = String")
        skip(f"Item '{EMPLOYEE_1_ID}' exists")
        skip(f"Item '{EMPLOYEE_2_ID}' exists")
        skip(f"Total number of items = {EXPECTED_ITEM_COUNT}")
        skip(f"Query for {PARTITION_KEY_NAME} = {EMPLOYEE_1_ID} returns exactly one item")
        skip(f"Query for {PARTITION_KEY_NAME} = {EMPLOYEE_2_ID} returns exactly one item")

    print("=" * 60)
