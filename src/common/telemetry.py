"""
telemetry.py — OpenTelemetry Bootstrap & Unified Trace Provider

Provides a single, consistent OpenTelemetry initialization point used by all
agent nodes, data pipeline components, and infrastructure services. Ensures
every span, metric, and log event carries consistent resource attributes for
correlation in CloudWatch / Grafana.

Integration pattern:
    from src.common.telemetry import get_tracer, init_telemetry

    tracer = get_tracer(__name__)
    with tracer.start_as_current_span("my_operation") as span:
        span.set_attribute("key", "value")
        # ... work ...

Architecture:
  - Traces are exported via OTLP HTTP to the configured endpoint.
  - If no endpoint is configured, traces are written to stdout (console
    exporter) for local development.
  - Resources carry service.name, deployment.environment, and service.version
    for cross-service correlation.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy globals
# ---------------------------------------------------------------------------

_tracer_provider = None
_is_initialized = False


def init_telemetry(
    service_name: str = "ai-data-catalog-agent",
    environment: str = "local",
    service_version: str = "1.0.0",
    otlp_endpoint: Optional[str] = None,
) -> None:
    """
    Initialize the OpenTelemetry SDK with OTLP HTTP exporter.

    Call once at application startup. Safe to call multiple times (idempotent).

    Args:
        service_name:    Logical service name (appears in trace viewer).
        environment:     Deployment environment tag (local/dev/staging/prod).
        service_version: Semver of the current deployment.
        otlp_endpoint:   OTLP HTTP collector endpoint. Falls back to
                         OTEL_EXPORTER_OTLP_ENDPOINT env var, then to
                         stdout console exporter if neither is set.
    """
    global _tracer_provider, _is_initialized

    if _is_initialized:
        logger.debug("OpenTelemetry already initialized — skipping")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

        endpoint = otlp_endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")

        resource = Resource.create({
            "service.name": service_name,
            "service.version": service_version,
            "deployment.environment": environment,
            "telemetry.sdk.name": "opentelemetry",
            "telemetry.sdk.language": "python",
        })

        _tracer_provider = TracerProvider(resource=resource)

        if endpoint:
            _tracer_provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
            )
            logger.info("OTLP exporter configured: %s", endpoint)
        else:
            # Console exporter for local development
            _tracer_provider.add_span_processor(
                BatchSpanProcessor(ConsoleSpanExporter())
            )
            logger.info("No OTLP endpoint set — using console exporter")

        trace.set_tracer_provider(_tracer_provider)
        _is_initialized = True
        logger.info("OpenTelemetry initialized for '%s' [%s]", service_name, environment)

    except ImportError as exc:
        logger.warning(
            "OpenTelemetry packages not installed (%s). "
            "Telemetry will be disabled. Install with: "
            "poetry add opentelemetry-api opentelemetry-sdk "
            "opentelemetry-exporter-otlp-proto-http",
            exc,
        )
    except Exception as exc:
        logger.error("Failed to initialize OpenTelemetry: %s", exc)


def get_tracer(module_name: str = __name__):
    """
    Return a tracer for the calling module.

    Safe to call before init_telemetry() — returns a NoopTracer if the SDK
    hasn't been initialized yet, allowing early instrumentation without
    ordering constraints.
    """
    try:
        from opentelemetry import trace
        return trace.get_tracer(module_name)
    except ImportError:
        return _NoopTracer()


def shutdown_telemetry() -> None:
    """Flush and shut down the tracer provider gracefully."""
    global _tracer_provider, _is_initialized
    if _tracer_provider is not None:
        try:
            _tracer_provider.shutdown()
            logger.info("OpenTelemetry shut down")
        except Exception as exc:
            logger.warning("Error shutting down OpenTelemetry: %s", exc)
    _is_initialized = False


# ---------------------------------------------------------------------------
# Noop fallback for environments without OTEL
# ---------------------------------------------------------------------------

class _NoopSpan:
    """Context manager that does nothing — used as fallback."""
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def set_attribute(self, key: str, value: object) -> None:
        pass
    def set_attributes(self, attributes: dict) -> None:
        pass
    def record_exception(self, exc: Exception) -> None:
        pass
    def add_event(self, name: str, attributes: Optional[dict] = None) -> None:
        pass


class _NoopTracer:
    """Tracer that returns _NoopSpan for all operations."""
    def start_as_current_span(self, name: str, **kwargs) -> _NoopSpan:
        return _NoopSpan()
    def start_span(self, name: str, **kwargs) -> _NoopSpan:
        return _NoopSpan()


# ---------------------------------------------------------------------------
# CloudWatch integration helper
# ---------------------------------------------------------------------------

def get_cloudwatch_log_group() -> str:
    """
    Return the CloudWatch log group name based on the environment.

    Pattern: /ecs/<service_name>/<environment>
    """
    service = os.environ.get("OTEL_SERVICE_NAME", "ai-data-catalog-agent")
    env = os.environ.get("ENVIRONMENT", "local")
    return f"/ecs/{service}/{env}"