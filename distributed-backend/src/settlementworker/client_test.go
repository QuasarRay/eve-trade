package settlementworker

import (
	"context"
	"testing"

	"google.golang.org/grpc"
	healthv1 "google.golang.org/grpc/health/grpc_health_v1"
)

type recordingHealthChecker struct {
	service string
	status  healthv1.HealthCheckResponse_ServingStatus
}

func (c *recordingHealthChecker) Check(_ context.Context, request *healthv1.HealthCheckRequest, _ ...grpc.CallOption) (*healthv1.HealthCheckResponse, error) {
	c.service = request.GetService()
	return &healthv1.HealthCheckResponse{Status: c.status}, nil
}

func TestSettlementExecutorPingUsesReadinessHealthService(t *testing.T) {
	health := &recordingHealthChecker{status: healthv1.HealthCheckResponse_SERVING}
	executor := &GRPCSettlementExecutor{health: health}

	if err := executor.Ping(context.Background()); err != nil {
		t.Fatalf("Ping returned error: %v", err)
	}
	if health.service != "readiness" {
		t.Fatalf("health service = %q, want readiness", health.service)
	}
}

func TestSettlementExecutorPingRejectsNonServingStatus(t *testing.T) {
	executor := &GRPCSettlementExecutor{health: &recordingHealthChecker{status: healthv1.HealthCheckResponse_NOT_SERVING}}
	if err := executor.Ping(context.Background()); err == nil {
		t.Fatal("Ping accepted NOT_SERVING readiness")
	}
}
