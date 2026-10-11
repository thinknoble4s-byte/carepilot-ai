#!/usr/bin/env bash
# Build the backend bundle and the frontend, then deploy the demo stack.
# First time only: npx aws-cdk@2 bootstrap aws://<account>/ap-south-1
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] || { echo "Create .env from .env.example first" >&2; exit 1; }

python backend/build.py
(cd frontend && npm ci && npm run build)
(cd infra && python -m pip install -q -r requirements.txt && npx --yes aws-cdk@2 deploy --require-approval never --outputs-file outputs.json)

python - <<'PY'
import json
out = next(iter(json.load(open("infra/outputs.json")).values()))
print("\nDemo URL :", out["WebsiteUrl"])
print("API URL  :", out["ApiUrl"])
PY
echo "Next: scripts/db.sh status, then python db/migrate.py once it is 'available'."
