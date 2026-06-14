package observability

import (
	"context"
	"log/slog"

	"connectrpc.com/connect"
	"connectrpc.com/otelconnect"
)

type ShutdownFunc func(context.Context) error

func Init(ctx context.Context) ShutdownFunc {
	_ = ctx
	slog.Info("OpenTelemetry interceptors initialized with default providers")
	return func(context.Context) error { return nil }
}

func NewExternalServerInterceptor() connect.Interceptor {
	interceptor, err := otelconnect.NewInterceptor(
		otelconnect.WithoutServerPeerAttributes(),
	)
	if err != nil {
		panic(err)
	}

	return interceptor
}

func NewInternalServerInterceptor() connect.Interceptor {
	interceptor, err := otelconnect.NewInterceptor(
		otelconnect.WithTrustRemote(),
		otelconnect.WithoutServerPeerAttributes(),
	)
	if err != nil {
		panic(err)
	}

	return interceptor
}

func NewClientInterceptor() connect.Interceptor {
	interceptor, err := otelconnect.NewInterceptor()
	if err != nil {
		panic(err)
	}

	return interceptor
}
