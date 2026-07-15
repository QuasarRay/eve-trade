package observability

import (
	"context"
	"errors"
	"log/slog"
	"os"
	"strconv"
	"strings"
	"time"

	"go.opentelemetry.io/contrib/bridges/otelslog"
	"go.opentelemetry.io/contrib/instrumentation/host"
	otelruntime "go.opentelemetry.io/contrib/instrumentation/runtime"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlplog/otlploghttp"
	"go.opentelemetry.io/otel/exporters/otlp/otlpmetric/otlpmetrichttp"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/log/global"
	"go.opentelemetry.io/otel/propagation"
	sdklog "go.opentelemetry.io/otel/sdk/log"
	sdkmetric "go.opentelemetry.io/otel/sdk/metric"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
)

const (
	defaultServiceName      = "eve-trade-service"
	defaultServiceNamespace = "eve-trade"
	defaultLogHandlerName   = "github.com/QuasarRay/eve-trade/observability/slog"
)

type ShutdownFunc func(context.Context) error

func Init(ctx context.Context) ShutdownFunc {
	serviceName := envOr("OTEL_SERVICE_NAME", defaultServiceName)
	commonAttrs := []slog.Attr{
		slog.String("service.name", serviceName),
		slog.String("service.version", envOr("SERVICE_VERSION", envOr("GITHUB_SHA", "local"))),
		slog.String("service.language", "go"),
		slog.String("observability.run_id", envOr("OBSERVABILITY_RUN_ID", "unobserved")),
	}
	jsonHandler := newCorrelationHandler(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
		Level:       logLevel(),
		ReplaceAttr: normalizeLogAttribute,
	}).WithAttrs(commonAttrs))
	slog.SetDefault(slog.New(jsonHandler))

	if sdkDisabled() {
		slog.Info("OpenTelemetry SDK disabled")
		return func(context.Context) error { return nil }
	}

	otelResource, err := newResource(ctx, serviceName)
	if err != nil {
		slog.Error("failed to initialize OpenTelemetry resource", "error", err)
		return func(context.Context) error { return nil }
	}

	var shutdowns []ShutdownFunc

	tracerProvider, err := newTracerProvider(ctx, otelResource)
	if err != nil {
		slog.Error("failed to initialize OpenTelemetry traces", "error", err)
		return shutdownAll(shutdowns)
	}
	otel.SetTracerProvider(tracerProvider)
	shutdowns = append(shutdowns, tracerProvider.Shutdown)

	meterProvider, err := newMeterProvider(ctx, otelResource)
	if err != nil {
		slog.Error("failed to initialize OpenTelemetry metrics", "error", err)
		return shutdownAll(shutdowns)
	}
	otel.SetMeterProvider(meterProvider)
	shutdowns = append(shutdowns, meterProvider.Shutdown)

	loggerProvider, err := newLoggerProvider(ctx, otelResource)
	if err != nil {
		slog.Error("failed to initialize OpenTelemetry logs", "error", err)
		return shutdownAll(shutdowns)
	}
	global.SetLoggerProvider(loggerProvider)
	shutdowns = append(shutdowns, loggerProvider.Shutdown)

	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(
		propagation.TraceContext{},
		propagation.Baggage{},
	))

	if err := otelruntime.Start(
		otelruntime.WithMeterProvider(meterProvider),
		otelruntime.WithMinimumReadMemStatsInterval(metricInterval()),
	); err != nil {
		slog.Warn("failed to start Go runtime metrics", "error", err)
	}
	if err := host.Start(host.WithMeterProvider(meterProvider)); err != nil {
		slog.Warn("failed to start host metrics", "error", err)
	}

	otelHandler := otelslog.NewHandler(
		envOr("OTEL_LOG_HANDLER_NAME", defaultLogHandlerName),
		otelslog.WithLoggerProvider(loggerProvider),
	).WithAttrs(commonAttrs)
	slog.SetDefault(slog.New(newTeeHandler(jsonHandler, otelHandler)))
	slog.Info("OpenTelemetry initialized", "service_name", serviceName)

	return shutdownAll(shutdowns)
}

func newResource(ctx context.Context, serviceName string) (*resource.Resource, error) {
	return resource.New(ctx,
		resource.WithTelemetrySDK(),
		resource.WithHost(),
		resource.WithOS(),
		// Avoid WithProcess: it includes command arguments (which may contain
		// secrets) and a process-owner detector that fails in minimal containers.
		resource.WithProcessPID(),
		resource.WithProcessExecutableName(),
		resource.WithProcessRuntimeName(),
		resource.WithProcessRuntimeVersion(),
		resource.WithProcessRuntimeDescription(),
		resource.WithContainer(),
		resource.WithFromEnv(),
		resource.WithAttributes(
			attribute.String("service.name", serviceName),
			attribute.String("service.namespace", envOr("OTEL_SERVICE_NAMESPACE", defaultServiceNamespace)),
			attribute.String("service.version", envOr("SERVICE_VERSION", envOr("GITHUB_SHA", "local"))),
			attribute.String("service.language", "go"),
			attribute.String("deployment.environment.name", envOr("DEPLOYMENT_ENVIRONMENT", "development")),
			attribute.String("observability.run_id", envOr("OBSERVABILITY_RUN_ID", "unobserved")),
		),
	)
}

func newTracerProvider(ctx context.Context, otelResource *resource.Resource) (*sdktrace.TracerProvider, error) {
	exporter, err := otlptracehttp.New(ctx)
	if err != nil {
		return nil, err
	}

	return sdktrace.NewTracerProvider(
		sdktrace.WithResource(otelResource),
		sdktrace.WithBatcher(exporter),
	), nil
}

func newMeterProvider(ctx context.Context, otelResource *resource.Resource) (*sdkmetric.MeterProvider, error) {
	exporter, err := otlpmetrichttp.New(ctx)
	if err != nil {
		return nil, err
	}

	return sdkmetric.NewMeterProvider(
		sdkmetric.WithResource(otelResource),
		sdkmetric.WithReader(sdkmetric.NewPeriodicReader(
			exporter,
			sdkmetric.WithInterval(metricInterval()),
		)),
	), nil
}

func newLoggerProvider(ctx context.Context, otelResource *resource.Resource) (*sdklog.LoggerProvider, error) {
	exporter, err := otlploghttp.New(ctx)
	if err != nil {
		return nil, err
	}

	return sdklog.NewLoggerProvider(
		sdklog.WithResource(otelResource),
		sdklog.WithProcessor(sdklog.NewBatchProcessor(exporter)),
	), nil
}

func shutdownAll(shutdowns []ShutdownFunc) ShutdownFunc {
	return func(ctx context.Context) error {
		var err error
		for i := len(shutdowns) - 1; i >= 0; i-- {
			err = errors.Join(err, shutdowns[i](ctx))
		}
		return err
	}
}

func metricInterval() time.Duration {
	value := strings.TrimSpace(os.Getenv("OTEL_METRIC_EXPORT_INTERVAL"))
	if value == "" {
		return 15 * time.Second
	}
	if milliseconds, err := strconv.Atoi(value); err == nil && milliseconds > 0 {
		return time.Duration(milliseconds) * time.Millisecond
	}
	if duration, err := time.ParseDuration(value); err == nil && duration > 0 {
		return duration
	}
	return 15 * time.Second
}

func logLevel() slog.Leveler {
	switch strings.ToLower(strings.TrimSpace(os.Getenv("LOG_LEVEL"))) {
	case "debug":
		return slog.LevelDebug
	case "warn", "warning":
		return slog.LevelWarn
	case "error":
		return slog.LevelError
	default:
		return slog.LevelInfo
	}
}

func sdkDisabled() bool {
	value := strings.ToLower(strings.TrimSpace(os.Getenv("OTEL_SDK_DISABLED")))
	return value == "true" || value == "1" || value == "yes"
}

func envOr(name string, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}
