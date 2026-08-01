import boto3


sqs = ""

QUEUE_NAME = "lab-5-1-queue"
EXPECTED_ATTRIBUTES = {
    "VisibilityTimeout": "30",
    "MessageRetentionPeriod": "345600",
    "DelaySeconds": "0",
    "MaximumMessageSize": "262144",
    "ReceiveMessageWaitTimeSeconds": "0",
}
MINIMUM_MESSAGE_COUNT = 3


def result(label, passed):
    status = "[PASS]" if passed else "[FAIL]"
    print(f"{status} {label}")
    return passed


def skip(label):
    print(f"[SKIP] {label}")
    return None


def _find_queue_url():
    """Return the exact queue URL without treating similarly named queues as matches."""
    try:
        response = sqs.list_queues(QueueNamePrefix=QUEUE_NAME)
        for queue_url in response.get("QueueUrls", []):
            if queue_url.rstrip("/").rsplit("/", 1)[-1] == QUEUE_NAME:
                return queue_url
    except Exception:
        return None
    return None


def _get_attributes(queue_url):
    try:
        return sqs.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=["All"],
        ).get("Attributes", {})
    except Exception:
        return None


def test_queue_exists(queue_url):
    return result(f"SQS queue '{QUEUE_NAME}' exists", queue_url is not None)


def test_queue_name(queue_url):
    actual_name = queue_url.rstrip("/").rsplit("/", 1)[-1]
    return result(f"Queue Name = '{QUEUE_NAME}'", actual_name == QUEUE_NAME)


def test_queue_type(attributes):
    is_standard = attributes.get("FifoQueue", "false").lower() != "true"
    return result("Queue Type = Standard", is_standard)


def test_attribute(attributes, key, label):
    return result(label, attributes.get(key) == EXPECTED_ATTRIBUTES[key])


def test_message_count(attributes):
    label = f"Queue contains at least {MINIMUM_MESSAGE_COUNT} messages"
    count_keys = (
        "ApproximateNumberOfMessages",
        "ApproximateNumberOfMessagesNotVisible",
        "ApproximateNumberOfMessagesDelayed",
    )
    try:
        total = sum(int(attributes.get(key, "0")) for key in count_keys)
    except (TypeError, ValueError):
        return result(label, False)
    return result(label, total >= MINIMUM_MESSAGE_COUNT)


def run_test_cases(credentials):
    global sqs
    sqs = boto3.client(
        "sqs",
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )

    print("=" * 60)
    print("LAB 5.1 VALIDATION: Create an Amazon SQS Queue")
    print("=" * 60)

    queue_url = _find_queue_url()
    queue_exists = test_queue_exists(queue_url)

    dependent_labels = [
        f"Queue Name = '{QUEUE_NAME}'",
        "Queue Type = Standard",
        "Visibility Timeout = 30 Seconds",
        "Message Retention Period = 4 Days",
        "Delivery Delay = 0 Seconds",
        "Maximum Message Size = 256 KB",
        "Receive Message Wait Time = 0 Seconds",
        f"Queue contains at least {MINIMUM_MESSAGE_COUNT} messages",
    ]

    if not queue_exists:
        for label in dependent_labels:
            skip(label)
        print("=" * 60)
        return

    test_queue_name(queue_url)
    attributes = _get_attributes(queue_url)
    if attributes is None:
        for label in dependent_labels[1:]:
            skip(label)
        print("=" * 60)
        return

    test_queue_type(attributes)
    test_attribute(attributes, "VisibilityTimeout", "Visibility Timeout = 30 Seconds")
    test_attribute(attributes, "MessageRetentionPeriod", "Message Retention Period = 4 Days")
    test_attribute(attributes, "DelaySeconds", "Delivery Delay = 0 Seconds")
    test_attribute(attributes, "MaximumMessageSize", "Maximum Message Size = 256 KB")
    test_attribute(
        attributes,
        "ReceiveMessageWaitTimeSeconds",
        "Receive Message Wait Time = 0 Seconds",
    )
    test_message_count(attributes)

    print("=" * 60)
