package gateway

import (
	"net"
	"testing"
	"time"

	"github.com/QuasarRay/eve-trade/distributed-backend/internal/testkit"
	"github.com/onsi/gomega"
)

type limiterMetricsSource interface {
	metricsSnapshot() limiterMetricsSnapshot
}

func TestCanonicalRateLimiterRegressions(t *testing.T) {
	t.Run("test_rate_limiter_evicts_expired_identity_before_active_identity", func(t *testing.T) {
		g := testkit.Expect(t)
		clock := testkit.NewManualClock(time.Unix(100, 0))
		limiter := newBoundedRemoteRateLimiter(1, 1, 2, time.Second)
		limiter.now = clock.Now
		g.Expect(limiter.allow("expired")).To(gomega.BeTrue())
		clock.Advance(2 * time.Second)
		g.Expect(limiter.allow("active")).To(gomega.BeTrue())
		g.Expect(limiter.allow("new")).To(gomega.BeTrue())
		g.Expect(limiter.allow("active")).To(gomega.BeFalse(), "active identity was evicted instead of the expired identity")
	})

	t.Run("test_rate_limiter_does_not_evict_active_identity_for_new_identity", func(t *testing.T) {
		g := testkit.Expect(t)
		limiter := newBoundedRemoteRateLimiter(1, 1, 1, time.Minute)
		g.Expect(limiter.allow("active")).To(gomega.BeTrue())
		g.Expect(limiter.allow("new")).To(gomega.BeFalse(), "new identity must be rejected while the only slot remains active")
		g.Expect(limiter.allow("active")).To(gomega.BeFalse(), "rejecting a new identity reset the active identity's token budget")
	})

	t.Run("test_rate_limiter_rejects_new_identity_when_capacity_is_full", func(t *testing.T) {
		g := testkit.Expect(t)
		limiter := newBoundedRemoteRateLimiter(1, 1, 1, time.Minute)
		g.Expect(limiter.allow("first")).To(gomega.BeTrue())
		g.Expect(limiter.allow("second")).To(gomega.BeFalse(), "capacity-plus-one admission reset another identity")
		g.Expect(limiter.size()).To(gomega.Equal(1))
	})

	t.Run("test_rate_limiter_identity_churn_does_not_reset_existing_limits", func(t *testing.T) {
		g := testkit.Expect(t)
		limiter := newBoundedRemoteRateLimiter(0.001, 1, 1, time.Hour)
		g.Expect(limiter.allow("legitimate")).To(gomega.BeTrue())
		g.Expect(limiter.allow("churn")).To(gomega.BeFalse())
		g.Expect(limiter.allow("legitimate")).To(gomega.BeFalse(), "identity churn reset the legitimate bucket")
	})

	t.Run("test_rate_limiter_identity_churn_does_not_evict_legitimate_principals", func(t *testing.T) {
		g := testkit.Expect(t)
		limiter := newBoundedRemoteRateLimiter(0.001, 1, 2, time.Hour)
		g.Expect(limiter.allow("principal:1001")).To(gomega.BeTrue())
		g.Expect(limiter.allow("principal:2002")).To(gomega.BeTrue())
		for index := 0; index < 10; index++ {
			g.Expect(limiter.allow(string(rune('a' + index)))).To(gomega.BeFalse())
		}
		g.Expect(limiter.allow("principal:1001")).To(gomega.BeFalse(), "identity churn reset principal 1001's limit")
		g.Expect(limiter.allow("principal:2002")).To(gomega.BeFalse(), "identity churn reset principal 2002's limit")
	})

	t.Run("test_authenticated_principal_limits_are_separate_from_source_limits", func(t *testing.T) {
		g := testkit.Expect(t)
		server := &QuilkinUDPServer{
			rateLimiter:   newBoundedRemoteRateLimiter(0.001, 1, 4, time.Hour),
			sourceLimiter: newBoundedRemoteRateLimiter(0.001, 1, 4, time.Hour),
		}
		remote := &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 4000}
		g.Expect(server.allowSource(remote)).To(gomega.BeTrue())
		g.Expect(server.allowPrincipal(1001, remote)).To(gomega.BeTrue())
		g.Expect(server.allowSource(remote)).To(gomega.BeFalse())
		g.Expect(server.allowPrincipal(1001, remote)).To(gomega.BeFalse())
	})

	t.Run("test_pre_auth_source_churn_cannot_bypass_rate_limit", func(t *testing.T) {
		g := testkit.Expect(t)
		server := &QuilkinUDPServer{sourceLimiter: newBoundedRemoteRateLimiter(0.001, 1, 2, time.Hour)}
		accepted := 0
		for index := 0; index < 10; index++ {
			remote := &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 4000 + index}
			if server.allowSource(remote) {
				accepted++
			}
		}
		g.Expect(accepted).To(gomega.BeNumerically("<=", 2), "pre-auth source churn admitted %d identities into a two-identity budget", accepted)
	})

	t.Run("test_rate_limiter_reports_identity_capacity_rejections", func(t *testing.T) {
		g := testkit.Expect(t)
		limiter := newBoundedRemoteRateLimiter(1, 1, 1, time.Hour)
		g.Expect(limiter.allow("first")).To(gomega.BeTrue())
		g.Expect(limiter.allow("second")).To(gomega.BeFalse())
		metrics, ok := any(limiter).(limiterMetricsSource)
		g.Expect(ok).To(gomega.BeTrue(), "limiter must expose capacity rejection metrics")
		if ok {
			g.Expect(metrics.metricsSnapshot().CapacityRejections).To(gomega.Equal(uint64(1)))
		}
	})

	t.Run("test_rate_limiter_reports_identity_evictions", func(t *testing.T) {
		g := testkit.Expect(t)
		clock := testkit.NewManualClock(time.Unix(100, 0))
		limiter := newBoundedRemoteRateLimiter(1, 1, 1, time.Second)
		limiter.now = clock.Now
		g.Expect(limiter.allow("expired")).To(gomega.BeTrue())
		clock.Advance(2 * time.Second)
		g.Expect(limiter.allow("replacement")).To(gomega.BeTrue())
		metrics, ok := any(limiter).(limiterMetricsSource)
		g.Expect(ok).To(gomega.BeTrue(), "limiter must expose identity eviction metrics")
		if ok {
			g.Expect(metrics.metricsSnapshot().IdentityEvictions).To(gomega.Equal(uint64(1)))
		}
	})
}
