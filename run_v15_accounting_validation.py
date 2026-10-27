"""Launch an isolated SAM EDGE V15 paper validation cohort.

This launcher never submits exchange orders and disables Telegram delivery.
It uses separate state/journal files so it cannot overwrite the historical
V15 journal or the existing paper engine state.
"""
from __future__ import annotations

import os
import runpy


def configure(key: str, env_key: str, default: str) -> None:
    os.environ[key] = os.getenv(env_key, default)


# Separate state and journal files: preserve the existing V15 records.
configure("SAM_EDGE_STATE_FILE", "SAM_EDGE_VALIDATION_STATE_FILE", "paper_v15_accounting_validation_state.json")
configure("SAM_EDGE_STATE_BACKUP_FILE", "SAM_EDGE_VALIDATION_STATE_BACKUP_FILE", "paper_v15_accounting_validation_state.backup.json")
configure("SAM_EDGE_JOURNAL_FILE", "SAM_EDGE_VALIDATION_JOURNAL_FILE", "paper_v15_accounting_validation_trades.csv")
configure("SAM_EDGE_SIGNAL_HISTORY_FILE", "SAM_EDGE_VALIDATION_SIGNAL_HISTORY_FILE", "paper_v15_accounting_validation_signals.json")
configure("SAM_EDGE_UNIVERSE_FILE", "SAM_EDGE_VALIDATION_UNIVERSE_FILE", "paper_v15_accounting_validation_universe.json")
configure("SAM_EDGE_SHADOW_E_FILE", "SAM_EDGE_VALIDATION_SHADOW_FILE", "v15_accounting_validation_shadow.csv")
configure("FORWARD_MASTER_JOURNAL", "SAM_EDGE_VALIDATION_MASTER_JOURNAL", "v15_accounting_validation_master_journal.csv")
configure("FORWARD_MASTER_STATE", "SAM_EDGE_VALIDATION_MASTER_STATE", "v15_accounting_validation_master_state.json")
configure("FORWARD_ONGOING_JOURNAL", "SAM_EDGE_VALIDATION_ONGOING_JOURNAL", "v15_accounting_validation_ongoing.csv")
configure("FORWARD_MASTER_SUMMARY", "SAM_EDGE_VALIDATION_SUMMARY", "v15_accounting_validation_summary.json")
os.environ["FORWARD_BASELINE_TRADE_NO"] = os.getenv("SAM_EDGE_VALIDATION_BASELINE_TRADE_NO", "0")
os.environ["FORWARD_TARGET_NEW_TRADES"] = os.getenv("SAM_EDGE_VALIDATION_TARGET_TRADES", "100")

# Approximate USDT equivalent of a Rp500,000 account. Override explicitly if
# the current exchange rate or available trading balance differs.
os.environ.setdefault("START_EQUITY", os.getenv("SAM_EDGE_VALIDATION_START_EQUITY", "30"))
os.environ.setdefault("RISK_PCT", os.getenv("SAM_EDGE_VALIDATION_RISK_PCT", "0.01"))
os.environ.setdefault("MAX_PORTFOLIO_RISK_PCT", "0.02")
os.environ.setdefault("FEE_RATE_ENTRY", "0.0005")
os.environ.setdefault("FEE_RATE_EXIT", "0.0005")
os.environ.setdefault("V15_PRECISION_MODE", "1")
os.environ.setdefault("V15_FAILURE_SHIELD_ENABLED", "0")
os.environ["TELEGRAM_SIGNAL_ENABLED"] = "0"
os.environ["RUN_ONCE"] = "0"
os.environ.setdefault("SAM_EDGE_BUILD", "V15-ACCOUNTING-VALIDATION")

# Prevent result notifications from leaving the validation environment.
import telegram_result_guard
telegram_result_guard.send_result = lambda *args, **kwargs: False

print("SAM EDGE V15 ACCOUNTING VALIDATION | PAPER ONLY | NO EXCHANGE ORDERS | TELEGRAM OFF")
print(f"START_EQUITY={os.environ['START_EQUITY']} USDT | RISK_PCT={os.environ['RISK_PCT']}")
print(f"PORTFOLIO_RISK_CAP={os.environ['MAX_PORTFOLIO_RISK_PCT']} | FEES={os.environ['FEE_RATE_ENTRY']}/{os.environ['FEE_RATE_EXIT']}")
print(f"MASTER_JOURNAL={os.environ['FORWARD_MASTER_JOURNAL']} | TARGET={os.environ['FORWARD_TARGET_NEW_TRADES']} CLOSED TRADES")

runpy.run_module("run_v15_forward_test_pro", run_name="__main__")
