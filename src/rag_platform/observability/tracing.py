"""OpenTelemetry tracing, opt-in via RAG_OTEL_EXPORTER.

- none (default): zero overhead, nothing configured.
- console: spans printed to stdout — how tracing is verified in dev/tests
  without running a collector.
- otlp: OTLP/HTTP to RAG_OTEL_ENDPOINT (or the standard
  OTEL_EXPORTER_OTLP_ENDPOINT env), Batch-processed.
"""

from fastapi import FastAPI

from rag_platform.config import Settings


def configure_tracing(app: FastAPI, settings: Settings) -> None:
    if settings.otel_exporter == "none":
        return

    # Imports deferred so the "none" path pays no otel import cost.
    from opentelemetry import trace
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
        SimpleSpanProcessor,
    )

    provider = TracerProvider(resource=Resource.create({"service.name": settings.app_name}))
    if settings.otel_exporter == "console":
        # Simple (unbatched) so spans appear immediately during verification
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    else:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exporter = (
            OTLPSpanExporter(endpoint=settings.otel_endpoint)
            if settings.otel_endpoint
            else OTLPSpanExporter()  # falls back to OTEL_EXPORTER_OTLP_ENDPOINT
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
