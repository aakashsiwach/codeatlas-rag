variable "name" {
  description = "Name prefix for every resource."
  type        = string
  default     = "codeatlas"
}

variable "region" {
  description = "AWS region for the service and the Bedrock calls."
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "Named AWS CLI profile (e.g. codeatlas). Null uses the default credential chain (AWS_PROFILE etc.)."
  type        = string
  default     = null
}

variable "image_tag" {
  description = "Tag of the image pushed to the ECR repository. Null creates everything except the ECS task and service, so the repository exists before the first push."
  type        = string
  default     = null
}

variable "task_cpu" {
  description = "Fargate CPU units. 1024 (1 vCPU) is enough for the fast setup; the quality setup (cross-encoder reranker) needs 2048 or more to stay inside CloudFront's 60 s origin timeout."
  type        = number
  default     = 1024
}

variable "task_memory" {
  description = "Fargate memory in MiB. The embedding model, the reranker and torch need about 3 GB."
  type        = number
  default     = 4096
}

variable "desired_count" {
  description = "Running tasks. Set to 0 to stop paying for compute while keeping the stack."
  type        = number
  default     = 1
}

variable "bedrock_model_id" {
  description = "Bedrock inference profile id the API calls (CODEATLAS_LLM_MODEL)."
  type        = string
  default     = "global.anthropic.claude-sonnet-5-5"
}

variable "bedrock_foundation_model_id" {
  description = "Foundation model behind the inference profile. The task role may invoke only this model and that profile."
  type        = string
  default     = "anthropic.claude-sonnet-5-5"
}

variable "llm_prices" {
  description = "Optional CODEATLAS_LLM_PRICES, e.g. { \"bedrock:<model id>\" = { input_per_mtok = 0, output_per_mtok = 0 } }. Not secret."
  type        = map(map(number))
  default     = {}
}

variable "api_token_parameter" {
  description = "Name of the SSM SecureString parameter that holds the API token. Created outside Terraform (docs/deploy.md), so the token never enters the state or the repo."
  type        = string
  default     = "/codeatlas/api-token"
}

variable "monthly_budget_usd" {
  description = "Monthly AWS Budget for the whole account; alerts at 80% actual and 100% forecast spend."
  type        = number
  default     = 10
}

variable "budget_alert_email" {
  description = "Email address for the budget alerts. Set it in terraform.tfvars (git-ignored), never in the repo."
  type        = string
}

variable "log_retention_days" {
  description = "CloudWatch log retention for the API container."
  type        = number
  default     = 14
}
