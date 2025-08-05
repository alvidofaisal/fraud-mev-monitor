from fastapi import FastAPI, Response
import asyncio
from redis.asyncio import Redis
import structlog
import os

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor

from app.processor.stream import start_stream_processor
from app.metrics import get_metrics, get_content_type

# Configure structured logging
structlog.configure(
    processors=[structlog.processors.JSONRenderer()],
    wrapper_class=structlog.make_filtering_bound_logger(30),  # INFO level
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

# Configure OpenTelemetry
trace.set_tracer_provider(TracerProvider())
tracer = trace.get_tracer(__name__)

# Setup OTLP exporter if endpoint is configured
if otlp_endpoint := os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
    otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
    span_processor = BatchSpanProcessor(otlp_exporter)
    trace.get_tracer_provider().add_span_processor(span_processor)

app = FastAPI(title="Fraud / MEV Monitor", version="0.1.0")

# Instrument FastAPI and Redis
FastAPIInstrumentor.instrument_app(app)
RedisInstrumentor().instrument()

redis: Redis | None = None


@app.on_event("startup")
async def on_startup() -> None:
    global redis
    redis = Redis.from_url("redis://redis:6379/0", decode_responses=True)
    app.state.redis = redis

    # Fire-and-forget background task
    asyncio.create_task(start_stream_processor(app.state.redis))


@app.on_event("shutdown")
async def on_shutdown() -> None:
    if redis:
        await redis.close()


@app.get("/healthz")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    return Response(content=get_metrics(), media_type=get_content_type())
