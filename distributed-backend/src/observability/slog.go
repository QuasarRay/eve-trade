package observability

import (
	"context"
	"errors"
	"log/slog"
)

type teeHandler struct {
	left  slog.Handler
	right slog.Handler
}

func newTeeHandler(left slog.Handler, right slog.Handler) slog.Handler {
	return teeHandler{left: left, right: right}
}

func (h teeHandler) Enabled(ctx context.Context, level slog.Level) bool {
	return h.left.Enabled(ctx, level) || h.right.Enabled(ctx, level)
}

func (h teeHandler) Handle(ctx context.Context, record slog.Record) error {
	var err error
	if h.left.Enabled(ctx, record.Level) {
		err = h.left.Handle(ctx, record.Clone())
	}
	if h.right.Enabled(ctx, record.Level) {
		err = errorsJoin(err, h.right.Handle(ctx, record.Clone()))
	}
	return err
}

func (h teeHandler) WithAttrs(attrs []slog.Attr) slog.Handler {
	return teeHandler{
		left:  h.left.WithAttrs(attrs),
		right: h.right.WithAttrs(attrs),
	}
}

func (h teeHandler) WithGroup(name string) slog.Handler {
	return teeHandler{
		left:  h.left.WithGroup(name),
		right: h.right.WithGroup(name),
	}
}

func errorsJoin(left error, right error) error {
	return errors.Join(left, right)
}
