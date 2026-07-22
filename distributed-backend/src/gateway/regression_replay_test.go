package gateway

import (
	"crypto/sha256"
	"testing"
	"time"

	"github.com/QuasarRay/eve-trade/distributed-backend/internal/testkit"
	"github.com/onsi/gomega"
)

type replayMetricsSource interface {
	metricsSnapshot() replayMetricsSnapshot
}

func replayFingerprint(value string) [sha256.Size]byte {
	return sha256.Sum256([]byte(value))
}

func beginForPrincipal(cache *interactionReplayCache, principalID int64, interactionID string, fingerprint [sha256.Size]byte) replayDisposition {
	disposition, _ := cache.beginForPrincipal(principalID, interactionID, fingerprint)
	return disposition
}

func TestCanonicalReplayCacheRegressions(t *testing.T) {
	t.Run("test_replay_cache_keys_entries_by_principal_and_interaction_id", func(t *testing.T) {
		g := testkit.Expect(t)
		cache := newInteractionReplayCache(time.Minute, 4)
		g.Expect(beginForPrincipal(cache, 1001, "shared", replayFingerprint("seller"))).To(gomega.Equal(replayNew))
		g.Expect(beginForPrincipal(cache, 2002, "shared", replayFingerprint("buyer"))).To(gomega.Equal(replayNew), "principal 2002 must own a distinct replay key")
	})

	t.Run("test_same_interaction_id_is_allowed_for_different_principals", func(t *testing.T) {
		g := testkit.Expect(t)
		cache := newInteractionReplayCache(time.Minute, 4)
		g.Expect(beginForPrincipal(cache, 1001, "same-id", replayFingerprint("payload"))).To(gomega.Equal(replayNew))
		g.Expect(beginForPrincipal(cache, 2002, "same-id", replayFingerprint("payload"))).To(gomega.Equal(replayNew), "same interaction ID for a different authenticated principal must be new")
	})

	t.Run("test_same_principal_cannot_reuse_interaction_id_with_different_payload", func(t *testing.T) {
		g := testkit.Expect(t)
		cache := newInteractionReplayCache(time.Minute, 4)
		g.Expect(beginForPrincipal(cache, 1001, "request", replayFingerprint("first"))).To(gomega.Equal(replayNew))
		g.Expect(beginForPrincipal(cache, 1001, "request", replayFingerprint("different"))).To(gomega.Equal(replayConflict))
	})

	t.Run("test_same_principal_can_replay_identical_request_idempotently", func(t *testing.T) {
		g := testkit.Expect(t)
		cache := newInteractionReplayCache(time.Minute, 4)
		fingerprint := replayFingerprint("stable")
		g.Expect(beginForPrincipal(cache, 1001, "request", fingerprint)).To(gomega.Equal(replayNew))
		cache.completeForPrincipal(1001, "request", fingerprint, []byte("response"))
		disposition, response := cache.beginForPrincipal(1001, "request", fingerprint)
		g.Expect(disposition).To(gomega.Equal(replayCached))
		g.Expect(response).To(gomega.Equal([]byte("response")))
	})

	t.Run("test_one_principal_cannot_reserve_another_principals_interaction_id", func(t *testing.T) {
		g := testkit.Expect(t)
		cache := newInteractionReplayCache(time.Minute, 4)
		g.Expect(beginForPrincipal(cache, 1001, "reserved", replayFingerprint("abuse"))).To(gomega.Equal(replayNew))
		g.Expect(beginForPrincipal(cache, 2002, "reserved", replayFingerprint("legitimate"))).To(gomega.Equal(replayNew), "principal 1001 must not reserve principal 2002's namespace")
	})

	t.Run("test_one_principal_cannot_exhaust_global_replay_capacity", func(t *testing.T) {
		g := testkit.Expect(t)
		cache := newInteractionReplayCache(time.Minute, 2)
		g.Expect(beginForPrincipal(cache, 1001, "abuse-1", replayFingerprint("1"))).To(gomega.Equal(replayNew))
		g.Expect(beginForPrincipal(cache, 1001, "abuse-2", replayFingerprint("2"))).To(gomega.Equal(replayOverflow))
		g.Expect(beginForPrincipal(cache, 2002, "legitimate", replayFingerprint("3"))).To(gomega.Equal(replayNew), "an abusive principal consumed all global replay capacity")
	})

	t.Run("test_replay_cache_enforces_per_principal_capacity", func(t *testing.T) {
		g := testkit.Expect(t)
		cache := newInteractionReplayCache(time.Minute, 4)
		g.Expect(beginForPrincipal(cache, 1001, "one", replayFingerprint("1"))).To(gomega.Equal(replayNew))
		g.Expect(beginForPrincipal(cache, 1001, "two", replayFingerprint("2"))).To(gomega.Equal(replayNew))
		g.Expect(beginForPrincipal(cache, 1001, "three", replayFingerprint("3"))).To(gomega.Equal(replayOverflow), "principal 1001 exceeded its two-entry allocation")
	})

	t.Run("test_replay_cache_enforces_global_emergency_capacity", func(t *testing.T) {
		g := testkit.Expect(t)
		cache := newInteractionReplayCache(time.Minute, 2)
		g.Expect(beginForPrincipal(cache, 1001, "one", replayFingerprint("1"))).To(gomega.Equal(replayNew))
		g.Expect(beginForPrincipal(cache, 2002, "two", replayFingerprint("2"))).To(gomega.Equal(replayNew))
		g.Expect(beginForPrincipal(cache, 3003, "three", replayFingerprint("3"))).To(gomega.Equal(replayOverflow))
		g.Expect(cache.size()).To(gomega.Equal(2))
	})

	t.Run("test_replay_cache_reserves_capacity_fairly_across_principals", func(t *testing.T) {
		g := testkit.Expect(t)
		cache := newInteractionReplayCache(time.Minute, 3)
		for index := 0; index < 2; index++ {
			g.Expect(beginForPrincipal(cache, 1001, string(rune('a'+index)), replayFingerprint(string(rune('a'+index))))).To(gomega.Equal(replayNew))
		}
		g.Expect(beginForPrincipal(cache, 1001, "c", replayFingerprint("c"))).To(gomega.Equal(replayOverflow))
		g.Expect(beginForPrincipal(cache, 2002, "fair-share", replayFingerprint("fair"))).To(gomega.Equal(replayNew), "principal 2002 had no reserved or reclaimable capacity")
	})

	t.Run("test_replay_cache_evicts_expired_entries_before_rejecting_new_requests", func(t *testing.T) {
		g := testkit.Expect(t)
		clock := testkit.NewManualClock(time.Unix(100, 0))
		cache := newInteractionReplayCache(time.Second, 1)
		cache.now = clock.Now
		g.Expect(beginForPrincipal(cache, 1001, "expired", replayFingerprint("old"))).To(gomega.Equal(replayNew))
		clock.Advance(2 * time.Second)
		g.Expect(beginForPrincipal(cache, 2002, "fresh", replayFingerprint("new"))).To(gomega.Equal(replayNew))
		g.Expect(cache.size()).To(gomega.Equal(1))
	})

	t.Run("test_replay_cache_limits_in_flight_entries_separately", func(t *testing.T) {
		g := testkit.Expect(t)
		cache := newInteractionReplayCache(time.Minute, 1)
		fingerprint := replayFingerprint("done")
		g.Expect(beginForPrincipal(cache, 1001, "completed", fingerprint)).To(gomega.Equal(replayNew))
		cache.completeForPrincipal(1001, "completed", fingerprint, []byte("ok"))
		g.Expect(beginForPrincipal(cache, 2002, "completed-space", replayFingerprint("result"))).To(gomega.Equal(replayNew), "in-flight capacity incorrectly consumed completed-response capacity")
	})

	t.Run("test_replay_cache_limits_completed_entries_separately", func(t *testing.T) {
		g := testkit.Expect(t)
		cache := newInteractionReplayCache(time.Minute, 1)
		fingerprint := replayFingerprint("done")
		g.Expect(beginForPrincipal(cache, 1001, "completed", fingerprint)).To(gomega.Equal(replayNew))
		cache.completeForPrincipal(1001, "completed", fingerprint, []byte("ok"))
		g.Expect(beginForPrincipal(cache, 2002, "in-flight-space", replayFingerprint("work"))).To(gomega.Equal(replayNew), "completed-response capacity incorrectly consumed in-flight capacity")
	})

	t.Run("test_replay_cache_capacity_failure_is_isolated_to_offending_principal", func(t *testing.T) {
		g := testkit.Expect(t)
		cache := newInteractionReplayCache(time.Minute, 2)
		g.Expect(beginForPrincipal(cache, 1001, "abuse-1", replayFingerprint("1"))).To(gomega.Equal(replayNew))
		g.Expect(beginForPrincipal(cache, 1001, "abuse-2", replayFingerprint("2"))).To(gomega.Equal(replayOverflow))
		g.Expect(beginForPrincipal(cache, 2002, "isolated", replayFingerprint("4"))).To(gomega.Equal(replayNew), "principal 2002 inherited principal 1001's capacity rejection")
	})

	t.Run("test_replay_cache_reports_capacity_utilization", func(t *testing.T) {
		g := testkit.Expect(t)
		cache := newInteractionReplayCache(time.Minute, 4)
		g.Expect(beginForPrincipal(cache, 1001, "one", replayFingerprint("1"))).To(gomega.Equal(replayNew))
		metrics, ok := any(cache).(replayMetricsSource)
		g.Expect(ok).To(gomega.BeTrue(), "replay cache must expose behavior-backed utilization metrics")
		if ok {
			g.Expect(metrics.metricsSnapshot().CapacityUtilization).To(gomega.Equal(0.25))
		}
	})

	t.Run("test_replay_cache_reports_per_principal_capacity_rejections", func(t *testing.T) {
		g := testkit.Expect(t)
		cache := newInteractionReplayCache(time.Minute, 1)
		g.Expect(beginForPrincipal(cache, 1001, "one", replayFingerprint("1"))).To(gomega.Equal(replayNew))
		g.Expect(beginForPrincipal(cache, 1001, "two", replayFingerprint("2"))).To(gomega.Equal(replayOverflow))
		metrics, ok := any(cache).(replayMetricsSource)
		g.Expect(ok).To(gomega.BeTrue(), "capacity rejection has no principal-scoped metric")
		if ok {
			g.Expect(metrics.metricsSnapshot().PrincipalRejections).To(gomega.HaveKeyWithValue(int64(1001), uint64(1)))
		}
	})
}
