package observability

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"log/slog"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/trace"
)

const domainTracerName = "github.com/QuasarRay/eve-trade/domain"

type DomainSpan struct {
	span trace.Span
}

func StartSpan(ctx context.Context, name string, attrs ...slog.Attr) (context.Context, *DomainSpan) {
	ctx, span := otel.Tracer(domainTracerName).Start(ctx, name, trace.WithAttributes(toOTELAttributes(attrs)...))
	return ctx, &DomainSpan{span: span}
}

func (s *DomainSpan) Set(attrs ...slog.Attr) {
	s.span.SetAttributes(toOTELAttributes(attrs)...)
}

func (s *DomainSpan) RecordError(err error) {
	if err == nil {
		return
	}
	s.span.RecordError(err)
	s.span.SetStatus(codes.Error, err.Error())
	s.span.SetAttributes(
		attribute.String("error.kind", fmt.Sprintf("%T", err)),
		attribute.String("error.message", err.Error()),
	)
}

func (s *DomainSpan) End() {
	s.span.End()
}

func HashIdentifier(value int64) string {
	sum := sha256.Sum256([]byte(fmt.Sprintf("eve-trade-id:%d", value)))
	return hex.EncodeToString(sum[:8])
}

func toOTELAttributes(attrs []slog.Attr) []attribute.KeyValue {
	result := make([]attribute.KeyValue, 0, len(attrs))
	for _, attr := range attrs {
		value := attr.Value.Resolve()
		switch value.Kind() {
		case slog.KindString:
			result = append(result, attribute.String(attr.Key, value.String()))
		case slog.KindInt64:
			result = append(result, attribute.Int64(attr.Key, value.Int64()))
		case slog.KindUint64:
			result = append(result, attribute.Int64(attr.Key, int64(value.Uint64())))
		case slog.KindFloat64:
			result = append(result, attribute.Float64(attr.Key, value.Float64()))
		case slog.KindBool:
			result = append(result, attribute.Bool(attr.Key, value.Bool()))
		default:
			result = append(result, attribute.String(attr.Key, fmt.Sprint(value.Any())))
		}
	}
	return result
}
