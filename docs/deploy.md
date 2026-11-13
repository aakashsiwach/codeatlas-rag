# Deploying CodeAtlas to AWS

`infra/terraform` runs the API and the web UI as one container on **ECS Fargate**, behind an Application
Load Balancer, with **CloudFront** in front for HTTPS. Answers come from **Amazon Bedrock**. The image has
the PetClinic index and both models baked in, so a task starts without downloading anything.

```
browser ──HTTPS──▶ CloudFront (*.cloudfront.net) ──HTTP──▶ ALB (CloudFront-only) ──▶ Fargate task :8000
                                                                                       │
                                     SSM Parameter Store (API token) ◀── at start-up ──┤
                                     Bedrock InvokeModel(WithResponseStream) ◀─────────┤
                                     CloudWatch Logs /ecs/codeatlas ◀──────────────────┘
```

**Why ECS Fargate rather than App Runner.** App Runner would be simpler (no VPC or load balancer), but it
closed to new customers on 30 April 2026, so a new AWS account cannot use it. Fargate with an ALB is the
standard alternative, and CloudFront adds HTTPS without a domain or certificate. That matters because the
UI sends the API token on every request.

## What it creates

| Resource | Notes |
|---|---|
| ECR repository `codeatlas` | Immutable tags, scan on push, keeps the 5 newest images |
| VPC, 2 public subnets, internet gateway | No NAT gateway (saves ~$33/month). The task's security group admits only the ALB |
| ALB + target group | Port 80, reachable only from CloudFront's origin-facing IP ranges; health check `/health` |
| CloudFront distribution | HTTPS, no caching; forwards every header, including `Authorization` |
| ECS cluster, task definition, service | 1 task, 1 vCPU / 4 GB by default |
| IAM task role | **Only** `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream`, on the inference profile and its foundation model |
| IAM execution role | Pull the image, write logs, read the one SSM parameter |
| CloudWatch log group `/ecs/codeatlas` | 14-day retention |
| AWS Budget | $10/month for the account; email at 80% actual and 100% forecast spend |

The API token is an SSM SecureString created outside Terraform, so it never appears in the Terraform
state, in tfvars or in the repository. The container receives it as `CODEATLAS_API_TOKEN`.

## Prerequisites

- An AWS CLI profile with admin rights for the deploy (the examples use `codeatlas`), Terraform ≥ 1.6,
  Docker, and **Bedrock model access approved** for the model in `bedrock_model_id`.
- Check access before deploying. This makes one 1-token call:
  `CODEATLAS_LLM_PROVIDER=bedrock CODEATLAS_AWS_PROFILE=codeatlas codeatlas doctor --llm`.

## Deploy

The commands below are for bash (macOS/Linux, or Git Bash on Windows). PowerShell equivalents follow each
block. Run them from the repository root unless a step says otherwise.

**1. Create the API token in SSM.** Keep a copy of the token: you will type it into the UI's Settings.

```bash
export AWS_PROFILE=codeatlas AWS_REGION=us-east-1
TOKEN=$(openssl rand -hex 32)
aws ssm put-parameter --name /codeatlas/api-token --type SecureString --value "$TOKEN"
echo "$TOKEN"
```
```powershell
$env:AWS_PROFILE = "codeatlas"; $env:AWS_REGION = "us-east-1"
$token = -join ((1..32) | ForEach-Object { '{0:x2}' -f (Get-Random -Maximum 256) })
aws ssm put-parameter --name /codeatlas/api-token --type SecureString --value $token
$token
```

**2. Create the stack without the service** (no image yet). Start by copying `infra/terraform/terraform.tfvars.example` to
`terraform.tfvars` and setting `budget_alert_email`.

```bash
cd infra/terraform
terraform init
terraform apply            # creates ECR, network, ALB, CloudFront, IAM, logs, budget
```

**3. Build and push the deploy image** (tag it with the commit so tags stay immutable).

```bash
cd ../..
REPO=$(terraform -chdir=infra/terraform output -raw ecr_repository_url)
TAG=$(git rev-parse --short HEAD)
aws ecr get-login-password | docker login --username AWS --password-stdin "${REPO%%/*}"
docker build --target deploy --platform linux/amd64 -t "$REPO:$TAG" .
docker push "$REPO:$TAG"
```
```powershell
$repo = terraform -chdir=infra/terraform output -raw ecr_repository_url
$tag = git rev-parse --short HEAD
aws ecr get-login-password | docker login --username AWS --password-stdin $repo.Split("/")[0]
docker build --target deploy --platform linux/amd64 -t "${repo}:$tag" .
docker push "${repo}:$tag"
```

**4. Start the service** with that image.

```bash
terraform -chdir=infra/terraform apply -var "image_tag=$TAG"
terraform -chdir=infra/terraform output -raw url
```
```powershell
terraform -chdir=infra/terraform apply -var "image_tag=$tag"
terraform -chdir=infra/terraform output -raw url
```

Open the URL. The first start takes 2–3 minutes while the models load, and CloudFront needs a few
minutes to deploy. Then open **Settings** and enter the token from step 1. The default setup is `fast`.

**Logs, stopping and starting.**

```bash
aws logs tail /ecs/codeatlas --follow
terraform -chdir=infra/terraform apply -var "image_tag=$TAG" -var desired_count=0   # stop compute
terraform -chdir=infra/terraform apply -var "image_tag=$TAG" -var desired_count=1   # start again
```

## Demo session: deploy → smoke test → record → destroy

The stack is meant to run only for a demo, not 24/7. Run one session like this:

1. **Deploy.** Follow steps 1–4 above. The billable clock starts at step 2, when the ALB is created.
   Allow 20–30 minutes: building and pushing the image, then CloudFront deploying and the models loading.
2. **Smoke test.** Use the token from step 1.

   ```bash
   URL=$(terraform -chdir=infra/terraform output -raw url)
   curl -s "$URL/health"                                              # {"status":"ok",...}
   curl -s -H "Authorization: Bearer $TOKEN" "$URL/api/config"        # setups, models, thresholds
   curl -sN -X POST "$URL/api/ask/stream" -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"question": "How does searching for owners by last name work?", "setup": "fast"}'
   ```
   ```powershell
   $url = terraform -chdir=infra/terraform output -raw url
   Invoke-RestMethod "$url/health"
   $auth = @{ Authorization = "Bearer $token" }
   Invoke-RestMethod "$url/api/config" -Headers $auth
   $body = @{ question = "How does searching for owners by last name work?"; setup = "fast" } | ConvertTo-Json
   Invoke-RestMethod -Method Post "$url/api/ask" -Headers $auth -ContentType "application/json" -Body $body
   ```

   Then open the URL, enter the token in **Settings**, and ask one `fast` and one `quality` question. A
   quality question streams `progress` events every 5 s while it retrieves, so it does not hit
   CloudFront's 60 s first-byte limit. On 1 vCPU, expect it to take noticeably longer than on a laptop.
3. **Record.** Screen-record the UI as `docs/demo.gif`, showing:
   - a `fast` answer, then opening one of its citations
   - a `quality` question with its progress bar
   - an unanswerable question getting the "don't know" card

   Keep `aws logs tail /ecs/codeatlas` output if anything misbehaves.
4. **Destroy.** Run the [teardown](#teardown) commands, and check that `terraform -chdir=infra/terraform
   state list` is empty.

**Cost of a 3-hour session** (default 1 vCPU / 4 GB, us-east-1 on-demand prices; check them first):

| Item | Basis | ≈ USD |
|---|---|---|
| Fargate | 3 h × ($0.04048 + 4 × $0.004445) | 0.18 |
| ALB | 3 h × $0.0225, plus LCUs (billed per started hour) | 0.08 |
| Public IPv4 addresses | 3 addresses × 3 h × $0.005 | 0.05 |
| CloudFront, SSM, ECR, Logs | free tiers / a few MB | ≈ 0 |
| **Infrastructure** | | **≈ 0.30** |
| Bedrock | about 2.5k input + 0.5k output tokens per answer: at $3 / $15 per million tokens (an assumed price; use your model's), ≈ $0.015 per question, so 50 questions ≈ $0.75 | usage |

A session like this stays far below the $10 budget. The alert only matters if you forget to destroy the
stack: at about $0.10/hour, it fires within roughly four days.

## Teardown

```bash
terraform -chdir=infra/terraform destroy -var "image_tag=$TAG"   # also deletes the images (force_delete)
aws ssm delete-parameter --name /codeatlas/api-token
```

`destroy` also removes the budget. Keep it in place with `terraform state rm aws_budgets_budget.monthly`
before destroying, if you want the alert to stay.

## Estimated monthly cost

This assumes us-east-1 on-demand prices, the stack running 24/7, and the defaults (1 task, 1 vCPU / 4 GB,
x86). Prices change, so check them on the AWS pricing pages before relying on these numbers.

| Item | Basis | ≈ USD / month |
|---|---|---|
| Fargate | 1 vCPU × $0.04048/h + 4 GB × $0.004445/h | 42.50 |
| Application Load Balancer | $0.0225/h + about 1 LCU | 17.00 |
| Public IPv4 addresses | 3 (ALB in 2 AZs, 1 task) × $0.005/h | 11.00 |
| CloudWatch Logs, ECR storage (~2.5 GB) | small volumes | 1.00 |
| CloudFront, SSM standard parameter, Budgets | within free tiers | 0 |
| **Total, infrastructure** | | **≈ 72 (≈ $0.10/hour)** |
| Bedrock | per token, only for answers (see `CODEATLAS_LLM_PRICES`) | usage |

- **This is well above the $10 budget.** Running 24/7, the alert fires within about four days. The
  stack is meant to be brought up for a demo and destroyed afterwards. A 3-hour demo costs about $0.30
  plus Bedrock.
- **With `desired_count=0`**, the ALB and its two IPs still cost about $25/month. `destroy` brings the
  cost to zero.
- **Using `task_cpu=2048`** (recommended for the quality setup) adds about $30/month.

## CPU or GPU: fast vs quality on the deployed service

The deployed default is the **fast** setup (vector search, about 30 ms per query), which runs on any
CPU. The **quality** setup adds a cross-encoder reranker (`gte-reranker-modernbert-base`) over 20
candidates. That took about 25–30 s per query on the development laptop's CPU.

| Option | Quality-setup latency | Cost |
|---|---|---|
| Fargate 1 vCPU (default) | Slower than the laptop, and can exceed CloudFront's **60 s origin timeout** before the first byte. Use fast | ≈ $72/month |
| Fargate 2 vCPU (`task_cpu=2048`) | About laptop speed (~25–30 s): usable for demos, inside the timeout | ≈ $102/month |
| ECS on a GPU instance (e.g. g4dn.xlarge, not in this Terraform) | Well under a second per query | ≈ $380/month on demand, plus an EC2 capacity provider |

Fargate has no GPUs. To make quality interactive, run the reranker on a GPU instance (an ECS capacity
provider, or a separate inference endpoint), or rerank fewer candidates (at the cost of the measured
recall). CloudFront's origin read timeout can also be raised above 60 s through a Service Quotas
request.
