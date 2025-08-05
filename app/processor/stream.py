import asyncio
import random
from typing import AsyncIterator
import structlog
from opentelemetry import trace

from redis.asyncio import Redis

from app.processor.rules import evaluate_rules
from app.metrics import tx_processed_total

MOCK_TX_TYPES = ["swap", "approve", "transfer"]

logger = structlog.get_logger()
tracer = trace.get_tracer(__name__)

# Mock data for more realistic transactions


async def mock_mempool_feed() -> AsyncIterator[dict]:
    """Yields fake mempool transactions indefinitely."""
    mock_addresses = [
        "0x742637a99b4b5a1a7c0b7c6b4ae6e8b8b73c7d8e",
        "0xa1b2c3d4e5f6789012345678901234567890abcd",
        "0xdeadbeefcafebabe1234567890123456789012ef",
        "0x1234567890abcdef1234567890abcdef12345678",  # This is in RISKY_TOKENS
    ]

    mock_token_pairs = ["WETH/USDC", "WETH/DAI", "USDC/DAI", "WBTC/WETH"]

    while True:
        tx_type = random.choice(MOCK_TX_TYPES)
        base_tx = {
            "hash": f"0x{random.getrandbits(256):064x}",
            "type": tx_type,
            "value": random.uniform(0.01, 100),
            "from": random.choice(mock_addresses),
            "to": random.choice(mock_addresses),
        }

        # Add type-specific fields
        if tx_type == "approve":
            base_tx.update(
                {
                    "token_address": random.choice(mock_addresses),
                    "allowance": random.choice(
                        [
                            random.uniform(1, 1000),  # Normal allowance
                            random.uniform(1000, 100000)
                            * 10**18,  # Large allowance (triggers alert)
                        ]
                    ),
                }
            )
        elif tx_type == "swap":
            base_tx.update(
                {
                    "token_pair": random.choice(mock_token_pairs),
                    "direction": random.choice(["buy", "sell"]),
                    "amount_in": random.uniform(0.1, 10),
                    "amount_out": random.uniform(0.1, 10),
                }
            )
        elif tx_type == "transfer":
            # Occasionally create fan-out patterns for anomalous transfer detection
            if random.random() < 0.05:  # 5% chance of fan-out
                base_tx[
                    "to"
                ] = f"0x{random.getrandbits(160):040x}"  # Random new address

        yield base_tx
        await asyncio.sleep(0.05)  # 20 tx/s


async def start_stream_processor(redis: Redis) -> None:
    """Consume the mempool feed and pass each tx through the rule engine."""
    logger.info("Starting stream processor")

    with tracer.start_as_current_span("stream_processor"):
        async for tx in mock_mempool_feed():
            with tracer.start_as_current_span("process_transaction") as span:
                span.set_attribute("tx.hash", tx["hash"])
                span.set_attribute("tx.type", tx["type"])

                try:
                    await evaluate_rules(tx, redis)
                    tx_processed_total.labels(tx_type=tx["type"]).inc()
                    span.set_attribute("status", "success")
                except Exception as e:
                    logger.error(
                        "Error processing transaction", tx_hash=tx["hash"], error=str(e)
                    )
                    span.set_attribute("status", "error")
                    span.set_attribute("error", str(e))
