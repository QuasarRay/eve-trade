locals {
  service_names = toset([
    "encore-backend",
    "trade-settlement",
    "quilkin",
  ])
}

resource "aws_ecr_repository" "service" {
  for_each = local.service_names

  name                 = "${var.environment_name}/${each.key}"
  image_tag_mutability = "IMMUTABLE"

  encryption_configuration {
    encryption_type = "AES256"
  }

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = merge(var.tags, {
    "app.kubernetes.io/name" = each.key
  })
}

resource "aws_ecr_lifecycle_policy" "service" {
  for_each = aws_ecr_repository.service

  repository = each.value.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after 14 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 14
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

locals {
  images = {
    for name in local.service_names : name => {
      repository = coalesce(try(var.container_image_overrides[name].repository, null), aws_ecr_repository.service[name].repository_url)
      tag        = coalesce(try(var.container_image_overrides[name].tag, null), var.default_image_tag)
      image      = "${coalesce(try(var.container_image_overrides[name].repository, null), aws_ecr_repository.service[name].repository_url)}:${coalesce(try(var.container_image_overrides[name].tag, null), var.default_image_tag)}"
    }
  }
}
