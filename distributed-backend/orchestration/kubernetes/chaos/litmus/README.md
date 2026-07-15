# Litmus Chaos Pipelines

The production suite renders stopped `pod-delete` engines for:

* `eve-trade-encore-backend`
* `eve-trade-trade-settlement`
* `eve-trade-nsqd`

The Encore backend probe hits `http://encore-backend.eve-trade.svc.cluster.local:4000/gateway/readyz`. The NSQ probe hits `http://nsqd.eve-trade.svc.cluster.local:4151/ping`.

Render locally with:

```powershell
kubectl kustomize distributed-backend\orchestration\kubernetes\chaos\litmus\overlays\prod
```
