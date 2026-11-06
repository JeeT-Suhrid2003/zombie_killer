import os
from datetime import datetime, timedelta
import boto3

# Initialize boto3 clients
ec2 = boto3.client("ec2")
cloudwatch = boto3.client("cloudwatch")

# Configuration via Environment Variables
CPU_THRESHOLD = float(
    os.environ.get("CPU_THRESHOLD", "5.0")
)  # CPU % threshold
LOOKBACK_DAYS = int(
    os.environ.get("LOOKBACK_DAYS", "7")
)  # Days to evaluate


def lambda_handler(event, context):
  print(
      f"Starting zombie server check. Threshold: {CPU_THRESHOLD}% over"
      f" {LOOKBACK_DAYS} days."
  )

  # 1. Find all running EC2 instances
  response = ec2.describe_instances(
      Filters=[
          {"Name": "instance-state-name", "Values": ["running"]},
          {
              "Name": "tag:Environment",
              "Values": ["dev", "test", "staging"],
          },  # Optional: Limit to non-prod tags to be safe!
      ]
  )

  running_instances = []
  for reservation in response["Reservations"]:
    for instance in reservation["Instances"]:
      running_instances.append(instance["InstanceId"])

  if not running_instances:
    print("No running instances found matching the filters.")
    return {"statusCode": 200, "body": "No instances to check."}

  stopped_instances = []
  end_time = datetime.utcnow()
  start_time = end_time - timedelta(days=LOOKBACK_DAYS)

  for instance_id in running_instances:
    # 2. Fetch CPU Utilization metric from CloudWatch
    cw_response = cloudwatch.get_metric_data(
        MetricDataQueries=[
            {
                "Id": "m1",
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/EC2",
                        "MetricName": "CPUUtilization",
                        "Dimensions": [
                            {"Name": "InstanceId", "Value": instance_id}
                        ],
                    },
                    "Period": 86400,  # 1-day granularity
                    "Stat": "Average",
                },
                "ReturnData": True,
            }
        ],
        StartTime=start_time,
        EndTime=end_time,
    )

    datapoints = cw_response["MetricDataResults"][0]["Values"]

    # If there's no data or not enough data points, skip to avoid false positives
    if len(datapoints) < LOOKBACK_DAYS:
      print(
          f"Instance {instance_id}: Insufficient CloudWatch data ({len(datapoints)}"
          " days found). Skipping."
      )
      continue

    # 3. Check if average CPU is consistently below threshold
    avg_cpu = sum(datapoints) / len(datapoints)
    print(
        f"Instance {instance_id}: Average CPU over last {LOOKBACK_DAYS} days is"
        f" {avg_cpu:.2f}%"
    )

    if avg_cpu < CPU_THRESHOLD:
      print(
          f"-> Stopping instance {instance_id} due to low utilization"
          f" ({avg_cpu:.2f}% < {CPU_THRESHOLD}%)"
      )
      ec2.stop_instances(InstanceIds=[instance_id])
      stopped_instances.append(instance_id)

  return {
      "statusCode": 200,
      "body": f"Successfully stopped zombie instances: {stopped_instances}",
  }