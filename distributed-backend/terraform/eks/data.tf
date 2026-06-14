data "aws_eks_cluster_auth" "this" {
  name = module._app_eks.eks_cluster_id

  depends_on = [
    null_resource.cluster_blocker
  ]
}

data "aws_eks_cluster_auth" "cluster" {
  name = module._app_eks.eks_cluster_id
}
