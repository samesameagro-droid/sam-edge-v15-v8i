"""Pure, testable trade PnL accounting for SAM EDGE paper simulations.

Fees default to the published BingX perpetual-futures taker rate when called by
main_paper_v15.py. Funding and slippage are intentionally not guessed here.
"""
from math import isfinite


def calculate_trade_pnl(
    *,
    entry: float,
    sl: float,
    exit_price: float,
    risk_cash: float,
    side: str,
    fee_rate_entry: float = 0.0005,
    fee_rate_exit: float = 0.0005,
) -> dict[str, float]:
    """Return gross/net PnL using the risk amount fixed at entry.

    Position quantity is sized so the stop distance equals risk_cash. Fee costs
    are estimated on both entry and exit notionals. Actual funding, slippage,
    partial fills, and liquidation effects are outside this model.
    """
    values = (entry, sl, exit_price, risk_cash, fee_rate_entry, fee_rate_exit)
    if not all(isfinite(float(v)) for v in values):
        raise ValueError("all inputs must be finite numbers")
    if risk_cash <= 0:
        raise ValueError("risk_cash must be positive")
    if abs(entry - sl) <= 0:
        raise ValueError("entry and stop must differ")
    if fee_rate_entry < 0 or fee_rate_exit < 0:
        raise ValueError("fee rates cannot be negative")

    direction = str(side).strip().upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("side must be LONG or SHORT")

    quantity = risk_cash / abs(entry - sl)
    gross_pnl = quantity * (exit_price - entry) if direction == "LONG" else quantity * (entry - exit_price)
    entry_notional = quantity * abs(entry)
    exit_notional = quantity * abs(exit_price)
    fee_cash = entry_notional * fee_rate_entry + exit_notional * fee_rate_exit
    net_pnl = gross_pnl - fee_cash
    gross_r = gross_pnl / risk_cash
    net_r = net_pnl / risk_cash
    return {
        "quantity": quantity,
        "entry_notional": entry_notional,
        "exit_notional": exit_notional,
        "gross_pnl": gross_pnl,
        "fee_cash": fee_cash,
        "net_pnl": net_pnl,
        "gross_R": gross_r,
        "net_R": net_r,
    }


def risk_budget_allows(*, open_risk_cash: float, selected_risk_cash: float, candidate_risk_cash: float, equity: float, max_portfolio_risk_pct: float) -> bool:
    """Whether adding a candidate keeps aggregate open stop-risk within budget."""
    values = (open_risk_cash, selected_risk_cash, candidate_risk_cash, equity, max_portfolio_risk_pct)
    if not all(isfinite(float(v)) for v in values):
        return False
    if equity <= 0 or max_portfolio_risk_pct < 0:
        return False
    total_risk = max(0.0, open_risk_cash) + max(0.0, selected_risk_cash) + max(0.0, candidate_risk_cash)
    return total_risk <= equity * max_portfolio_risk_pct + 1e-9
