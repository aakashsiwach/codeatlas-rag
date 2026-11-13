data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:ecs:${var.region}:${local.account_id}:*"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

# Execution role: used by ECS (not the app) to pull the image, write logs and read the API token at start-up.
resource "aws_iam_role" "execution" {
  name               = "${var.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "read_api_token" {
  statement {
    actions   = ["ssm:GetParameters"]
    resources = [local.api_token_parameter_arn]
  }
  # The parameter uses the AWS-managed aws/ssm key, whose key policy already allows decryption through
  # SSM. Add kms:Decrypt here only if you switch it to a customer-managed key.
}

resource "aws_iam_role_policy" "read_api_token" {
  name   = "read-api-token"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.read_api_token.json
}

# Task role: what the application itself may do. Only Bedrock invocation (Converse and ConverseStream use
# these two actions), of one inference profile and the foundation model behind it. A global inference
# profile can route to any region, so the model ARNs cover every region and the region-less form.
data "aws_iam_policy_document" "bedrock_invoke" {
  statement {
    actions = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = [
      "arn:aws:bedrock:${var.region}:${local.account_id}:inference-profile/${var.bedrock_model_id}",
      "arn:aws:bedrock:*::foundation-model/${var.bedrock_foundation_model_id}",
      "arn:aws:bedrock:::foundation-model/${var.bedrock_foundation_model_id}",
    ]
  }
}

resource "aws_iam_role" "task" {
  name               = "${var.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy" "bedrock_invoke" {
  name   = "bedrock-invoke"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.bedrock_invoke.json
}
