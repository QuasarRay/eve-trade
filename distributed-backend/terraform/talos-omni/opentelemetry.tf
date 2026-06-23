resource "kubectl_manifest" "otel_instrumentation" {
  count = var.opentelemetry_enabled ? 1 : 0

  yaml_body = yamlencode({
    apiVersion = "opentelemetry.io/v1alpha1"
    kind       = "Instrumentation"
    metadata = {
      name      = "default-instrumentation"
      namespace = var.opentelemetry_instrumentation_namespace
      labels    = local.labels
    }
    spec = {
      env = [
        {
          name  = "OTEL_SDK_DISABLED"
          value = "false"
        },
        {
          name  = "OTEL_EXPORTER_OTLP_PROTOCOL"
          value = "http/protobuf"
        },
        {
          name  = "OTEL_RESOURCE_PROVIDERS_AWS_ENABLED"
          value = "false"
        },
        {
          name  = "OTEL_RESOURCE_PROVIDERS_GCP_ENABLED"
          value = "false"
        },
        {
          name  = "OTEL_RESOURCE_ATTRIBUTES"
          value = "deployment.platform=talos-omni"
        },
        {
          name  = "OTEL_METRICS_EXPORTER"
          value = "none"
        },
        {
          name  = "OTEL_JAVA_GLOBAL_AUTOCONFIGURE_ENABLED"
          value = "true"
        }
      ]
      exporter = {
        endpoint = var.opentelemetry_otlp_endpoint
      }
      propagators = [
        "tracecontext",
        "baggage",
      ]
      sampler = {
        type = "always_on"
      }
    }
  })
}

locals {
  opentelemetry_instrumentation = var.opentelemetry_enabled ? "${var.opentelemetry_instrumentation_namespace}/${yamldecode(kubectl_manifest.otel_instrumentation[0].yaml_body_parsed).metadata.name}" : ""
}
