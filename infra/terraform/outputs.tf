output "url" {
  description = "HTTPS URL of the web UI and the API."
  value       = "https://${aws_cloudfront_distribution.api.domain_name}"
}

output "ecr_repository_url" {
  description = "Push the deploy image here."
  value       = aws_ecr_repository.api.repository_url
}

output "cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "service_name" {
  value = try(aws_ecs_service.api[0].name, null)
}

output "log_group" {
  value = aws_cloudwatch_log_group.api.name
}
