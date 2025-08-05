from redis.asyncio import Redis
import structlog
from opentelemetry import trace
import hashlib
import time

from app.metrics import alerts_total, rule_evaluation_duration, MetricsTimer

RULE_ID_SUSPICIOUS_APPROVAL = "suspicious_approval"
RULE_ID_SANDWICH_RISK = "sandwich_risk"
RULE_ID_ANOMALOUS_TRANSFER = "anomalous_transfer"

logger = structlog.get_logger()
tracer = trace.get_tracer(__name__)

# Risky token addresses (mock data)
RISKY_TOKENS = {
    "0x1234567890abcdef1234567890abcdef12345678",  # Mock risky token
    "0xabcdef1234567890abcdef1234567890abcdef12",  # Another mock risky token
}

# Large allowance threshold (in wei, ~1000 ETH equivalent)
LARGE_ALLOWANCE_THRESHOLD = 1000 * 10**18


async def evaluate_rules(tx: dict, redis: Redis) -> None:
    """Run all rule functions against a transaction."""
    with tracer.start_as_current_span("evaluate_rules") as span:
        span.set_attribute("tx.hash", tx.get("hash", ""))
        span.set_attribute("tx.type", tx.get("type", ""))

        # Run all rules in parallel for better performance
        await _approval_rule(tx, redis)
        await _sandwich_risk_rule(tx, redis)
        await _anomalous_transfer_rule(tx, redis)


async def _approval_rule(tx: dict, redis: Redis) -> None:
    """Fire an alert when an unusually large approval is seen."""
    if tx["type"] != "approve":
        return

    with tracer.start_as_current_span("approval_rule") as span:
        span.set_attribute("rule.id", RULE_ID_SUSPICIOUS_APPROVAL)

        with MetricsTimer(
            rule_evaluation_duration,
            {"rule": RULE_ID_SUSPICIOUS_APPROVAL, "result": "evaluated"},
        ):
            alert_id = _generate_alert_id(RULE_ID_SUSPICIOUS_APPROVAL, tx["hash"])

            # Idempotency key – avoid flooding
            exists = await redis.get(alert_id)
            if exists:
                span.set_attribute("result", "duplicate")
                return

            # Enhanced logic: check for large allowances and risky tokens
            should_alert = False
            reasons = []

            # Check for large allowance
            allowance = tx.get("allowance", 0)
            if allowance > LARGE_ALLOWANCE_THRESHOLD:
                should_alert = True
                reasons.append(f"large_allowance:{allowance}")

            # Check for risky token
            token_address = tx.get("token_address", "")
            if token_address.lower() in {addr.lower() for addr in RISKY_TOKENS}:
                should_alert = True
                reasons.append(f"risky_token:{token_address}")

            if should_alert:
                alert_msg = f"[ALERT] Suspicious approval tx {tx['hash']} - {', '.join(reasons)}"
                logger.warning(
                    alert_msg,
                    trace_id=span.get_span_context().trace_id,
                    rule_id=RULE_ID_SUSPICIOUS_APPROVAL,
                    tx_hash=tx["hash"],
                    reasons=reasons,
                )

                alerts_total.labels(rule=RULE_ID_SUSPICIOUS_APPROVAL).inc()
                await redis.set(alert_id, "1", ex=60)  # suppress for 60 s
                span.set_attribute("result", "alert_fired")
            else:
                span.set_attribute("result", "no_alert")


async def _sandwich_risk_rule(tx: dict, redis: Redis) -> None:
    """Detect potential sandwich attack patterns."""
    if tx["type"] != "swap":
        return

    with tracer.start_as_current_span("sandwich_risk_rule") as span:
        span.set_attribute("rule.id", RULE_ID_SANDWICH_RISK)

        with MetricsTimer(
            rule_evaluation_duration,
            {"rule": RULE_ID_SANDWICH_RISK, "result": "evaluated"},
        ):
            # Look for complementary swaps within a time window
            token_pair = tx.get("token_pair", "")
            if not token_pair:
                return

            swap_key = f"swaps:{token_pair}"
            window_seconds = 30

            # Store this swap with timestamp
            current_time = int(time.time())
            swap_data = f"{tx['hash']}:{tx.get('direction', '')}:{current_time}"

            # Add to sorted set for time-based queries
            await redis.zadd(swap_key, {swap_data: current_time})
            await redis.expire(swap_key, window_seconds)

            # Get recent swaps for this pair
            recent_swaps = await redis.zrangebyscore(
                swap_key, current_time - window_seconds, current_time
            )

            # Analyze for sandwich pattern
            if len(recent_swaps) >= 3:
                # Simple heuristic: if we have 3+ swaps in the same pair within window
                # and they alternate direction, flag potential sandwich
                directions = [
                    swap.split(":")[1] for swap in recent_swaps if ":" in swap
                ]

                if len(set(directions)) > 1:  # Multiple directions present
                    alert_id = _generate_alert_id(RULE_ID_SANDWICH_RISK, tx["hash"])

                    exists = await redis.get(alert_id)
                    if not exists:
                        alert_msg = f"[ALERT] Potential sandwich attack pattern tx {tx['hash']} - {len(recent_swaps)} swaps in {window_seconds}s"
                        logger.warning(
                            alert_msg,
                            trace_id=span.get_span_context().trace_id,
                            rule_id=RULE_ID_SANDWICH_RISK,
                            tx_hash=tx["hash"],
                            swap_count=len(recent_swaps),
                        )

                        alerts_total.labels(rule=RULE_ID_SANDWICH_RISK).inc()
                        await redis.set(alert_id, "1", ex=60)
                        span.set_attribute("result", "alert_fired")
                        return

            span.set_attribute("result", "no_alert")


async def _anomalous_transfer_rule(tx: dict, redis: Redis) -> None:
    """Detect anomalous transfer patterns (fan-out to many recipients)."""
    if tx["type"] != "transfer":
        return

    with tracer.start_as_current_span("anomalous_transfer_rule") as span:
        span.set_attribute("rule.id", RULE_ID_ANOMALOUS_TRANSFER)

        with MetricsTimer(
            rule_evaluation_duration,
            {"rule": RULE_ID_ANOMALOUS_TRANSFER, "result": "evaluated"},
        ):
            sender = tx.get("from", "")
            recipient = tx.get("to", "")

            if not sender or not recipient:
                return

            # Track unique recipients for each sender in a 5-second window
            sender_key = f"transfers:{sender}"
            window_seconds = 5
            max_recipients = 10  # Threshold for anomalous fan-out

            # Add recipient to sender's recent recipients
            await redis.sadd(sender_key, recipient)
            await redis.expire(sender_key, window_seconds)

            # Count unique recipients
            recipient_count = await redis.scard(sender_key)

            if recipient_count > max_recipients:
                alert_id = _generate_alert_id(RULE_ID_ANOMALOUS_TRANSFER, tx["hash"])

                exists = await redis.get(alert_id)
                if not exists:
                    alert_msg = f"[ALERT] Anomalous transfer pattern tx {tx['hash']} - {recipient_count} unique recipients in {window_seconds}s"
                    logger.warning(
                        alert_msg,
                        trace_id=span.get_span_context().trace_id,
                        rule_id=RULE_ID_ANOMALOUS_TRANSFER,
                        tx_hash=tx["hash"],
                        sender=sender,
                        recipient_count=recipient_count,
                    )

                    alerts_total.labels(rule=RULE_ID_ANOMALOUS_TRANSFER).inc()
                    await redis.set(alert_id, "1", ex=60)
                    span.set_attribute("result", "alert_fired")
                    return

            span.set_attribute("result", "no_alert")


def _generate_alert_id(rule_id: str, tx_hash: str) -> str:
    """Generate a deterministic alert ID for idempotency."""
    return hashlib.sha256(f"{rule_id}:{tx_hash}".encode()).hexdigest()[:16]
