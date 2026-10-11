"""Stop the CarePilot database when the API has had no calls for IDLE_MINUTES.

Runs every 30 minutes from EventBridge Scheduler. Also catches the automatic
restart AWS performs after an instance has been stopped for 7 days.
"""
import datetime
import os

import boto3

rds = boto3.client("rds")
cloudwatch = boto3.client("cloudwatch")


def handler(event, context):
    db_id = os.environ["DB_INSTANCE_ID"]
    fn_name = os.environ["API_FUNCTION_NAME"]
    idle_minutes = int(os.environ.get("IDLE_MINUTES", "60"))

    status = rds.describe_db_instances(DBInstanceIdentifier=db_id)["DBInstances"][0]["DBInstanceStatus"]
    if status != "available":
        return {"action": "none", "status": status}

    now = datetime.datetime.now(datetime.timezone.utc)
    stats = cloudwatch.get_metric_statistics(
        Namespace="AWS/Lambda",
        MetricName="Invocations",
        Dimensions=[{"Name": "FunctionName", "Value": fn_name}],
        StartTime=now - datetime.timedelta(minutes=idle_minutes),
        EndTime=now,
        Period=300,
        Statistics=["Sum"],
    )
    calls = sum(point["Sum"] for point in stats["Datapoints"])
    if calls > 0:
        return {"action": "none", "status": status, "recentApiCalls": calls}

    rds.stop_db_instance(DBInstanceIdentifier=db_id)
    print(f"Stopped {db_id}: no API calls in the last {idle_minutes} minutes")
    return {"action": "stopped", "status": status}
