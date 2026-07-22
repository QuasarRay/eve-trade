package gateway

import (
	"context"
	"net"
	"runtime"
	"testing"
	"time"

	"github.com/QuasarRay/eve-trade/distributed-backend/internal/testkit"
	"github.com/onsi/gomega"
)

type gatewayLifecycleSupervisor interface {
	Serve(context.Context) error
	Shutdown(context.Context) error
	ListenerErrors() <-chan error
}

func startCanonicalUDPServer(t *testing.T) (*QuilkinUDPServer, net.PacketConn, context.CancelFunc, <-chan error) {
	t.Helper()
	listener, err := net.ListenPacket("udp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("create UDP listener: %v", err)
	}
	server := testUDPServer(&recordingMarketClient{})
	server.listenFunc = func(string, string) (net.PacketConn, error) { return listener, nil }
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- server.ListenAndServe(ctx) }()
	t.Cleanup(func() {
		cancel()
		_ = listener.Close()
		select {
		case <-done:
		default:
		}
	})
	testkit.Expect(t).Eventually(server.Ready).WithTimeout(time.Second).WithPolling(5 * time.Millisecond).Should(gomega.BeTrue())
	return server, listener, cancel, done
}

func TestCanonicalGatewayListenerRegressions(t *testing.T) {
	t.Run("test_gateway_udp_listener_uses_service_owned_context", func(t *testing.T) {
		g := testkit.Expect(t)
		listener, err := net.ListenPacket("udp", "127.0.0.1:0")
		g.Expect(err).NotTo(gomega.HaveOccurred())
		t.Cleanup(func() { _ = listener.Close() })
		server := testUDPServer(&recordingMarketClient{})
		server.listenFunc = func(string, string) (net.PacketConn, error) { return listener, nil }
		service := &Service{server: server}
		supervisor, ok := any(service).(gatewayLifecycleSupervisor)
		g.Expect(ok).To(gomega.BeTrue(), "gateway service exposes no owned listener shutdown/supervision lifecycle")
		if !ok {
			t.FailNow()
		}
		ctx, cancel := context.WithCancel(context.Background())
		done := make(chan error, 1)
		go func() { done <- supervisor.Serve(ctx) }()
		g.Eventually(server.Ready).WithTimeout(time.Second).Should(gomega.BeTrue())
		cancel()
		g.Eventually(done).WithTimeout(time.Second).Should(gomega.Receive(gomega.BeNil()))
	})

	t.Run("test_gateway_udp_listener_stops_when_service_context_is_cancelled", func(t *testing.T) {
		g := testkit.Expect(t)
		_, _, cancel, done := startCanonicalUDPServer(t)
		cancel()
		g.Eventually(done).WithTimeout(time.Second).Should(gomega.Receive(gomega.BeNil()))
	})

	t.Run("test_gateway_udp_listener_closes_socket_during_shutdown", func(t *testing.T) {
		g := testkit.Expect(t)
		_, listener, cancel, done := startCanonicalUDPServer(t)
		cancel()
		g.Eventually(done).WithTimeout(time.Second).Should(gomega.Receive(gomega.BeNil()))
		_, err := listener.WriteTo([]byte("probe"), listener.LocalAddr())
		g.Expect(err).To(gomega.HaveOccurred(), "listener socket remained writable after shutdown")
	})

	t.Run("test_gateway_udp_listener_shutdown_has_bounded_grace_period", func(t *testing.T) {
		g := testkit.Expect(t)
		_, _, cancel, done := startCanonicalUDPServer(t)
		deadline := time.NewTimer(250 * time.Millisecond)
		defer deadline.Stop()
		cancel()
		select {
		case err := <-done:
			g.Expect(err).NotTo(gomega.HaveOccurred())
		case <-deadline.C:
			t.Fatal("gateway UDP listener exceeded its 250ms no-work shutdown budget")
		}
	})

	t.Run("test_gateway_udp_listener_fatal_error_marks_service_unready", func(t *testing.T) {
		g := testkit.Expect(t)
		server, listener, _, done := startCanonicalUDPServer(t)
		g.Expect(listener.Close()).To(gomega.Succeed())
		g.Eventually(done).WithTimeout(time.Second).Should(gomega.Receive(gomega.HaveOccurred()))
		g.Expect(server.Ready()).To(gomega.BeFalse())
		g.Expect(server.Failed()).To(gomega.BeTrue())
	})

	t.Run("test_gateway_udp_listener_fatal_error_is_propagated_to_supervisor", func(t *testing.T) {
		g := testkit.Expect(t)
		service := &Service{server: testUDPServer(&recordingMarketClient{})}
		supervisor, ok := any(service).(gatewayLifecycleSupervisor)
		g.Expect(ok).To(gomega.BeTrue(), "fatal listener errors are logged but cannot reach a service supervisor")
		if !ok {
			t.FailNow()
		}
		listener, err := net.ListenPacket("udp", "127.0.0.1:0")
		g.Expect(err).NotTo(gomega.HaveOccurred())
		service.server.listenFunc = func(string, string) (net.PacketConn, error) { return listener, nil }
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		done := make(chan error, 1)
		go func() { done <- supervisor.Serve(ctx) }()
		g.Eventually(service.server.Ready).WithTimeout(time.Second).Should(gomega.BeTrue())
		g.Expect(listener.Close()).To(gomega.Succeed())
		g.Eventually(supervisor.ListenerErrors()).WithTimeout(time.Second).Should(gomega.Receive(gomega.HaveOccurred()))
		g.Eventually(done).WithTimeout(time.Second).Should(gomega.Receive(gomega.HaveOccurred()))
	})

	t.Run("test_gateway_udp_listener_goroutine_does_not_leak_after_shutdown", func(t *testing.T) {
		g := testkit.Expect(t)
		baseline := runtime.NumGoroutine()
		_, _, cancel, done := startCanonicalUDPServer(t)
		cancel()
		g.Eventually(done).WithTimeout(time.Second).Should(gomega.Receive(gomega.BeNil()))
		g.Eventually(runtime.NumGoroutine).WithTimeout(time.Second).WithPolling(5*time.Millisecond).Should(gomega.BeNumerically("<=", baseline+1), "listener shutdown leaked a goroutine")
	})
}
