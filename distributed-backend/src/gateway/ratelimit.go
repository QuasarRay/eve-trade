package gateway

import (
	"container/list"
	"fmt"
	"net"
	"sync"
	"time"
)

const (
	defaultLimiterMaxIdentities = 4096
	defaultLimiterIdleTTL       = 10 * time.Minute
	maxLimiterCleanupPerCall    = 32
)

type remoteRateLimiter struct {
	mu                 sync.Mutex
	rate               float64
	burst              float64
	maxIdentities      int
	idleTTL            time.Duration
	buckets            map[string]*tokenBucket
	order              *list.List
	now                func() time.Time
	capacityRejections uint64
	identityEvictions  uint64
}

type limiterMetricsSnapshot struct {
	CapacityRejections uint64
	IdentityEvictions  uint64
}

type tokenBucket struct {
	tokens  float64
	updated time.Time
	element *list.Element
}

func newRemoteRateLimiter(ratePerSecond float64, burst int) *remoteRateLimiter {
	return newBoundedRemoteRateLimiter(ratePerSecond, burst, defaultLimiterMaxIdentities, defaultLimiterIdleTTL)
}

func newBoundedRemoteRateLimiter(ratePerSecond float64, burst int, maxIdentities int, idleTTL time.Duration) *remoteRateLimiter {
	if ratePerSecond <= 0 || burst <= 0 {
		return nil
	}
	if maxIdentities <= 0 {
		maxIdentities = defaultLimiterMaxIdentities
	}
	if idleTTL <= 0 {
		idleTTL = defaultLimiterIdleTTL
	}
	return &remoteRateLimiter{
		rate:          ratePerSecond,
		burst:         float64(burst),
		maxIdentities: maxIdentities,
		idleTTL:       idleTTL,
		buckets:       make(map[string]*tokenBucket, maxIdentities),
		order:         list.New(),
		now:           time.Now,
	}
}

func (l *remoteRateLimiter) allow(key string) bool {
	l.mu.Lock()
	defer l.mu.Unlock()

	now := l.now()
	l.removeIdle(now, maxLimiterCleanupPerCall)
	bucket := l.buckets[key]
	if bucket == nil {
		if len(l.buckets) >= l.maxIdentities {
			l.capacityRejections++
			return false
		}
		bucket = &tokenBucket{tokens: l.burst - 1, updated: now}
		bucket.element = l.order.PushBack(key)
		l.buckets[key] = bucket
		return true
	}
	elapsed := now.Sub(bucket.updated).Seconds()
	bucket.updated = now
	l.order.MoveToBack(bucket.element)
	bucket.tokens = minFloat(l.burst, bucket.tokens+elapsed*l.rate)
	if bucket.tokens < 1 {
		return false
	}
	bucket.tokens--
	return true
}

func (l *remoteRateLimiter) removeIdle(now time.Time, limit int) {
	for removed := 0; removed < limit; removed++ {
		oldest := l.order.Front()
		if oldest == nil {
			return
		}
		key := oldest.Value.(string)
		if now.Sub(l.buckets[key].updated) < l.idleTTL {
			return
		}
		l.removeOldest()
		l.identityEvictions++
	}
}

func (l *remoteRateLimiter) removeOldest() {
	oldest := l.order.Front()
	if oldest == nil {
		return
	}
	delete(l.buckets, oldest.Value.(string))
	l.order.Remove(oldest)
}

func (l *remoteRateLimiter) size() int {
	l.mu.Lock()
	defer l.mu.Unlock()
	return len(l.buckets)
}

func (l *remoteRateLimiter) metricsSnapshot() limiterMetricsSnapshot {
	l.mu.Lock()
	defer l.mu.Unlock()
	return limiterMetricsSnapshot{
		CapacityRejections: l.capacityRejections,
		IdentityEvictions:  l.identityEvictions,
	}
}

func (s *QuilkinUDPServer) allowSource(remote net.Addr) bool {
	return s.sourceLimiter == nil || s.sourceLimiter.allow(remoteKey(remote))
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
