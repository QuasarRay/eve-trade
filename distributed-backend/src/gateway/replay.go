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
	mu         sync.Mutex
	ttl        time.Duration
	maxEntries int
	seen       map[string]*interactionReplayEntry
	order      *list.List
	now        func() time.Time
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
		ttl:        ttl,
		maxEntries: capacity,
		seen:       make(map[string]*interactionReplayEntry, capacity),
		order:      list.New(),
		now:        time.Now,
	}
}

func (s *QuilkinUDPServer) replay() *interactionReplayCache {
	if s.replayCache == nil {
		s.replayCache = newInteractionReplayCache(10 * time.Minute)
	}
	return s.replayCache
}

func (c *interactionReplayCache) begin(interactionID string, fingerprint [sha256.Size]byte) (replayDisposition, []byte) {
	c.mu.Lock()
	defer c.mu.Unlock()

	now := c.now()
	c.removeExpired(now, maxReplayCleanupPerCall)
	if entry, ok := c.seen[interactionID]; ok {
		if !entry.expiresAt.After(now) {
			c.remove(interactionID, entry)
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
	if len(c.seen) >= c.maxEntries {
		return replayOverflow, nil
	}
	entry := &interactionReplayEntry{
		fingerprint: fingerprint,
		expiresAt:   now.Add(c.ttl),
	}
	entry.element = c.order.PushBack(interactionID)
	c.seen[interactionID] = entry
	return replayNew, nil
}

func (c *interactionReplayCache) complete(interactionID string, fingerprint [sha256.Size]byte, response []byte) {
	c.mu.Lock()
	defer c.mu.Unlock()

	entry, ok := c.seen[interactionID]
	if !ok || entry.fingerprint != fingerprint {
		return
	}
	entry.response = append([]byte(nil), response...)
	entry.expiresAt = c.now().Add(c.ttl)
	c.order.MoveToBack(entry.element)
}

func (c *interactionReplayCache) release(interactionID string, fingerprint [sha256.Size]byte) {
	c.mu.Lock()
	defer c.mu.Unlock()

	entry, ok := c.seen[interactionID]
	if ok && entry.fingerprint == fingerprint {
		c.remove(interactionID, entry)
	}
}

func (c *interactionReplayCache) removeExpired(now time.Time, limit int) {
	for removed := 0; removed < limit; removed++ {
		oldest := c.order.Front()
		if oldest == nil {
			return
		}
		interactionID := oldest.Value.(string)
		entry := c.seen[interactionID]
		if entry.expiresAt.After(now) {
			return
		}
		c.remove(interactionID, entry)
	}
}

func (c *interactionReplayCache) remove(interactionID string, entry *interactionReplayEntry) {
	delete(c.seen, interactionID)
	c.order.Remove(entry.element)
}

func (c *interactionReplayCache) size() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return len(c.seen)
}
