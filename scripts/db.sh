#!/usr/bin/env bash
# Start, stop or check the demo database. Usage: scripts/db.sh start|stop|status
# The database also stops by itself after 60 idle minutes and every night at 21:00 IST.
set -euo pipefail
cd "$(dirname "$0")/.."

region="${AWS_REGION:-ap-south-1}"
id=$(python -c "import json;print(next(iter(json.load(open('infra/outputs.json')).values()))['DbInstanceId'])")

case "${1:-status}" in
  start)  aws rds start-db-instance --region "$region" --db-instance-identifier "$id" --query 'DBInstance.DBInstanceStatus' --output text
          echo "Starting; ready in about 5 minutes (scripts/db.sh status)." ;;
  stop)   aws rds stop-db-instance --region "$region" --db-instance-identifier "$id" --query 'DBInstance.DBInstanceStatus' --output text ;;
  status) aws rds describe-db-instances --region "$region" --db-instance-identifier "$id" --query 'DBInstances[0].DBInstanceStatus' --output text ;;
  *)      echo "usage: scripts/db.sh start|stop|status" >&2; exit 2 ;;
esac
