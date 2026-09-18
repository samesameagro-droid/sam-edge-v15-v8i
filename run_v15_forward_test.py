from __future__ import annotations

import csv
import json
from pathlib import Path

import main_paper_v15 as base

# Forward-test policy:
# - scanner still scans the whole eligible universe;
# - a signal becomes a trade when it passes the SAME 9 V15 gates used by signal_mask();
# - SCORE is informational/ranking only and is NOT an execution threshold;
# - Trade # is a MASTER sequence across all coins and is assigned when a trade closes;
# - selected/closed trades count toward the 20-trade target;
# - waitlist signals do not count because they were not executed by the paper engine;
# - preserve the main paper engine's normal multi-position capacity (default 5).
base.MAX_ACTIVE = int(__import__('os').getenv('FORWARD_MAX_ACTIVE', '5'))

MASTER_JOURNAL = Path("v15_forward_test_master_journal.csv")
MASTER_STATE = Path("v15_forward_test_master_state.json")
MASTER_SUMMARY = Path("v15_forward_test_summary.json")
TARGET_TRADES = 20

FIELDS = [
    "trade_no", "trade_key", "signal_time", "coin", "side", "core", "score",
    "entry", "sl", "tp", "closed_at", "exit", "result", "R", "equity_after",
    "win_loss", "win_rate_pct", "net_R", "profit_factor", "drawdown_R",
]


class ForwardPaperEngine(base.PaperEngine):
    def __init__(self):
        self.candidate_meta: dict[str, dict] = {}
        self.master_rows: list[dict] = []
        self.master_bootstrapped = False
        super().__init__()
        self._load_master()
        self._migrate_legacy_active_positions()
        self._refresh_summary_fields()
        self._write_master() if self.master_rows else None
        self._write_summary()
        print(
            f"FORWARD TEST | completed={len(self.master_rows)}/{TARGET_TRADES} "
            f"| active={len(self.positions)} | max_active={base.MAX_ACTIVE} "
            f"| policy=9_GATES_NO_SCORE_THRESHOLD"
        )

    @staticmethod
    def _trade_key_from_record(r):
        return f"{r.get('coin', '')}|{r.get('side', '')}|{r.get('opened_at', '')}|{r.get('core', '')}"

    def _append_closed_record(self, r, trade_no):
        result = str(r.get("result", "")).upper().strip()
        if result not in {"TP", "SL"} or not self._valid_closed_record({**r, "signal_time": r.get("opened_at", "")}):
            return
        self.master_rows.append({
            "trade_no": str(trade_no),
            "trade_key": self._trade_key_from_record(r),
            "signal_time": r.get("opened_at", ""),
            "coin": r.get("coin", ""),
            "side": r.get("side", ""),
            "core": r.get("core", ""),
            "score": r.get("selection_score", ""),
            "entry": r.get("entry", ""),
            "sl": r.get("sl", ""),
            "tp": r.get("tp", ""),
            "closed_at": r.get("closed_at", ""),
            "exit": r.get("exit", ""),
            "result": "TP HIT" if result == "TP" else "SL HIT",
            "R": r.get("R", ""),
            "equity_after": r.get("equity_after", ""),
        })

    def _load_master(self):
        if MASTER_JOURNAL.exists():
            try:
                with MASTER_JOURNAL.open("r", newline="", encoding="utf-8") as f:
                    self.master_rows = list(csv.DictReader(f))
                print(f"MASTER JOURNAL RESTORED | closed={len(self.master_rows)}")
            except Exception as e:
                print(f"MASTER JOURNAL RESTORE ERROR: {e}")
                self.master_rows = []
            return

        # GitHub Actions persists paper_v15_state.json. Prefer its authoritative
        # closed history so AIN/SL is promoted to Trade #1 even when the legacy CSV
        # was not committed to the repository.
        sources = []
        state_file = Path("paper_v15_state.json")
        legacy = Path("paper_v15_trades.csv")
        try:
            if state_file.exists():
                d = json.loads(state_file.read_text(encoding="utf-8"))
                sources.extend(d.get("closed", []))
        except Exception as e:
            print(f"STATE JOURNAL BOOTSTRAP ERROR: {e}")

        if not sources and legacy.exists():
            try:
                with legacy.open("r", newline="", encoding="utf-8") as f:
                    sources = list(csv.DictReader(f))
            except Exception as e:
                print(f"LEGACY JOURNAL BOOTSTRAP ERROR: {e}")

        sources = [r for r in sources if self._valid_closed_record({**r, "signal_time": r.get("opened_at", "")})]
        sources.sort(key=lambda r: str(r.get("closed_at") or r.get("opened_at") or ""))
        seen = set()
        trade_no = 0
        for r in sources:
            key = self._trade_key_from_record(r)
            if key in seen:
                continue
            before = len(self.master_rows)
            self._append_closed_record(r, trade_no + 1)
            if len(self.master_rows) > before:
                trade_no += 1
                seen.add(key)

        if self.master_rows:
            self.master_bootstrapped = True
            print(f"MASTER JOURNAL BOOTSTRAPPED | imported={len(self.master_rows)} from GitHub state")

    def _migrate_legacy_active_positions(self):
        # IMPORTANT:
        # Never delete active positions merely because more than one position
        # exists. The V15 forward test is intentionally multi-position (up to
        # FORWARD_MAX_ACTIVE), so older versions of this migration were silently
        # deleting legitimate trades such as ARB and ASTER on every workflow run.
        #
        # Only perform the legacy cleanup on the very first bootstrap, when the
        # master forward-test journal does not yet exist. Once MASTER_JOURNAL is
        # present, every active position is part of the forward-test state and
        # must survive restarts unchanged.
        if MASTER_JOURNAL.exists() or len(self.positions) <= 1:
            return

        newest_key, newest = max(
            self.positions.items(),
            key=lambda kv: str(kv[1].opened_at),
        )
        dropped = [p.coin for k, p in self.positions.items() if k != newest_key]
        self.positions = {newest_key: newest}
        print(
            f"LEGACY ACTIVE MIGRATION | initial bootstrap only | kept={newest.coin} | dropped_stale={','.join(dropped)}"
        )

    def _write_master(self):
        with MASTER_JOURNAL.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(self.master_rows)

    def _refresh_summary_fields(self):
        rs = []
        for row in self.master_rows:
            try:
                rs.append(float(row["R"]))
            except (KeyError, TypeError, ValueError):
                pass
        if not rs:
            return
        wins = sum(x > 0 for x in rs)
        losses = sum(x < 0 for x in rs)
        win_rate = wins / len(rs) * 100.0
        gross_win = sum(x for x in rs if x > 0)
        gross_loss = -sum(x for x in rs if x < 0)
        pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
        running = 0.0
        peak = 0.0
        max_dd = 0.0
        for x in rs:
            running += x
            peak = max(peak, running)
            max_dd = max(max_dd, peak - running)
        for row in self.master_rows:
            row["win_loss"] = f"{wins}/{losses}"
            row["win_rate_pct"] = f"{win_rate:.2f}"
            row["net_R"] = f"{sum(rs):+.2f}"
            row["profit_factor"] = "INF" if gross_loss == 0 else f"{pf:.3f}"
            row["drawdown_R"] = f"{max_dd:.2f}"

    def _write_summary(self):
        rs = []
        for row in self.master_rows:
            try:
                rs.append(float(row["R"]))
            except (KeyError, TypeError, ValueError):
                pass
        wins = sum(x > 0 for x in rs)
        losses = sum(x < 0 for x in rs)
        gross_win = sum(x for x in rs if x > 0)
        gross_loss = -sum(x for x in rs if x < 0)
        pf = gross_win / gross_loss if gross_loss > 0 else None
        running = 0.0
        peak = 0.0
        max_dd = 0.0
        for x in rs:
            running += x
            peak = max(peak, running)
            max_dd = max(max_dd, peak - running)
        MASTER_SUMMARY.write_text(json.dumps({
            "target_closed_trades": TARGET_TRADES,
            "closed_trades": len(rs),
            "remaining": max(0, TARGET_TRADES - len(rs)),
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(wins / len(rs) * 100.0, 2) if rs else 0.0,
            "net_R": round(sum(rs), 4),
            "profit_factor": round(pf, 4) if pf is not None else None,
            "drawdown_R": round(max_dd, 4),
            "equity": float(self.equity),
            "active_positions": len(self.positions),
            "max_active": base.MAX_ACTIVE,
            "score_is_execution_threshold": False,
            "validation": "9 V15 gates from signal_mask",
            "status": "COMPLETE" if len(rs) >= TARGET_TRADES else "RUNNING",
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        MASTER_STATE.write_text(json.dumps({
            "target": TARGET_TRADES,
            "next_trade_no": len(self.master_rows) + 1,
            "closed_trades": len(self.master_rows),
            "active_positions": list(self.positions),
            "max_active": base.MAX_ACTIVE,
            "score_is_execution_threshold": False,
            "validation": "9 V15 gates from signal_mask",
        }, indent=2, ensure_ascii=False), encoding="utf-8")

    def analyze_latest(self, symbol):
        result = super().analyze_latest(symbol)
        if result and result.get("signal"):
            candidate = result["signal"]
            self.candidate_meta[candidate["key"]] = {
                "score": float(candidate["score"]),
                "trade_key": candidate["key"],
            }
        return result

    def _score_for_position(self, p):
        prefix = f"{p.coin}|{p.side}|"
        suffix = f"|{p.core}"
        matches = [
            meta["score"] for key, meta in self.candidate_meta.items()
            if key.startswith(prefix) and key.endswith(suffix)
        ]
        return matches[-1] if matches else ""

    def close(self, key, result, price, ts):
        p = self.positions[key]
        trade_key = f"{p.coin}|{p.side}|{p.opened_at}|{p.core}"
        if any(row.get("trade_key") == trade_key for row in self.master_rows):
            return super().close(key, result, price, ts)
        score = self._score_for_position(p)
        super().close(key, result, price, ts)
        rr = base.RR if result == "TP" else -1.0
        row = {
            "trade_no": str(len(self.master_rows) + 1),
            "trade_key": trade_key,
            "signal_time": p.opened_at,
            "coin": p.coin,
            "side": p.side,
            "core": p.core,
            "score": score,
            "entry": p.entry,
            "sl": p.sl,
            "tp": p.tp,
            "closed_at": ts,
            "exit": price,
            "result": "TP HIT" if result == "TP" else "SL HIT",
            "R": rr,
            "equity_after": self.equity,
        }
        self.master_rows.append(row)
        self._refresh_summary_fields()
        self._write_master()
        self._write_summary()
        self._print_master_summary()
        print(f"📘 MASTER FORWARD TEST | Trade #{row['trade_no']} | {p.coin} | {p.side} | {row['result']} | SCORE={score}")

    def _print_master_summary(self):
        rs = []
        for row in self.master_rows:
            try:
                rs.append(float(row["R"]))
            except (KeyError, TypeError, ValueError):
                pass
        if not rs:
            return
        wins = sum(x > 0 for x in rs)
        losses = sum(x < 0 for x in rs)
        gross_win = sum(x for x in rs if x > 0)
        gross_loss = -sum(x for x in rs if x < 0)
        pf = gross_win / gross_loss if gross_loss else float("inf")
        running = peak = max_dd = 0.0
        for x in rs:
            running += x
            peak = max(peak, running)
            max_dd = max(max_dd, peak - running)
        print(f"MASTER REPORT | closed={len(rs)}/{TARGET_TRADES} | W/L={wins}/{losses} | WR={wins/len(rs)*100:.2f}% | NetR={sum(rs):+.2f} | PF={pf:.3f} | DD_R={max_dd:.2f} | Equity=${self.equity:.2f}")

    def scan_once(self):
        if len(self.master_rows) >= TARGET_TRADES and not self.positions:
            print(f"FORWARD TEST COMPLETE | {len(self.master_rows)} closed trades reached.")
            self.running = False
            return
        super().scan_once()
        self._refresh_summary_fields()
        if self.master_rows:
            self._write_master()
        self._write_summary()
        if len(self.master_rows) >= TARGET_TRADES and not self.positions:
            print(f"FORWARD TEST COMPLETE | {len(self.master_rows)} closed trades reached.")
            self.running = False


if __name__ == "__main__":
    ForwardPaperEngine().run()
