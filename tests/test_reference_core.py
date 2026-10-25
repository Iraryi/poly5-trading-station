import sys
import unittest
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reference_core import (
    BidLevel, DataInvalid, FeeSchedule, Fill, InsufficientBidDepth,
    Phase, buy_basis, dec, expected_hold_roi, fee_for_fill,
    market_start_utc, phase_at, project_full_exit,
)


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.fees = FeeSchedule(rate=D("0.07"), exponent=1)

    def test_five_dollar_trade_exit_fee_adjusted(self):
        # Illustrative buy notional $5 PLUS entry fee. Do not count as a $5 total cap.
        result = project_full_exit(
            [Fill(D("0.40"), D("12.5"))],
            [BidLevel(D("0.50"), D("12.5"))], self.fees
        )
        self.assertEqual(result.spent, D("5.000"))
        self.assertEqual(result.entry_fee, D("0.210000"))
        self.assertEqual(result.proceeds, D("6.250"))
        self.assertEqual(result.estimated_exit_fee, D("0.2187500"))
        self.assertEqual(result.net_pnl, D("0.8212500"))
        self.assertAlmostEqual(float(result.net_roi * 100), 15.76295585412668)

    def test_deep_bid_sweep_not_just_best_bid(self):
        result = project_full_exit(
            [Fill(D("0.40"), D("10"))],
            [BidLevel(D("0.50"), D("2")), BidLevel(D("0.47"), D("8"))],
            self.fees
        )
        self.assertEqual(result.proceeds, D("4.76"))
        self.assertNotEqual(result.proceeds, D("5.0"))

    def test_unfillable_not_fake_profit(self):
        with self.assertRaises(InsufficientBidDepth):
            project_full_exit(
                [Fill(D("0.40"), D("10"))],
                [BidLevel(D("0.99"), D("2"))], self.fees,
            )

    def test_maker_fee_zero(self):
        self.assertEqual(fee_for_fill(Fill(D("0.5"), D("10"), False), self.fees), D("0"))

    def test_required_winning_probability_above_price_due_to_fee(self):
        entry = [Fill(D("0.5"), D("10"))]
        self.assertLess(expected_hold_roi(entry, self.fees, D("0.50")), 0)
        self.assertGreater(expected_hold_roi(entry, self.fees, D("0.53")), 0)

    def test_decimal_float_refused(self):
        with self.assertRaises(TypeError):
            dec(0.1)

    def test_phases_exact_boundaries(self):
        base = 1_800_000_000  # divisible by 300, fake UTC seconds
        base = market_start_utc(base)
        for s, phase in [(0, Phase.EARLY), (179, Phase.EARLY),
                         (180, Phase.TRANSITION), (209, Phase.TRANSITION),
                         (210, Phase.LATE), (284, Phase.LATE),
                         (285, Phase.CLOSE_ONLY), (299, Phase.CLOSE_ONLY)]:
            self.assertEqual(phase_at(base + s), phase)
        self.assertEqual(phase_at(base+300), Phase.EARLY)

    def test_invalid_fill(self):
        with self.assertRaises(DataInvalid):
            Fill(D("1"), D("10"))
        with self.assertRaises(DataInvalid):
            Fill(D("0.40"), D("0"))

    def test_fees_if_buys_and_sells_fee_curve_symmetric(self):
        a = fee_for_fill(Fill(D("0.30"), D("10")), self.fees)
        b = fee_for_fill(Fill(D("0.70"), D("10")), self.fees)
        self.assertEqual(a,b)


if __name__ == "__main__":
    unittest.main()
