import time

import boto3
from datetime import datetime, timedelta, timezone


sns = ""
cloudwatch = ""

TOPIC_NAME = "lab-5-2-topic"

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


def _get_topic_attributes(topic_arn):
    try:
        return sns.get_topic_attributes(TopicArn=topic_arn).get("Attributes", {})
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


def test_topic_exists(topic_arn):
    return result(f"SNS topic '{TOPIC_NAME}' exists", topic_arn is not None)


def test_topic_name(topic_arn):
    return result(f"Topic Name = '{TOPIC_NAME}'", _topic_name(topic_arn) == TOPIC_NAME)


def test_topic_type(attributes):
    is_standard = attributes is not None and attributes.get("FifoTopic", "false").lower() != "true"
    return result("Topic Type = Standard", is_standard)


def test_subscription_count(subscriptions):
    return result("Exactly one subscription exists", subscriptions is not None and len(subscriptions) == 1)


def test_subscription_protocol(subscription):
    return result("Subscription Protocol = Email", subscription.get("Protocol", "").lower() == "email")


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
            return result("At least one message published to the topic", True)
        if time.monotonic() >= deadline:
            return result("At least one message published to the topic", False)
        time.sleep(METRIC_RETRY_INTERVAL_SECONDS)


def run_test_cases(credentials):
    global sns, cloudwatch
    sns = boto3.client(
        "sns",
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
    print("LAB 5.2 VALIDATION: Create an Amazon SNS Topic")
    print("=" * 60)

    topic_arn = _find_topic_arn()
    topic_exists = test_topic_exists(topic_arn)

    if not topic_exists:
        skip(f"Topic Name = '{TOPIC_NAME}'")
        skip("Topic Type = Standard")
        skip("Exactly one subscription exists")
        skip("Subscription Protocol = Email")
        skip("Subscription Status = Confirmed")
        skip("At least one message published to the topic")
        print("=" * 60)
        return

    test_topic_name(topic_arn)
    attributes = _get_topic_attributes(topic_arn)
    test_topic_type(attributes)

    subscriptions = _get_subscriptions(topic_arn)
    subscription_count_valid = test_subscription_count(subscriptions)
    if not subscription_count_valid:
        skip("Subscription Protocol = Email")
        skip("Subscription Status = Confirmed")
        test_message_published()
        print("=" * 60)
        return

    subscription = subscriptions[0]
    test_subscription_protocol(subscription)
    test_subscription_status(subscription)
    test_message_published()

    print("=" * 60)
