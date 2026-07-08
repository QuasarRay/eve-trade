package gateway

import (
	"fmt"
	"net"
	"sync"
	"time"
)

type remoteRateLimiter struct {
	mu      sync.Mutex
	rate    float64
	burst   float64
	buckets map[string]*tokenBucket
	now     func() time.Time
}

type tokenBucket struct {
	tokens  float64
	updated time.Time
}

func newRemoteRateLimiter(ratePerSecond float64, burst int) *remoteRateLimiter {
	if ratePerSecond <= 0 || burst <= 0 {
		return nil
	}
	return &remoteRateLimiter{
		rate:    ratePerSecond,
		burst:   float64(burst),
		buckets: make(map[string]*tokenBucket),
		now:     time.Now,
	}
}

func (l *remoteRateLimiter) allow(key string) bool {
	l.mu.Lock()
	defer l.mu.Unlock()

	now := l.now()
	bucket := l.buckets[key]
	if bucket == nil {
		l.buckets[key] = &tokenBucket{tokens: l.burst - 1, updated: now}
		return true
	}
	elapsed := now.Sub(bucket.updated).Seconds()
	bucket.updated = now
	bucket.tokens = minFloat(l.burst, bucket.tokens+elapsed*l.rate)
	if bucket.tokens < 1 {
		return false
	}
	bucket.tokens--
	return true
}

func (s *QuilkinUDPServer) allowPrincipal(principalID int64, remote net.Addr) bool {
	if s.rateLimiter == nil {
		return true
	}
	key := remoteKey(remote)
	if principalID > 0 {
		key = fmt.Sprintf("capsuleer:%d", principalID)
	}
	return s.rateLimiter.allow(key)
}

func minFloat(a float64, b float64) float64 {
	if a < b {
		return a
	}
	return b
}
