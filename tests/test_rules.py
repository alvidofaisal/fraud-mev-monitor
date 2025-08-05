"""Unit tests for fraud detection rules."""

import pytest
from unittest.mock import AsyncMock
from redis.asyncio import Redis

from app.processor.rules import (
    _approval_rule,
    _sandwich_risk_rule,
    _anomalous_transfer_rule,
    evaluate_rules,
    LARGE_ALLOWANCE_THRESHOLD,
    RISKY_TOKENS,
    _generate_alert_id,
)


@pytest.fixture
def mock_redis():
    """Create a mock Redis instance."""
    redis = AsyncMock(spec=Redis)
    # Set up async method return values
    redis.get = AsyncMock(return_value=None)  # No existing alerts by default
    redis.set = AsyncMock(return_value=True)
    redis.zadd = AsyncMock(return_value=1)
    redis.zrangebyscore = AsyncMock(return_value=[])
    redis.expire = AsyncMock(return_value=True)
    redis.sadd = AsyncMock(return_value=1)
    redis.scard = AsyncMock(return_value=1)
    return redis


class TestApprovalRule:
    """Tests for the suspicious approval rule."""

    @pytest.mark.asyncio
    async def test_ignores_non_approve_transactions(self, mock_redis):
        """Should ignore transactions that are not approvals."""
        tx = {"type": "transfer", "hash": "0x123"}

        await _approval_rule(tx, mock_redis)

        mock_redis.get.assert_not_called()
        mock_redis.set.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_duplicate_alerts(self, mock_redis):
        """Should skip alerts that already exist (idempotency)."""
        mock_redis.get.return_value = "1"  # Alert already exists

        tx = {
            "type": "approve",
            "hash": "0x123",
            "allowance": LARGE_ALLOWANCE_THRESHOLD + 1,
        }

        await _approval_rule(tx, mock_redis)

        mock_redis.set.assert_not_called()

    @pytest.mark.asyncio
    async def test_alerts_on_large_allowance(self, mock_redis):
        """Should alert when allowance exceeds threshold."""
        tx = {
            "type": "approve",
            "hash": "0x123",
            "allowance": LARGE_ALLOWANCE_THRESHOLD + 1,
            "token_address": "0xsafe_token",
        }

        await _approval_rule(tx, mock_redis)

        mock_redis.set.assert_called_once()
        call_args = mock_redis.set.call_args
        assert call_args[0][1] == "1"  # Value
        assert call_args[1]["ex"] == 60  # TTL

    @pytest.mark.asyncio
    async def test_alerts_on_risky_token(self, mock_redis):
        """Should alert when token is in risky list."""
        risky_token = list(RISKY_TOKENS)[0]

        tx = {
            "type": "approve",
            "hash": "0x123",
            "allowance": 100,  # Small allowance
            "token_address": risky_token,
        }

        await _approval_rule(tx, mock_redis)

        mock_redis.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_alerts_on_both_conditions(self, mock_redis):
        """Should alert when both large allowance and risky token."""
        risky_token = list(RISKY_TOKENS)[0]

        tx = {
            "type": "approve",
            "hash": "0x123",
            "allowance": LARGE_ALLOWANCE_THRESHOLD + 1,
            "token_address": risky_token,
        }

        await _approval_rule(tx, mock_redis)

        mock_redis.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_alert_on_safe_conditions(self, mock_redis):
        """Should not alert when conditions are safe."""
        tx = {
            "type": "approve",
            "hash": "0x123",
            "allowance": 100,  # Small allowance
            "token_address": "0xsafe_token",
        }

        await _approval_rule(tx, mock_redis)

        mock_redis.set.assert_not_called()


class TestSandwichRiskRule:
    """Tests for the sandwich risk detection rule."""

    @pytest.mark.asyncio
    async def test_ignores_non_swap_transactions(self, mock_redis):
        """Should ignore transactions that are not swaps."""
        tx = {"type": "approve", "hash": "0x123"}

        await _sandwich_risk_rule(tx, mock_redis)

        mock_redis.zadd.assert_not_called()

    @pytest.mark.asyncio
    async def test_stores_swap_data(self, mock_redis):
        """Should store swap data in Redis sorted set."""
        tx = {
            "type": "swap",
            "hash": "0x123",
            "token_pair": "WETH/USDC",
            "direction": "buy",
        }

        await _sandwich_risk_rule(tx, mock_redis)

        mock_redis.zadd.assert_called_once()
        mock_redis.expire.assert_called_once()

    @pytest.mark.asyncio
    async def test_alerts_on_sandwich_pattern(self, mock_redis):
        """Should alert when sandwich pattern is detected."""
        # Mock multiple swaps with different directions
        mock_redis.zrangebyscore.return_value = [
            "0x111:buy:1234567890",
            "0x222:sell:1234567891",
            "0x333:buy:1234567892",
        ]

        tx = {
            "type": "swap",
            "hash": "0x123",
            "token_pair": "WETH/USDC",
            "direction": "sell",
        }

        await _sandwich_risk_rule(tx, mock_redis)

        mock_redis.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_alert_with_insufficient_swaps(self, mock_redis):
        """Should not alert with insufficient swap count."""
        mock_redis.zrangebyscore.return_value = ["0x111:buy:1234567890"]

        tx = {
            "type": "swap",
            "hash": "0x123",
            "token_pair": "WETH/USDC",
            "direction": "sell",
        }

        await _sandwich_risk_rule(tx, mock_redis)

        mock_redis.set.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_alert_with_same_direction(self, mock_redis):
        """Should not alert when all swaps are same direction."""
        mock_redis.zrangebyscore.return_value = [
            "0x111:buy:1234567890",
            "0x222:buy:1234567891",
            "0x333:buy:1234567892",
        ]

        tx = {
            "type": "swap",
            "hash": "0x123",
            "token_pair": "WETH/USDC",
            "direction": "buy",
        }

        await _sandwich_risk_rule(tx, mock_redis)

        mock_redis.set.assert_not_called()


class TestAnomalousTransferRule:
    """Tests for the anomalous transfer detection rule."""

    @pytest.mark.asyncio
    async def test_ignores_non_transfer_transactions(self, mock_redis):
        """Should ignore transactions that are not transfers."""
        tx = {"type": "swap", "hash": "0x123"}

        await _anomalous_transfer_rule(tx, mock_redis)

        mock_redis.sadd.assert_not_called()

    @pytest.mark.asyncio
    async def test_tracks_recipients(self, mock_redis):
        """Should track recipients for sender."""
        tx = {
            "type": "transfer",
            "hash": "0x123",
            "from": "0xsender",
            "to": "0xrecipient",
        }

        await _anomalous_transfer_rule(tx, mock_redis)

        mock_redis.sadd.assert_called_once_with("transfers:0xsender", "0xrecipient")
        mock_redis.expire.assert_called_once()

    @pytest.mark.asyncio
    async def test_alerts_on_fan_out_pattern(self, mock_redis):
        """Should alert when sender has too many unique recipients."""
        mock_redis.scard.return_value = 15  # Above threshold (10)

        tx = {
            "type": "transfer",
            "hash": "0x123",
            "from": "0xsender",
            "to": "0xrecipient",
        }

        await _anomalous_transfer_rule(tx, mock_redis)

        mock_redis.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_alert_with_low_recipient_count(self, mock_redis):
        """Should not alert with low recipient count."""
        mock_redis.scard.return_value = 5  # Below threshold

        tx = {
            "type": "transfer",
            "hash": "0x123",
            "from": "0xsender",
            "to": "0xrecipient",
        }

        await _anomalous_transfer_rule(tx, mock_redis)

        mock_redis.set.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_missing_addresses(self, mock_redis):
        """Should handle transactions with missing from/to addresses."""
        tx = {
            "type": "transfer",
            "hash": "0x123",
            # Missing from/to addresses
        }

        await _anomalous_transfer_rule(tx, mock_redis)

        mock_redis.sadd.assert_not_called()


class TestEvaluateRules:
    """Tests for the main rule evaluation function."""

    @pytest.mark.asyncio
    async def test_runs_all_rules(self, mock_redis):
        """Should run all rules for a transaction."""
        tx = {
            "type": "approve",
            "hash": "0x123",
            "allowance": LARGE_ALLOWANCE_THRESHOLD + 1,
            "token_address": list(RISKY_TOKENS)[0],
        }

        await evaluate_rules(tx, mock_redis)

        # Should have called Redis operations for the approval rule
        mock_redis.get.assert_called()
        mock_redis.set.assert_called()


class TestUtilityFunctions:
    """Tests for utility functions."""

    def test_generate_alert_id(self):
        """Should generate consistent alert IDs."""
        rule_id = "test_rule"
        tx_hash = "0x123"

        alert_id1 = _generate_alert_id(rule_id, tx_hash)
        alert_id2 = _generate_alert_id(rule_id, tx_hash)

        assert alert_id1 == alert_id2
        assert len(alert_id1) == 16  # Truncated to 16 chars

        # Different inputs should produce different IDs
        alert_id3 = _generate_alert_id("different_rule", tx_hash)
        assert alert_id1 != alert_id3
