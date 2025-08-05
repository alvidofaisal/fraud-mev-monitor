"""Prometheus metrics for the fraud monitor."""

from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
import time
from typing import Dict, Any

# Metrics
tx_processed_total = Counter(
    "tx_processed_total", "Total transactions processed", ["tx_type"]
)

alerts_total = Counter("alerts_total", "Total alerts generated", ["rule"])

rule_evaluation_duration = Histogram(
    "rule_evaluation_duration_seconds",
    "Time spent evaluating rules",
    ["rule", "result"],
)


def get_metrics() -> str:
    """Return Prometheus metrics in text format."""
    return generate_latest()


def get_content_type() -> str:
    """Return the content type for Prometheus metrics."""
    return CONTENT_TYPE_LATEST


class MetricsTimer:
    """Context manager for timing operations."""

    def __init__(self, histogram: Histogram, labels: Dict[str, Any]):
        self.histogram = histogram
        self.labels = labels
        self.start_time = None

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.start_time:
            duration = time.time() - self.start_time
            self.histogram.labels(**self.labels).observe(duration)
