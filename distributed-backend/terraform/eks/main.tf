module "tags" {
  source = "../lib/tags"

  environment_name = var.environment_name
}

module "vpc" {
  source = "../lib/vpc"

  environment_name = var.environment_name

  public_subnet_tags = {
    "kubernetes.io/cluster/${var.environment_name}" = "shared"
    "kubernetes.io/role/elb"                        = "1"
  }

  private_subnet_tags = {
    "kubernetes.io/cluster/${var.environment_name}" = "shared"
    "kubernetes.io/role/internal-elb"               = "1"
  }

  tags = module.tags.result
}

module "_app_eks" {
  source = "../lib/eks"

  providers = {
    kubernetes.cluster = kubernetes.cluster
    kubernetes.addons  = kubernetes
    helm.addons        = helm
    kubectl.addons     = kubectl
  }

  environment_name                = var.environment_name
  cluster_version                 = var.cluster_version
  cluster_endpoint_public_access  = var.cluster_endpoint_public_access
  vpc_id                          = module.vpc.inner.vpc_id
  vpc_cidr                        = module.vpc.inner.vpc_cidr_block
  subnet_ids                      = module.vpc.inner.private_subnets
  opentelemetry_enabled           = var.opentelemetry_enabled
  tags                            = module.tags.result

}
