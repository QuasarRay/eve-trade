package gateway

import (
	"crypto/sha256"
	"sync"
	"time"
)

type interactionReplayCache struct {
	mu    sync.Mutex
	ttl   time.Duration
	seen  map[string]interactionReplayEntry
	now   func() time.Time
	sweep time.Time
}

type replayDisposition uint8

const (
	replayNew replayDisposition = iota
	replayInFlight
	replayCached
	replayConflict
)

type interactionReplayEntry struct {
	fingerprint [sha256.Size]byte
	response    []byte
	expiresAt   time.Time
}

func newInteractionReplayCache(ttl time.Duration) *interactionReplayCache {
	if ttl <= 0 {
		ttl = 10 * time.Minute
	}
	return &interactionReplayCache{
		ttl:  ttl,
		seen: make(map[string]interactionReplayEntry),
		now:  time.Now,
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
	if now.After(c.sweep) {
		for id, entry := range c.seen {
			if !entry.expiresAt.After(now) {
				delete(c.seen, id)
			}
		}
		c.sweep = now.Add(c.ttl / 2)
	}
	if entry, ok := c.seen[interactionID]; ok && entry.expiresAt.After(now) {
		if entry.fingerprint != fingerprint {
			return replayConflict, nil
		}
		if entry.response == nil {
			return replayInFlight, nil
		}
		return replayCached, append([]byte(nil), entry.response...)
	}
	c.seen[interactionID] = interactionReplayEntry{
		fingerprint: fingerprint,
		expiresAt:   now.Add(c.ttl),
	}
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
	c.seen[interactionID] = entry
}

func (c *interactionReplayCache) release(interactionID string, fingerprint [sha256.Size]byte) {
	c.mu.Lock()
	defer c.mu.Unlock()

	entry, ok := c.seen[interactionID]
	if ok && entry.fingerprint == fingerprint {
		delete(c.seen, interactionID)
	}
}
