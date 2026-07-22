package gateway

import (
	"container/list"
	"crypto/sha256"
	"sync"
	"time"
)

const (
	defaultReplayMaxEntries = 4096
	maxReplayCleanupPerCall = 64
)

type interactionReplayCache struct {
	mu                  sync.Mutex
	ttl                 time.Duration
	maxEntries          int
	perPrincipalEntries int
	seen                map[interactionReplayKey]*interactionReplayEntry
	order               *list.List
	now                 func() time.Time
	inFlightEntries     int
	completedEntries    int
	principalEntries    map[int64]int
	principalRejections map[int64]uint64
}

type interactionReplayKey struct {
	principalID   int64
	interactionID string
}

type replayMetricsSnapshot struct {
	CapacityUtilization float64
	PrincipalRejections map[int64]uint64
}

type replayDisposition uint8

const (
	replayNew replayDisposition = iota
	replayInFlight
	replayCached
	replayConflict
	replayOverflow
)

type interactionReplayEntry struct {
	fingerprint [sha256.Size]byte
	response    []byte
	expiresAt   time.Time
	element     *list.Element
}

func newInteractionReplayCache(ttl time.Duration, maxEntries ...int) *interactionReplayCache {
	if ttl <= 0 {
		ttl = 10 * time.Minute
	}
	capacity := defaultReplayMaxEntries
	if len(maxEntries) > 0 && maxEntries[0] > 0 {
		capacity = maxEntries[0]
	}
	return &interactionReplayCache{
		ttl:                 ttl,
		maxEntries:          capacity,
		perPrincipalEntries: max(1, (capacity+1)/2),
		seen:                make(map[interactionReplayKey]*interactionReplayEntry, capacity*2),
		order:               list.New(),
		now:                 time.Now,
		principalEntries:    make(map[int64]int),
		principalRejections: make(map[int64]uint64),
	}
}

func (s *QuilkinUDPServer) replay() *interactionReplayCache {
	if s.replayCache == nil {
		s.replayCache = newInteractionReplayCache(10 * time.Minute)
	}
	return s.replayCache
}

func (c *interactionReplayCache) begin(interactionID string, fingerprint [sha256.Size]byte) (replayDisposition, []byte) {
	return c.beginForPrincipal(0, interactionID, fingerprint)
}

func (c *interactionReplayCache) beginForPrincipal(principalID int64, interactionID string, fingerprint [sha256.Size]byte) (replayDisposition, []byte) {
	c.mu.Lock()
	defer c.mu.Unlock()

	now := c.now()
	c.removeExpired(now, maxReplayCleanupPerCall)
	key := interactionReplayKey{principalID: principalID, interactionID: interactionID}
	if entry, ok := c.seen[key]; ok {
		if !entry.expiresAt.After(now) {
			c.remove(key, entry)
		} else {
			c.order.MoveToBack(entry.element)
			if entry.fingerprint != fingerprint {
				return replayConflict, nil
			}
			if entry.response == nil {
				return replayInFlight, nil
			}
			return replayCached, append([]byte(nil), entry.response...)
		}
	}
	principalLimit := c.perPrincipalEntries
	if principalID == 0 {
		principalLimit = c.maxEntries
	}
	if c.principalEntries[principalID] >= principalLimit {
		c.principalRejections[principalID]++
		return replayOverflow, nil
	}
	if c.inFlightEntries >= c.maxEntries {
		c.principalRejections[principalID]++
		return replayOverflow, nil
	}
	entry := &interactionReplayEntry{
		fingerprint: fingerprint,
		expiresAt:   now.Add(c.ttl),
	}
	entry.element = c.order.PushBack(key)
	c.seen[key] = entry
	c.inFlightEntries++
	c.principalEntries[principalID]++
	return replayNew, nil
}

func (c *interactionReplayCache) complete(interactionID string, fingerprint [sha256.Size]byte, response []byte) {
	c.completeForPrincipal(0, interactionID, fingerprint, response)
}

func (c *interactionReplayCache) completeForPrincipal(principalID int64, interactionID string, fingerprint [sha256.Size]byte, response []byte) {
	c.mu.Lock()
	defer c.mu.Unlock()

	key := interactionReplayKey{principalID: principalID, interactionID: interactionID}
	entry, ok := c.seen[key]
	if !ok || entry.fingerprint != fingerprint {
		return
	}
	if entry.response == nil {
		if c.completedEntries >= c.maxEntries {
			c.removeOldestCompleted()
		}
		c.inFlightEntries--
		c.completedEntries++
	}
	entry.response = append([]byte(nil), response...)
	entry.expiresAt = c.now().Add(c.ttl)
	c.order.MoveToBack(entry.element)
}

func (c *interactionReplayCache) release(interactionID string, fingerprint [sha256.Size]byte) {
	c.releaseForPrincipal(0, interactionID, fingerprint)
}

func (c *interactionReplayCache) releaseForPrincipal(principalID int64, interactionID string, fingerprint [sha256.Size]byte) {
	c.mu.Lock()
	defer c.mu.Unlock()

	key := interactionReplayKey{principalID: principalID, interactionID: interactionID}
	entry, ok := c.seen[key]
	if ok && entry.fingerprint == fingerprint {
		c.remove(key, entry)
	}
}

func (c *interactionReplayCache) removeExpired(now time.Time, limit int) {
	for removed := 0; removed < limit; removed++ {
		oldest := c.order.Front()
		if oldest == nil {
			return
		}
		key := oldest.Value.(interactionReplayKey)
		entry := c.seen[key]
		if entry.expiresAt.After(now) {
			return
		}
		c.remove(key, entry)
	}
}

func (c *interactionReplayCache) remove(key interactionReplayKey, entry *interactionReplayEntry) {
	delete(c.seen, key)
	c.order.Remove(entry.element)
	if entry.response == nil {
		c.inFlightEntries--
	} else {
		c.completedEntries--
	}
	c.principalEntries[key.principalID]--
	if c.principalEntries[key.principalID] == 0 {
		delete(c.principalEntries, key.principalID)
	}
}

func (c *interactionReplayCache) removeOldestCompleted() {
	for element := c.order.Front(); element != nil; element = element.Next() {
		key := element.Value.(interactionReplayKey)
		entry := c.seen[key]
		if entry.response != nil {
			c.remove(key, entry)
			return
		}
	}
}

func (c *interactionReplayCache) size() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return len(c.seen)
}

func (c *interactionReplayCache) metricsSnapshot() replayMetricsSnapshot {
	c.mu.Lock()
	defer c.mu.Unlock()
	utilization := float64(max(c.inFlightEntries, c.completedEntries)) / float64(c.maxEntries)
	rejections := make(map[int64]uint64, len(c.principalRejections))
	for principalID, count := range c.principalRejections {
		rejections[principalID] = count
	}
	return replayMetricsSnapshot{
		CapacityUtilization: utilization,
		PrincipalRejections: rejections,
	}
}
