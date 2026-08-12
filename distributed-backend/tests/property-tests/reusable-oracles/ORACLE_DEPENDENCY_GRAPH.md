# Reusable oracle dependency graph

```mermaid
flowchart LR
    S["Immutable tests-to-implement leaves"] --> Q["catalog_quality"]
    C["classification.json"] --> Q
    E["Real run-evidence.json"] --> D["deployment_evidence"]
    Q --> T["Explicit catalog-governance business tests"]
    D --> T
```

`catalog_quality` receives catalog records and bytes from the explicit test setup; it does not load or implement infrastructure. `deployment_evidence` independently rejects empty, controller-only, or control-plane-only scenario evidence before a business oracle is evaluated.
