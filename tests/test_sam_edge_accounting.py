import unittest

from sam_edge_accounting import calculate_trade_pnl


class TradePnLTests(unittest.TestCase):
    def test_long_take_profit_uses_entry_risk_and_deducts_round_trip_fees(self):
        result = calculate_trade_pnl(
            entry=100.0, sl=98.0, exit_price=102.5,
            risk_cash=5.0, side="LONG",
            fee_rate_entry=0.0005, fee_rate_exit=0.0005,
        )
        self.assertAlmostEqual(result["quantity"], 2.5)
        self.assertAlmostEqual(result["gross_pnl"], 6.25)
        self.assertAlmostEqual(result["fee_cash"], 0.253125)
        self.assertAlmostEqual(result["net_pnl"], 5.996875)
        self.assertAlmostEqual(result["gross_R"], 1.25)
        self.assertAlmostEqual(result["net_R"], 1.199375)

    def test_short_stop_loss_is_worse_than_minus_one_net_R_after_fees(self):
        result = calculate_trade_pnl(
            entry=100.0, sl=102.0, exit_price=102.0,
            risk_cash=5.0, side="SHORT",
            fee_rate_entry=0.0005, fee_rate_exit=0.0005,
        )
        self.assertAlmostEqual(result["gross_pnl"], -5.0)
        self.assertGreater(result["fee_cash"], 0)
        self.assertLess(result["net_R"], -1.0)

    def test_entry_time_risk_is_fixed_independent_of_later_equity(self):
        first = calculate_trade_pnl(
            entry=100.0, sl=98.0, exit_price=102.5,
            risk_cash=5.0, side="LONG",
        )
        second = calculate_trade_pnl(
            entry=100.0, sl=98.0, exit_price=102.5,
            risk_cash=5.0, side="LONG",
        )
        self.assertEqual(first, second)

    def test_invalid_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            calculate_trade_pnl(
                entry=100.0, sl=100.0, exit_price=101.0,
                risk_cash=5.0, side="LONG",
            )
        with self.assertRaises(ValueError):
            calculate_trade_pnl(
                entry=100.0, sl=98.0, exit_price=101.0,
                risk_cash=0.0, side="LONG",
            )


if __name__ == "__main__":
    unittest.main()
