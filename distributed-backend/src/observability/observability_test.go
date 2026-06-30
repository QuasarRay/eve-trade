package observability

import (
	"context"
	"errors"
	"log/slog"
	"reflect"
	"testing"
	"time"

	"go.opentelemetry.io/otel/trace"
)

type captureHandler struct {
	enabled bool
	err     error
	records *[]slog.Record
}

func (h captureHandler) Enabled(context.Context, slog.Level) bool { return h.enabled }
func (h captureHandler) Handle(_ context.Context, record slog.Record) error {
	*h.records = append(*h.records, record.Clone())
	return h.err
}
func (h captureHandler) WithAttrs([]slog.Attr) slog.Handler { return h }
func (h captureHandler) WithGroup(string) slog.Handler      { return h }

func TestConfigurationHelpersUseStrictFallbacks(t *testing.T) {
	t.Setenv("OTEL_METRIC_EXPORT_INTERVAL", "250")
	if got := metricInterval(); got != 250*time.Millisecond {
		t.Fatalf("metric interval milliseconds = %v", got)
	}
	t.Setenv("OTEL_METRIC_EXPORT_INTERVAL", "2s")
	if got := metricInterval(); got != 2*time.Second {
		t.Fatalf("metric interval duration = %v", got)
	}
	t.Setenv("OTEL_METRIC_EXPORT_INTERVAL", "invalid")
	if got := metricInterval(); got != 15*time.Second {
		t.Fatalf("invalid metric interval = %v", got)
	}
	for value, want := range map[string]bool{"true": true, "1": true, "yes": true, "false": false} {
		t.Setenv("OTEL_SDK_DISABLED", value)
		if got := sdkDisabled(); got != want {
			t.Fatalf("sdkDisabled(%q) = %v", value, got)
		}
	}
	t.Setenv("LOG_LEVEL", "warning")
	if got := logLevel().Level(); got != slog.LevelWarn {
		t.Fatalf("warning level = %v", got)
	}
}

func TestDomainAttributeConversionAndIdentifierHash(t *testing.T) {
	attrs := toOTELAttributes([]slog.Attr{
		slog.String("string", "value"), slog.Int64("int", 2), slog.Uint64("uint", 3),
		slog.Float64("float", 1.5), slog.Bool("bool", true), slog.Any("any", []int{1, 2}),
	})
	if len(attrs) != 6 {
		t.Fatalf("converted attributes = %d", len(attrs))
	}
	first := HashIdentifier(1001)
	if len(first) != 16 || first == HashIdentifier(1002) || first != HashIdentifier(1001) {
		t.Fatalf("identifier hashes are not stable and distinct: %q", first)
	}
}

func TestCorrelationAndTeeHandlersPreserveRecordsAndErrors(t *testing.T) {
	var leftRecords, rightRecords []slog.Record
	leftErr := errors.New("left")
	rightErr := errors.New("right")
	tee := newTeeHandler(
		captureHandler{enabled: true, err: leftErr, records: &leftRecords},
		captureHandler{enabled: true, err: rightErr, records: &rightRecords},
	)
	ctx := trace.ContextWithSpanContext(context.Background(), trace.NewSpanContext(trace.SpanContextConfig{
		TraceID: trace.TraceID{1}, SpanID: trace.SpanID{2}, TraceFlags: trace.FlagsSampled,
	}))
	handler := newCorrelationHandler(tee)
	if !handler.Enabled(ctx, slog.LevelInfo) {
		t.Fatal("combined handler unexpectedly disabled")
	}
	err := handler.Handle(ctx, slog.NewRecord(time.Now(), slog.LevelInfo, "message", 0))
	if !errors.Is(err, leftErr) || !errors.Is(err, rightErr) {
		t.Fatalf("combined handler error = %v", err)
	}
	for _, records := range [][]slog.Record{leftRecords, rightRecords} {
		if len(records) != 1 {
			t.Fatalf("records = %d", len(records))
		}
		values := map[string]string{}
		records[0].Attrs(func(attr slog.Attr) bool { values[attr.Key] = attr.Value.String(); return true })
		if values["trace_id"] == "" || values["span_id"] == "" {
			t.Fatalf("correlation attributes missing: %v", values)
		}
	}
}

func TestShutdownRunsInReverseOrderAndInitCanBeDisabled(t *testing.T) {
	var order []int
	shutdown := shutdownAll([]ShutdownFunc{
		func(context.Context) error { order = append(order, 1); return nil },
		func(context.Context) error { order = append(order, 2); return nil },
	})
	if err := shutdown(context.Background()); err != nil || !reflect.DeepEqual(order, []int{2, 1}) {
		t.Fatalf("shutdown order=%v err=%v", order, err)
	}
	t.Setenv("OTEL_SDK_DISABLED", "true")
	if err := Init(context.Background())(context.Background()); err != nil {
		t.Fatalf("disabled SDK shutdown failed: %v", err)
	}
}

func TestNormalizeLogAttributeRenamesSensitiveStandardKeys(t *testing.T) {
	if got := normalizeLogAttribute(nil, slog.String(slog.TimeKey, "now")); got.Key != "timestamp" {
		t.Fatalf("time key = %q", got.Key)
	}
	if got := normalizeLogAttribute(nil, slog.String("error", "secret")); got.Key != "error.message" {
		t.Fatalf("error key = %q", got.Key)
	}
}
