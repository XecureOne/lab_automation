import time

import boto3
from datetime import datetime, timedelta, timezone


sns = ""
sqs = ""
cloudwatch = ""

TOPIC_NAME = "lab-5-3-topic"
QUEUE_NAME = "lab-5-3-queue"

METRIC_RETRY_INTERVAL_SECONDS = 10
METRIC_RETRY_TIMEOUT_SECONDS = 60


def result(label, passed):
    status = "[PASS]" if passed else "[FAIL]"
    print(f"{status} {label}")
    return passed


def skip(label):
    print(f"[SKIP] {label}")
    return None


def _topic_name(topic_arn):
    return topic_arn.rsplit(":", 1)[-1]


def _find_topic_arn():
    """Return the ARN of the exact topic name, including across paginated results."""
    try:
        paginator = sns.get_paginator("list_topics")
        for page in paginator.paginate():
            for topic in page.get("Topics", []):
                topic_arn = topic.get("TopicArn", "")
                if _topic_name(topic_arn) == TOPIC_NAME:
                    return topic_arn
    except Exception:
        return None
    return None


def _find_queue_url():
    try:
        response = sqs.list_queues(QueueNamePrefix=QUEUE_NAME)
        for queue_url in response.get("QueueUrls", []):
            if queue_url.rsplit("/", 1)[-1] == QUEUE_NAME:
                return queue_url
    except Exception:
        return None
    return None


def _get_queue_attributes(queue_url):
    try:
        return sqs.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=["QueueArn"],
        ).get("Attributes", {})
    except Exception:
        return None


def _get_subscriptions(topic_arn):
    try:
        subscriptions = []
        paginator = sns.get_paginator("list_subscriptions_by_topic")
        for page in paginator.paginate(TopicArn=topic_arn):
            subscriptions.extend(page.get("Subscriptions", []))
        return subscriptions
    except Exception:
        return None


def _find_sqs_subscription(subscriptions, queue_arn):
    for subscription in subscriptions:
        if subscription.get("Protocol", "").lower() == "sqs" and subscription.get("Endpoint", "") == queue_arn:
            return subscription
    return None


def test_topic_exists(topic_arn):
    return result(f"SNS topic '{TOPIC_NAME}' exists", topic_arn is not None)


def test_topic_name(topic_arn):
    return result(f"Topic Name = '{TOPIC_NAME}'", _topic_name(topic_arn) == TOPIC_NAME)


def test_queue_exists(queue_url):
    return result(f"SQS queue '{QUEUE_NAME}' exists", queue_url is not None)


def test_queue_name(queue_url):
    return result(f"Queue Name = '{QUEUE_NAME}'", queue_url is not None and queue_url.rsplit("/", 1)[-1] == QUEUE_NAME)


def test_queue_type(queue_url):
    is_standard = queue_url is not None and not queue_url.rsplit("/", 1)[-1].endswith(".fifo")
    return result("Queue Type = Standard", is_standard)



def test_subscription_exists(subscription):
    return result("SNS Subscription to the SQS queue exists", subscription is not None)


def test_subscription_protocol(subscription):
    return result("Subscription Protocol = SQS", subscription.get("Protocol", "").lower() == "sqs")


def test_subscription_status(subscription):
    subscription_arn = subscription.get("SubscriptionArn", "")
    is_confirmed = subscription_arn.startswith("arn:") and subscription_arn not in {
        "PendingConfirmation",
        "Deleted",
    }
    return result("Subscription Status = Confirmed", is_confirmed)


def _get_messages_published_count():
    try:
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=1)
        response = cloudwatch.get_metric_statistics(
            Namespace="AWS/SNS",
            MetricName="NumberOfMessagesPublished",
            Dimensions=[{"Name": "TopicName", "Value": TOPIC_NAME}],
            StartTime=start_time,
            EndTime=end_time,
            Period=86400,
            Statistics=["Sum"],
        )
        datapoints = response.get("Datapoints", [])
        return sum(dp.get("Sum", 0) for dp in datapoints)
    except Exception:
        return None


def test_message_published():
    deadline = time.monotonic() + METRIC_RETRY_TIMEOUT_SECONDS
    while True:
        count = _get_messages_published_count()
        if count is not None and count >= 1:
            return result("At least one message published to the SNS topic", True)
        if time.monotonic() >= deadline:
            return result("At least one message published to the SNS topic", False)
        time.sleep(METRIC_RETRY_INTERVAL_SECONDS)


def _get_messages_delivered_count(queue_name):
    try:
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=1)
        response = cloudwatch.get_metric_statistics(
            Namespace="AWS/SQS",
            MetricName="NumberOfMessagesSent",
            Dimensions=[{"Name": "QueueName", "Value": queue_name}],
            StartTime=start_time,
            EndTime=end_time,
            Period=86400,
            Statistics=["Sum"],
        )
        datapoints = response.get("Datapoints", [])
        return sum(dp.get("Sum", 0) for dp in datapoints)
    except Exception:
        return None


def test_message_delivered():
    deadline = time.monotonic() + METRIC_RETRY_TIMEOUT_SECONDS
    while True:
        count = _get_messages_delivered_count(QUEUE_NAME)
        if count is not None and count >= 1:
            return result("At least one message delivered to the SQS queue", True)
        if time.monotonic() >= deadline:
            return result("At least one message delivered to the SQS queue", False)
        time.sleep(METRIC_RETRY_INTERVAL_SECONDS)


def run_test_cases(credentials):
    global sns, sqs, cloudwatch
    sns = boto3.client(
        "sns",
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )
    sqs = boto3.client(
        "sqs",
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )
    cloudwatch = boto3.client(
        "cloudwatch",
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )

    print("=" * 60)
    print("LAB 5.3 VALIDATION: Configure an SNS Subscription to Deliver Messages to an SQS Queue")
    print("=" * 60)

    topic_arn = _find_topic_arn()
    topic_exists = test_topic_exists(topic_arn)

    if not topic_exists:
        skip(f"Topic Name = '{TOPIC_NAME}'")
        skip(f"SQS queue '{QUEUE_NAME}' exists")
        skip(f"Queue Name = '{QUEUE_NAME}'")
        skip("Queue Type = Standard")
        skip("SNS Subscription to the SQS queue exists")
        skip("Subscription Protocol = SQS")
        skip("Subscription Status = Confirmed")
        skip("At least one message published to the SNS topic")
        skip("At least one message delivered to the SQS queue")
        print("=" * 60)
        return

    test_topic_name(topic_arn)

    queue_url = _find_queue_url()
    queue_exists = test_queue_exists(queue_url)

    if not queue_exists:
        skip(f"Queue Name = '{QUEUE_NAME}'")
        skip("Queue Type = Standard")
        skip("SNS Subscription to the SQS queue exists")
        skip("Subscription Protocol = SQS")
        skip("Subscription Status = Confirmed")
        test_message_published()
        skip("At least one message delivered to the SQS queue")
        print("=" * 60)
        return

    test_queue_name(queue_url)
    test_queue_type(queue_url)

    queue_attributes = _get_queue_attributes(queue_url)

    queue_arn = (queue_attributes or {}).get("QueueArn", "")
    subscriptions = _get_subscriptions(topic_arn)
    subscription = _find_sqs_subscription(subscriptions or [], queue_arn) if subscriptions is not None else None
    subscription_exists = test_subscription_exists(subscription)

    if not subscription_exists:
        skip("Subscription Protocol = SQS")
        skip("Subscription Status = Confirmed")
        test_message_published()
        skip("At least one message delivered to the SQS queue")
        print("=" * 60)
        return

    test_subscription_protocol(subscription)
    test_subscription_status(subscription)
    test_message_published()
    test_message_delivered()

    print("=" * 60)
