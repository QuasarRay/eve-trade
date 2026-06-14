package observability

import (
	"context"
	"log/slog"

	"connectrpc.com/connect"
	"connectrpc.com/otelconnect"
	"go.opentelemetry.io/contrib/otelconf"
	"go.opentelemetry.io/otel"
)

type ShutdownFunc func(context.Context) error

func Init(ctx context.Context) ShutdownFunc {
	sdk, err := otelconf.NewSDK()
	if err != nil {
		slog.Error("failed to initialize OpenTelemetry", "error", err)
		return func(context.Context) error { return nil }
	}

	otel.SetTracerProvider(sdk.TracerProvider())
	otel.SetMeterProvider(sdk.MeterProvider())
	otel.SetTextMapPropagator(sdk.Propagator())

	slog.Info("OpenTelemetry initialized")

	return sdk.Shutdown
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