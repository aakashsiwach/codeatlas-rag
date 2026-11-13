data "aws_caller_identity" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  azs        = slice(data.aws_availability_zones.available.names, 0, 2)
  deployed   = var.image_tag != null

  api_token_parameter_arn = "arn:aws:ssm:${var.region}:${local.account_id}:parameter/${trimprefix(var.api_token_parameter, "/")}"

  # The container's configuration: Bedrock, with the frozen `fast` setup as the default. No secrets here;
  # the API token is injected from SSM (ecs.tf).
  environment = merge(
    {
      CODEATLAS_LLM_PROVIDER = "bedrock"
      CODEATLAS_LLM_MODEL    = var.bedrock_model_id
      CODEATLAS_AWS_REGION   = var.region
      CODEATLAS_DEFAULT_MODE = "vector"
    },
    length(var.llm_prices) > 0 ? { CODEATLAS_LLM_PRICES = jsonencode(var.llm_prices) } : {},
  )
}
