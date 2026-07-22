package testkit

import (
	"encoding/json"
	"os"
	"sync"
	"testing"
	"time"

	fpAssert "github.com/IBM/fp-go/v2/assert"
	"github.com/IBM/fp-go/v2/option"
	gustResult "github.com/andeya/gust/result"
	"github.com/lainio/err2"
	"github.com/lainio/err2/try"
	"github.com/onsi/gomega"
)

type ManualClock struct {
	mu      sync.Mutex
	now     time.Time
	waiters []manualWaiter
}

type manualWaiter struct {
	deadline time.Time
	ready    chan time.Time
}

func NewManualClock(start time.Time) *ManualClock {
	return &ManualClock{now: start}
}

func (clock *ManualClock) Now() time.Time {
	clock.mu.Lock()
	defer clock.mu.Unlock()
	return clock.now
}

func (clock *ManualClock) Advance(duration time.Duration) {
	clock.mu.Lock()
	defer clock.mu.Unlock()
	clock.now = clock.now.Add(duration)
	pending := clock.waiters[:0]
	for _, waiter := range clock.waiters {
		if waiter.deadline.After(clock.now) {
			pending = append(pending, waiter)
			continue
		}
		waiter.ready <- clock.now
		close(waiter.ready)
	}
	clock.waiters = pending
}

func (clock *ManualClock) After(duration time.Duration) <-chan time.Time {
	clock.mu.Lock()
	defer clock.mu.Unlock()
	ready := make(chan time.Time, 1)
	deadline := clock.now.Add(duration)
	if !deadline.After(clock.now) {
		ready <- clock.now
		close(ready)
		return ready
	}
	clock.waiters = append(clock.waiters, manualWaiter{deadline: deadline, ready: ready})
	return ready
}

type Recorder[T any] struct {
	mu     sync.Mutex
	values []T
}

func (recorder *Recorder[T]) Add(value T) {
	recorder.mu.Lock()
	defer recorder.mu.Unlock()
	recorder.values = append(recorder.values, value)
}

func (recorder *Recorder[T]) Values() []T {
	recorder.mu.Lock()
	defer recorder.mu.Unlock()
	return append([]T(nil), recorder.values...)
}

func (recorder *Recorder[T]) Last() option.Option[T] {
	values := recorder.Values()
	if len(values) == 0 {
		return option.None[T]()
	}
	return option.Some(values[len(values)-1])
}

func Capture[T any](operation func() (T, error)) gustResult.Result[T] {
	value, err := operation()
	if err != nil {
		return gustResult.TryErr[T](err)
	}
	return gustResult.Ok(value)
}

func MustCapture[T any](t *testing.T, operation func() (T, error)) T {
	t.Helper()
	result := Capture(operation)
	if result.IsErr() {
		t.Fatalf("test fixture operation failed: %v", result.UnwrapErr())
	}
	return result.Unwrap()
}

func ReadFile(path string) (contents []byte, err error) {
	defer err2.Handle(&err, "read test fixture %s", path)
	return try.To1(os.ReadFile(path)), nil
}

func ReadJSON(path string, target any) (err error) {
	defer err2.Handle(&err, "read test JSON %s", path)
	contents := try.To1(os.ReadFile(path))
	try.To(json.Unmarshal(contents, target))
	return nil
}

func Expect(t *testing.T) *gomega.WithT {
	t.Helper()
	return gomega.NewWithT(t)
}

func AssertSequence[T any](t *testing.T, expected []T, actual []T) {
	t.Helper()
	fpAssert.Equal(expected)(actual)(t)
}
