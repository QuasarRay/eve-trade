output "images" {
  description = "Image repository, tag, and full image reference keyed by service name."
  value       = local.images
}

output "repository_urls" {
  description = "ECR repository URLs keyed by service name."
  value       = { for name, repository in aws_ecr_repository.service : name => repository.repository_url }
}

output "repository_arns" {
  description = "ECR repository ARNs keyed by service name."
  value       = { for name, repository in aws_ecr_repository.service : name => repository.arn }
}
