#!/usr/bin/env python3
import os
from pathlib import Path

import aws_cdk as cdk

from carepilot_infra.stack import CarePilotStack


def load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines from the repo .env without overriding real env vars."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv(Path(__file__).resolve().parents[1] / ".env")

app = cdk.App()
stack = CarePilotStack(
    app,
    "CarePilotDemo",
    env=cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region=os.environ.get("AWS_REGION", "ap-south-1")),
    description="CarePilot AI MVP demo (synthetic data, security deferred)",
)
cdk.Tags.of(stack).add("project", "carepilot")
cdk.Tags.of(stack).add("env", app.node.try_get_context("appEnv") or "demo")
app.synth()
