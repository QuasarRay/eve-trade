package gateway

import (
	"crypto/sha256"
	"fmt"
	"testing"
	"time"
)

func BenchmarkAuthenticatedPayload(b *testing.B) {
	server := testUDPServer(&recordingMarketClient{})
	packet := signedUDPPacketForFuzz(authenticatedTestPayload("benchmark", 1), "edge-secret", "primary")
	b.ReportAllocs()
	b.SetBytes(int64(len(packet)))
	b.ResetTimer()
	for range b.N {
		payload, interactionID, principalID, rejection := server.authenticatedPayload(packet)
		if rejection != nil || len(payload) == 0 || interactionID != "benchmark" || principalID != 1001 {
			b.Fatalf("valid packet rejected: %v", rejection)
		}
	}
}

func BenchmarkReplayCache(b *testing.B) {
	fingerprint := sha256.Sum256([]byte("request"))
	b.Run("new-and-release", func(b *testing.B) {
		cache := newInteractionReplayCache(time.Minute, 4096)
		b.ReportAllocs()
		for i := range b.N {
			interactionID := fmt.Sprintf("interaction-%d", i)
			disposition, _ := cache.begin(interactionID, fingerprint)
			if disposition != replayNew {
				b.Fatalf("disposition = %v", disposition)
			}
			cache.release(interactionID, fingerprint)
		}
	})
	b.Run("cached", func(b *testing.B) {
		cache := newInteractionReplayCache(time.Minute, 1)
		if disposition, _ := cache.begin("interaction", fingerprint); disposition != replayNew {
			b.Fatalf("disposition = %v", disposition)
		}
		cache.complete("interaction", fingerprint, []byte(`{"status":"ok"}`))
		b.ReportAllocs()
		b.ResetTimer()
		for range b.N {
			disposition, response := cache.begin("interaction", fingerprint)
			if disposition != replayCached || len(response) == 0 {
				b.Fatalf("disposition = %v", disposition)
			}
		}
	})
}

func BenchmarkRateLimiterLookup(b *testing.B) {
	limiter := newBoundedRemoteRateLimiter(1_000_000_000, 1, 4096, time.Minute)
	now := time.Unix(0, 0)
	limiter.now = func() time.Time {
		now = now.Add(time.Nanosecond)
		return now
	}
	b.ReportAllocs()
	b.ResetTimer()
	for range b.N {
		if !limiter.allow("capsuleer:1001") {
			b.Fatal("benchmark limiter unexpectedly rejected")
		}
	}
}
