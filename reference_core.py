"""Pure offline arithmetic reference for Codex to test against.

NO networking, signing, wallets, or live order submission. This module is NOT a bot.
Dollar numbers are illustrative; all real trading needs market-specific fee schedule,
actual receipts, and explicit compliant user approval.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Sequence


D = Decimal


class DataInvalid(ValueError):
    pass


class InsufficientBidDepth(DataInvalid):
    pass


def dec(value: str | int | Decimal) -> Decimal:
    """Refuse binary floats for money to prevent unnoticed rounding."""
    if isinstance(value, float):
        raise TypeError("Float is prohibited for price/size; use Decimal or str")
    x = D(value)
    if not x.is_finite():
        raise DataInvalid("Non-finite Decimal")
    return x


@dataclass(frozen=True)
class FeeSchedule:
    rate: Decimal
    exponent: int = 1
    taker_only: bool = True

    def __post_init__(self):
        if not D("0") <= self.rate <= D("1"):
            raise DataInvalid("Unreasonable fee rate")
        if not isinstance(self.exponent, int) or self.exponent <= 0:
            raise DataInvalid("Nonpositive/unsupported fee exponent")
        if not self.taker_only:
            raise DataInvalid("This offline example handles taker-only fees; verify other schedules")


@dataclass(frozen=True)
class Fill:
    price: Decimal
    shares: Decimal
    taker: bool = True

    def __post_init__(self):
        if not D("0") < self.price < D("1"):
            raise DataInvalid("A trading price must be within (0,1)")
        if self.shares <= 0:
            raise DataInvalid("Fill shares must be >0")


@dataclass(frozen=True)
class BidLevel:
    price: Decimal
    shares: Decimal

    def __post_init__(self):
        if not D("0") < self.price < D("1") or self.shares <= 0:
            raise DataInvalid("Invalid order book level")


@dataclass(frozen=True)
class ExitProjection:
    shares: Decimal
    spent: Decimal
    entry_fee: Decimal
    proceeds: Decimal
    estimated_exit_fee: Decimal
    net_pnl: Decimal
    net_roi: Decimal
    # This is an *estimate from a snapshot*, NEVER an executed fill or realized PnL.


def fee_for_fill(fill: Fill, schedule: FeeSchedule) -> Decimal:
    """Approximate published fee curve. Exact receipts/rounding take precedence."""
    if not fill.taker:
        return D("0")
    component = fill.price * (D("1") - fill.price)
    return fill.shares * schedule.rate * component ** schedule.exponent


def buy_basis(fills: Sequence[Fill], schedule: FeeSchedule) -> tuple[Decimal, Decimal, Decimal]:
    if not fills:
        raise DataInvalid("No confirmed entry fills")
    shares = sum((f.shares for f in fills), D("0"))
    spent = sum((f.price * f.shares for f in fills), D("0"))
    fees = sum((fee_for_fill(f, schedule) for f in fills), D("0"))
    return shares, spent, fees


def project_full_exit(
    entry_fills: Sequence[Fill],
    current_bids: Sequence[BidLevel],
    schedule: FeeSchedule,
    expected_exit_taker: bool = True,
) -> ExitProjection:
    """Worst-case based on *visible* depth. Not a fill guarantee; no latency modeled.

    Bids may arrive unsorted. No fictitious sale of unavailable shares is allowed.
    """
    shares, spent, entry_fee = buy_basis(entry_fills, schedule)
    remaining = shares
    proceeds = D("0")
    exit_fee = D("0")
    for level in sorted(current_bids, key=lambda x: x.price, reverse=True):
        quantity = min(remaining, level.shares)
        if quantity <= 0:
            continue
        exit_fill = Fill(level.price, quantity, expected_exit_taker)
        proceeds += quantity * level.price
        exit_fee += fee_for_fill(exit_fill, schedule)
        remaining -= quantity
        if remaining == 0:
            break
    if remaining > 0:
        raise InsufficientBidDepth(f"Unfillable position: still {remaining} shares")
    total_basis = spent + entry_fee
    net_pnl = proceeds - exit_fee - total_basis
    return ExitProjection(shares, spent, entry_fee, proceeds, exit_fee,
                          net_pnl, net_pnl / total_basis)


def expected_hold_roi(
    entry_fills: Sequence[Fill], schedule: FeeSchedule, estimated_win_probability: Decimal
) -> Decimal:
    """Illustrative expectation if winners pay 1/share, losers pay 0/share."""
    p = dec(estimated_win_probability)
    if not D("0") <= p <= D("1"):
        raise DataInvalid("Probability outside [0,1]")
    shares, spent, entry_fee = buy_basis(entry_fills, schedule)
    basis = spent + entry_fee
    return (p * shares - basis) / basis


class Phase(str, Enum):
    EARLY = "early_roi_research"
    TRANSITION = "transition_manage_position"
    LATE = "late_direction_research"
    CLOSE_ONLY = "close_only"


def market_start_utc(epoch_second: int) -> int:
    if not isinstance(epoch_second, int) or epoch_second < 0:
        raise DataInvalid("Epoch must be a nonnegative integer UTC second")
    return epoch_second // 300 * 300


def phase_at(epoch_second: int) -> Phase:
    elapsed = epoch_second - market_start_utc(epoch_second)
    if elapsed < 180:
        return Phase.EARLY
    if elapsed < 210:
        return Phase.TRANSITION
    if elapsed < 285:
        return Phase.LATE
    return Phase.CLOSE_ONLY
