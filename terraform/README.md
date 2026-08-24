# AWS deployment (Terraform)

Provisions: a 2-AZ VPC (single NAT), RDS Postgres 16 (pgvector-capable),
single-node ElastiCache Redis, an ECS Fargate cluster running `api` (×2),
`worker` (×1) and `web` (×1), an ALB routing `/v1/*` + health probes to the
API and everything else to the web UI, two ECR repos, four Secrets Manager
secrets (containers only — values never touch Terraform state), and
CloudWatch log groups.

**Cost honesty:** the always-on drivers are the NAT gateway, ALB, RDS
`db.t4g.small`, ElastiCache `t4g.micro`, and ~2.25 vCPU of Fargate — roughly
$120–160/month at defaults as of this writing. That's an estimate, not a
promise; run your numbers through the AWS calculator, and `terraform destroy`
cleans it all up (ECR `force_delete` and secret `recovery_window=0` are set
demo-friendly on purpose).

**Verification honesty:** this configuration is formatted and
syntax-validated, and CI runs `terraform validate` against real provider
schemas — but it has **not been applied to a live AWS account** as part of
this repo's development. Expect the first `terraform plan` to be a real
review, not a rubber stamp.

## 1. Apply

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars   # edit
terraform init        # add the S3 backend (versions.tf) first if you want remote state
terraform plan
terraform apply
```

## 2. Set secret values (out-of-band, by design)

```bash
# JWT signing key
aws secretsmanager put-secret-value --secret-id rag/jwt-secret \
  --secret-string "$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"

# Anthropic API key
aws secretsmanager put-secret-value --secret-id rag/anthropic-api-key \
  --secret-string "sk-ant-..."

# Database URL, composed from the RDS-managed master secret.
# python does the composing because the password may need percent-encoding:
HOST=$(terraform output -raw rds_endpoint)
PW=$(aws secretsmanager get-secret-value \
  --secret-id "$(terraform output -raw rds_master_secret_arn)" \
  --query SecretString --output text | python3 -c 'import sys,json;print(json.load(sys.stdin)["password"])')
URL=$(python3 -c "from urllib.parse import quote_plus;print(f'postgresql+asyncpg://rag:{quote_plus('''$PW''')}@$HOST/rag')")
aws secretsmanager put-secret-value --secret-id rag/database-url --secret-string "$URL"
```

(`rag/web-ui-credentials` comes in step 5, after a tenant exists.)

## 3. Build and push images

```bash
REGION=$(terraform output -raw url >/dev/null 2>&1; aws configure get region)
APP=$(terraform output -raw ecr_app_repository)
WEB=$(terraform output -raw ecr_web_repository)
aws ecr get-login-password | docker login --username AWS --password-stdin "${APP%%/*}"

docker build -t "$APP:v0.1.0" .            # repo root: api+worker image
docker build -t "$WEB:v0.1.0" ./frontend
docker push "$APP:v0.1.0" && docker push "$WEB:v0.1.0"
```

## 4. Run migrations (one-off task, same image as the API)

```bash
SUBNET=$(terraform output -json private_subnets | python3 -c 'import sys,json;print(json.load(sys.stdin)[0])')
SG=$(terraform output -raw service_security_group_id)
NETCFG="awsvpcConfiguration={subnets=[$SUBNET],securityGroups=[$SG],assignPublicIp=DISABLED}"

aws ecs run-task --cluster rag --launch-type FARGATE \
  --task-definition rag-api \
  --network-configuration "$NETCFG" \
  --overrides '{"containerOverrides":[{"name":"api","command":["alembic","upgrade","head"]}]}'
# then: aws ecs describe-tasks / check the CloudWatch log group /ecs/rag-api
```

## 5. Bootstrap a tenant + credentials (operator plane stays CLI — ADR 0004)

```bash
aws ecs run-task --cluster rag --launch-type FARGATE --task-definition rag-api \
  --network-configuration "$NETCFG" \
  --overrides '{"containerOverrides":[{"name":"api","command":["python","-m","rag_platform.cli","create-tenant","--name","Acme","--slug","acme"]}]}'

aws ecs run-task --cluster rag --launch-type FARGATE --task-definition rag-api \
  --network-configuration "$NETCFG" \
  --overrides '{"containerOverrides":[{"name":"api","command":["python","-m","rag_platform.cli","create-credential","--tenant","acme","--name","web-ui"]}]}'
# the credential JSON is printed ONCE in the task's CloudWatch logs — copy it:
aws secretsmanager put-secret-value --secret-id rag/web-ui-credentials \
  --secret-string '{"client_id":"rag_ci_...","client_secret":"rag_cs_..."}'
aws ecs update-service --cluster rag --service web --force-new-deployment
```

## 6. Smoke test

```bash
URL=$(terraform output -raw url)
curl -fs "$URL/healthz" && curl -fs "$URL/readyz"
open "$URL"   # the web UI; upload a doc, ask about it
```

## Known gaps (deliberate, in scope-order of fixing)

- **HTTP by default** — set `certificate_arn` (ACM) for TLS; without a domain
  this demo runs plaintext and says so.
- **Master DB user is the app user** — least-privilege app role is a follow-up.
- **Prometheus/Grafana don't exist here** — cloud-side you get CloudWatch
  Container Insights + the JSON logs; `/metrics` is deliberately not routed
  through the ALB. Wiring AMP/Grafana Cloud is a follow-up.
- **No autoscaling** — desired counts are static vars; scaling policy
  discussion lives in SCALABILITY.md.
- **Single NAT, single Redis node, optional single-AZ RDS** — stated cost
  choices, not oversights.
- **No CD pipeline** — CI builds and validates; pushing images and
  `terraform apply` are manual by design at this stage.
