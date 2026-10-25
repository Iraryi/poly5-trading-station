"""Offline v3 account/order reference projection. Not a Polymarket client, signer or executor.

All amounts are Decimal strings. Missing data is deliberately not converted to 0.
This code demonstrates safe UI/account semantics; Codex must replace with verified adapters.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

class AccountDataError(ValueError):
    pass


def amount(raw: Optional[str]) -> Optional[Decimal]:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise AccountDataError('money/share amounts must be decimal strings or null')
    try:
        value = Decimal(raw)
    except Exception as exc:
        raise AccountDataError('invalid decimal amount') from exc
    if not value.is_finite() or value < 0:
        raise AccountDataError('amounts must be finite and non-negative')
    return value


@dataclass(frozen=True)
class AccountSnapshot:
    account_id: str
    mode: str
    cash: Optional[Decimal]
    available_to_buy: Optional[Decimal]
    reserved: Optional[Decimal]
    position_value: Optional[Decimal]
    observed_at_utc: str
    cash_verified: bool
    available_verified: bool
    position_value_verified: bool
    comparable_sources: bool = False

    def __post_init__(self):
        if self.mode not in ('monitor', 'paper', 'replay', 'live'):
            raise AccountDataError('invalid account mode')
        for key in ('cash', 'available_to_buy', 'reserved', 'position_value'):
            value = getattr(self, key)
            if value is not None and (not isinstance(value, Decimal) or not value.is_finite() or value < 0):
                raise AccountDataError(f'{key} must be non-negative Decimal or null')

    @property
    def displayed_cash(self) -> Optional[Decimal]:
        return self.cash if self.cash_verified else None

    @property
    def displayed_available(self) -> Optional[Decimal]:
        return self.available_to_buy if self.available_verified else None

    @property
    def displayed_position_value(self) -> Optional[Decimal]:
        return self.position_value if self.position_value_verified else None

    @property
    def estimated_equity(self) -> Optional[Decimal]:
        """Conservative: only show when explicitly marked cross-source comparable.

        Never add 'reserved' again: reserved orders may already be included in cash.
        """
        if not self.comparable_sources:
            return None
        if self.displayed_cash is None or self.displayed_position_value is None:
            return None
        return self.displayed_cash + self.displayed_position_value


@dataclass(frozen=True)
class OrderUpdate:
    event_id: str
    account_id: str
    order_id: str
    status: str
    cumulative_filled_shares: Decimal
    total_requested_shares: Decimal
    # WS/REST merge uses actual provider timestamps and confirmation; this
    # simplified projection does NOT assert exchange-global event order.
    source: str

    def __post_init__(self):
        if not self.event_id or not self.account_id or not self.order_id:
            raise AccountDataError('missing order identity')
        if self.status not in ('submitted', 'open', 'partial', 'filled', 'canceled', 'rejected', 'unknown'):
            raise AccountDataError('unsupported order status')
        if self.source not in ('rest', 'user_ws', 'replay'):
            raise AccountDataError('unexpected source')
        if self.total_requested_shares <= 0 or self.cumulative_filled_shares < 0 or self.cumulative_filled_shares > self.total_requested_shares:
            raise AccountDataError('inconsistent share totals')


@dataclass
class OrderProjection:
    account_id: str
    order_id: str
    requested_shares: Optional[Decimal] = None
    cumulative_filled: Decimal = Decimal('0')
    status: str = 'unknown'
    seen_event_ids: set[str] = field(default_factory=set)

    def apply(self, update: OrderUpdate) -> bool:
        if update.account_id != self.account_id or update.order_id != self.order_id:
            raise AccountDataError('cross-account / cross-order event denied')
        if update.event_id in self.seen_event_ids:
            return False
        if self.requested_shares is not None and update.total_requested_shares != self.requested_shares:
            raise AccountDataError('requested size changed for same order id')
        self.requested_shares = update.total_requested_shares
        self.seen_event_ids.add(update.event_id)
        self.cumulative_filled = max(self.cumulative_filled, update.cumulative_filled_shares)
        if self.cumulative_filled == self.requested_shares:
            self.status = 'filled'
        elif update.status in ('canceled', 'rejected') and self.status != 'filled':
            self.status = update.status
        elif self.status in ('canceled', 'rejected'):
            # REST/WS arrivals can be out of order; never revert terminal status
            pass
        elif self.cumulative_filled > 0:
            self.status = 'partial'
        elif update.status != 'unknown' or self.status == 'unknown':
            self.status = update.status
        return True

    @property
    def unfilled_shares(self) -> Optional[Decimal]:
        if self.requested_shares is None:
            return None
        return self.requested_shares - self.cumulative_filled

    @property
    def live_order(self) -> bool:
        return self.status in ('submitted', 'open', 'partial', 'unknown')


def can_submit_live(*, user_explicitly_enabled: bool, geo_allowed: bool,
                    authenticated: bool, balances_verified: bool,
                    order_state_known: bool, market_fresh: bool,
                    risk_checks_pass: bool, adapter_verified: bool) -> bool:
    """Safety gate. This function never sends an order."""
    return all((user_explicitly_enabled, geo_allowed, authenticated,
                balances_verified, order_state_known, market_fresh,
                risk_checks_pass, adapter_verified))
