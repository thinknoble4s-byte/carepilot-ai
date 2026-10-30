"""CarePilot demo stack (Increment 0, demo profile: security deferred).

Public S3 website + Lambda Function URL + public RDS PostgreSQL.
The database is off by default: the owner starts it with scripts/db.sh for a
demo or tests; an idle check every 30 minutes and a nightly stop switch it off.
"""
import os
from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    SecretValue,
    Stack,
    aws_budgets as budgets,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_rds as rds,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_scheduler as scheduler,
)
from constructs import Construct

ROOT = Path(__file__).resolve().parents[2]
TZ = "Asia/Kolkata"


class CarePilotStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        ctx = self.node.try_get_context
        db_password = os.environ.get("CAREPILOT_DB_PASSWORD", "")
        if len(db_password) < 16:
            raise ValueError("Set CAREPILOT_DB_PASSWORD (16+ random characters) in .env before synth/deploy")

        # ---------- Network: public subnets only, no NAT (no cost) ----------
        vpc = ec2.Vpc(
            self, "Vpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[ec2.SubnetConfiguration(name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24)],
        )
        db_sg = ec2.SecurityGroup(self, "DbSg", vpc=vpc, description="Demo profile: Postgres open to the internet (synthetic data only)")
        db_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(5432), "Postgres from anywhere (demo profile)")

        # ---------- Database ----------
        db = rds.DatabaseInstance(
            self, "Db",
            engine=rds.DatabaseInstanceEngine.postgres(version=rds.PostgresEngineVersion.VER_16),
            instance_type=ec2.InstanceType.of(ec2.InstanceClass.BURSTABLE4_GRAVITON, ec2.InstanceSize.MICRO),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            publicly_accessible=True,
            security_groups=[db_sg],
            credentials=rds.Credentials.from_password("carepilot", SecretValue.unsafe_plain_text(db_password)),
            database_name="carepilot",
            allocated_storage=20,
            storage_type=rds.StorageType.GP3,
            storage_encrypted=True,  # AWS-managed key: no cost
            multi_az=False,
            backup_retention=Duration.days(1),
            delete_automated_backups=True,
            deletion_protection=False,
            enable_performance_insights=False,
            removal_policy=RemovalPolicy.SNAPSHOT,
        )

        # ---------- API Lambda (outside any VPC) ----------
        backend_build = ROOT / "backend" / ".build"
        if not backend_build.exists():
            raise ValueError("backend/.build is missing: run `python backend/build.py` first")

        api_fn = lambda_.Function(
            self, "ApiFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.handler",
            code=lambda_.Code.from_asset(str(backend_build)),
            memory_size=512,
            timeout=Duration.seconds(20),
            environment={
                "APP_ENV": ctx("appEnv") or "demo",
                "DB_HOST": db.db_instance_endpoint_address,
                "DB_PORT": db.db_instance_endpoint_port,
                "DB_NAME": "carepilot",
                "DB_USER": "carepilot",
                "DB_PASSWORD": db_password,
                "DB_INSTANCE_ID": db.instance_identifier,
                # Server-side only; never shipped to the browser.
                "SARVAM_API_KEY": os.environ.get("SARVAM_API_KEY", ""),
            },
            log_group=logs.LogGroup(self, "ApiLogs", retention=logs.RetentionDays.TWO_WEEKS, removal_policy=RemovalPolicy.DESTROY),
        )
        api_fn.add_to_role_policy(iam.PolicyStatement(
            # Read the status, and start it from the "Start database" button. Stopping stays with the idle/nightly stop.
            actions=["rds:DescribeDBInstances", "rds:StartDBInstance"],
            resources=[db.instance_arn],
        ))
        api_url = api_fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
            cors=lambda_.FunctionUrlCorsOptions(
                allowed_origins=["*"],
                allowed_methods=[lambda_.HttpMethod.ALL],
                allowed_headers=["*"],
            ),
        )

        # ---------- Stop the database when it is not used ----------
        idle_minutes = int(ctx("dbIdleMinutes") or 60)
        idle_fn = lambda_.Function(
            self, "DbIdleStopFn",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="index.handler",
            code=lambda_.Code.from_asset(str(ROOT / "infra" / "lambdas" / "db_idle_stop")),
            timeout=Duration.seconds(30),
            environment={
                "DB_INSTANCE_ID": db.instance_identifier,
                "API_FUNCTION_NAME": api_fn.function_name,
                "IDLE_MINUTES": str(idle_minutes),
            },
            log_group=logs.LogGroup(self, "DbIdleStopLogs", retention=logs.RetentionDays.TWO_WEEKS, removal_policy=RemovalPolicy.DESTROY),
        )
        idle_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["rds:DescribeDBInstances", "rds:StopDBInstance"], resources=[db.instance_arn]))
        idle_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["cloudwatch:GetMetricStatistics"], resources=["*"]))

        sched_role = iam.Role(self, "SchedulerRole", assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"))
        sched_role.add_to_policy(iam.PolicyStatement(
            actions=["rds:StartDBInstance", "rds:StopDBInstance"], resources=[db.instance_arn]))
        idle_fn.grant_invoke(sched_role)

        db_input = self.to_json_string({"DbInstanceIdentifier": db.instance_identifier})
        off = scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF")

        scheduler.CfnSchedule(
            self, "DbIdleCheck",
            description="Stop the database after IDLE_MINUTES without API calls",
            schedule_expression="rate(30 minutes)",
            flexible_time_window=off,
            target=scheduler.CfnSchedule.TargetProperty(arn=idle_fn.function_arn, role_arn=sched_role.role_arn),
        )
        scheduler.CfnSchedule(
            self, "DbNightlyStop",
            description="Safety net: stop the database every night",
            schedule_expression=ctx("dbStopCron") or "cron(0 21 * * ? *)",
            schedule_expression_timezone=TZ,
            flexible_time_window=off,
            target=scheduler.CfnSchedule.TargetProperty(
                arn="arn:aws:scheduler:::aws-sdk:rds:stopDBInstance",
                role_arn=sched_role.role_arn,
                input=db_input,
            ),
        )
        # Optional weekday auto-start (off by default: start on demand instead).
        auto_start = str(ctx("dbAutoStart") or os.environ.get("CAREPILOT_DB_AUTOSTART", "false")).lower() == "true"
        scheduler.CfnSchedule(
            self, "DbWeekdayStart",
            description="Optional: start the database before the 07:00 gap check",
            schedule_expression=ctx("dbStartCron") or "cron(30 6 ? * MON-FRI *)",
            schedule_expression_timezone=TZ,
            flexible_time_window=off,
            state="ENABLED" if auto_start else "DISABLED",
            target=scheduler.CfnSchedule.TargetProperty(
                arn="arn:aws:scheduler:::aws-sdk:rds:startDBInstance",
                role_arn=sched_role.role_arn,
                input=db_input,
            ),
        )

        # ---------- Public website (S3 static website hosting) ----------
        frontend_dist = ROOT / "frontend" / "dist"
        if not frontend_dist.exists():
            raise ValueError("frontend/dist is missing: run `npm run build` in frontend/ first")

        site = s3.Bucket(
            self, "Site",
            website_index_document="index.html",
            website_error_document="index.html",
            public_read_access=True,
            block_public_access=s3.BlockPublicAccess(
                block_public_acls=True, ignore_public_acls=True,
                block_public_policy=False, restrict_public_buckets=False,
            ),
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )
        s3deploy.BucketDeployment(
            self, "SiteDeploy",
            destination_bucket=site,
            sources=[
                s3deploy.Source.asset(str(frontend_dist)),
                s3deploy.Source.json_data("config.json", {"apiUrl": api_url.url, "env": ctx("appEnv") or "demo"}),
            ],
        )

        # ---------- Budget alert ----------
        email = os.environ.get("BUDGET_EMAIL", "")
        if email:
            budgets.CfnBudget(
                self, "Budget",
                budget=budgets.CfnBudget.BudgetDataProperty(
                    budget_type="COST",
                    time_unit="MONTHLY",
                    budget_limit=budgets.CfnBudget.SpendProperty(amount=float(ctx("budgetUsd") or 30), unit="USD"),
                ),
                notifications_with_subscribers=[
                    budgets.CfnBudget.NotificationWithSubscribersProperty(
                        notification=budgets.CfnBudget.NotificationProperty(
                            notification_type=kind, comparison_operator="GREATER_THAN", threshold=pct, threshold_type="PERCENTAGE"),
                        subscribers=[budgets.CfnBudget.SubscriberProperty(subscription_type="EMAIL", address=email)],
                    )
                    for kind, pct in (("ACTUAL", 80), ("FORECASTED", 100))
                ],
            )

        CfnOutput(self, "WebsiteUrl", value=site.bucket_website_url)
        CfnOutput(self, "ApiUrl", value=api_url.url)
        CfnOutput(self, "DbHost", value=db.db_instance_endpoint_address)
        CfnOutput(self, "DbInstanceId", value=db.instance_identifier)
