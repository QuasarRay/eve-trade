package testkit

import (
	"testing"
	"time"
)

func TestManualClockAfterFiresOnlyWhenAdvancedPastDeadline(t *testing.T) {
	start := time.Unix(1_700_000_000, 0)
	clock := NewManualClock(start)
	ready := clock.After(time.Minute)

	clock.Advance(59 * time.Second)
	select {
	case firedAt := <-ready:
		t.Fatalf("manual deadline fired early at %s", firedAt)
	default:
	}

	clock.Advance(time.Second)
	select {
	case firedAt := <-ready:
		if want := start.Add(time.Minute); !firedAt.Equal(want) {
			t.Fatalf("manual deadline fired at %s, want %s", firedAt, want)
		}
	default:
		t.Fatal("manual deadline did not fire after the clock reached it")
	}
}
