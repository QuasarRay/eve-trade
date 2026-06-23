# Eve Trade on Talos/Omni

This Terraform root prepares an Omni-managed Talos Kubernetes cluster to run the
same Eve Trade Kubernetes manifests used by AWS/EKS and GCP/GKE.

The root assumes Omni already owns the Talos cluster lifecycle. Create or import
the cluster in Omni, export a kubeconfig with `omnictl`, then use this root to
create provider-neutral runtime prerequisites such as the application namespace
and `trade-settlement-database` secret.

## Example

```powershell
omnictl kubeconfig --cluster eve-trade
terraform -chdir=distributed-backend/terraform/talos-omni init
terraform -chdir=distributed-backend/terraform/talos-omni apply `
  -var kubeconfig_path="$env:USERPROFILE\.kube\config" `
  -var kubeconfig_context="eve-trade" `
  -var database_mode="external" `
  -var external_database_url="postgres://eve_trade:REDACTED@postgres.example.internal:5432/eve_trade" `
  -var image_registry="registry.example.com/eve-trade"
```

For non-production smoke tests, `database_mode=in_cluster` creates a simple
PostgreSQL StatefulSet inside the Eve Trade namespace. Set
`in_cluster_database_password` explicitly when using this mode. Production
Talos/Omni deployments should normally use `database_mode=external` with an
operated PostgreSQL service and backup/restore process.

After Terraform creates prerequisites, render and apply the shared Kubernetes
overlay:

```powershell
python ci-cd\pipeline.py render-kubernetes --cloud-provider talos-omni --registry registry.example.com/eve-trade --tag sha-1234 --output ci-cd\out\kubernetes.yaml
kubectl apply -f ci-cd\out\kubernetes.yaml
```

Talos/Omni does not require a separate Eve Trade workload overlay. Gateway API,
Ingress, load balancer, storage class, cert-manager, and observability operators
remain cluster/platform responsibilities.
