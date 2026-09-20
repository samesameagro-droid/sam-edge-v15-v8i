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
TARGET_TRADES = 50

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
    def _valid_closed_record(r):
        """Validate a closed trade before importing it into the master journal.

        Both the paper-state records (opened_at) and master-journal records
        (signal_time) are accepted.  A record is valid only when it has a
        terminal TP/SL result and the close timestamp is not earlier than the
        signal/open timestamp.
        """
        from datetime import datetime

        result = str(r.get("result", "")).upper().strip()
        if result not in {"TP", "SL", "TP HIT", "SL HIT"}:
            return False

        signal_raw = r.get("signal_time") or r.get("opened_at")
        closed_raw = r.get("closed_at")
        if not signal_raw or not closed_raw:
            return False

        try:
            signal = datetime.fromisoformat(str(signal_raw).replace("Z", "+00:00"))
            closed = datetime.fromisoformat(str(closed_raw).replace("Z", "+00:00"))
            return closed >= signal
        except (TypeError, ValueError):
            return False

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
        # Reconcile the master journal with the authoritative persisted paper
        # state on EVERY startup. A previously truncated master must never block
        # recovery just because the CSV file exists.
        existing = []
        if MASTER_JOURNAL.exists():
            try:
                with MASTER_JOURNAL.open("r", newline="", encoding="utf-8") as f:
                    existing = list(csv.DictReader(f))
                print(f"MASTER JOURNAL RESTORED | closed={len(existing)}")
            except Exception as e:
                print(f"MASTER JOURNAL RESTORE ERROR: {e}")

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

        merged = []
        seen = set()
        for r in existing + sources:
            normalized = {**r, "signal_time": r.get("opened_at", r.get("signal_time", ""))}
            if not self._valid_closed_record(normalized):
                continue
            key = self._trade_key_from_record(r)
            if key in seen:
                continue
            seen.add(key)
            merged.append(r)

        merged.sort(
            key=lambda r: str(
                r.get("closed_at") or r.get("opened_at") or r.get("signal_time") or ""
            )
        )
        self.master_rows = []
        for r in merged:
            self._append_closed_record(r, len(self.master_rows) + 1)

        if existing and len(self.master_rows) > len(existing):
            print(
                f"MASTER JOURNAL RECONCILED | before={len(existing)} "
                f"| after={len(self.master_rows)}"
            )
        elif self.master_rows:
            self.master_bootstrapped = True
            print(f"MASTER JOURNAL BOOTSTRAPPED | imported={len(self.master_rows)}")

    def _migrate_legacy_active_positions(self):
        # Never delete active positions during migration. V15 is intentionally
        # multi-position; destructive cleanup can erase legitimate trades.
        if len(self.positions) > base.MAX_ACTIVE:
            print(f'ACTIVE STATE ABOVE LIMIT | restored={len(self.positions)} | max_active={base.MAX_ACTIVE} | no positions deleted')
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

    @staticmethod
    def _stats(rows):
        rs = []
        for row in rows:
            try:
                rs.append(float(row.get("R", 0)))
            except (TypeError, ValueError):
                continue
        wins = sum(x > 0 for x in rs)
        losses = sum(x < 0 for x in rs)
        gross_win = sum(x for x in rs if x > 0)
        gross_loss = -sum(x for x in rs if x < 0)
        pf = gross_win / gross_loss if gross_loss else None
        return {
            "trades": len(rs),
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(wins / len(rs) * 100.0, 2) if rs else 0.0,
            "net_R": round(sum(rs), 4),
            "profit_factor": round(pf, 4) if pf is not None else None,
        }

    def _performance_diagnostics(self):
        rows = list(self.master_rows)
        def streak():
            current = 0
            maximum = 0
            for row in rows:
                try:
                    r = float(row.get("R", 0))
                except (TypeError, ValueError):
                    continue
                if r < 0:
                    current += 1
                    maximum = max(maximum, current)
                else:
                    current = 0
            return {"current_loss_streak": current, "max_loss_streak": maximum}

        by_side = {}
        by_core = {}
        for side in sorted({str(r.get("side", "")) for r in rows if r.get("side")}):
            by_side[side] = self._stats([r for r in rows if r.get("side") == side])
        for core in sorted({str(r.get("core", "")) for r in rows if r.get("core")}):
            by_core[core] = self._stats([r for r in rows if r.get("core") == core])

        return {
            "optimization_mode": "SHADOW_ONLY_UNTIL_50_TRADES",
            "recent_10": self._stats(rows[-10:]),
            "recent_20": self._stats(rows[-20:]),
            "all_closed": self._stats(rows),
            "by_side": by_side,
            "by_core": by_core,
            **streak(),
        }

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
        from datetime import datetime, timezone
        MASTER_SUMMARY.write_text(json.dumps({
            "updated_at": datetime.now(timezone.utc).isoformat(),
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
            "performance_diagnostics": self._performance_diagnostics(),
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
        # Idempotent recovery: if master already records this trade as closed,
        # remove the stale live copy without creating a second result/equity event.
        if any(row.get('trade_key') == trade_key for row in self.master_rows):
            print(f'FORWARD CLOSE RECOVERY | already closed | {trade_key}')
            self.positions.pop(key, None)
            self.save_state()
            return
        score = self._score_for_position(p)
        super().close(key, result, price, ts)
        rr = base.RR if result == 'TP' else -1.0
        row = {
            'trade_no': str(len(self.master_rows) + 1),
            'trade_key': trade_key,
            'signal_time': p.opened_at,
            'coin': p.coin,
            'side': p.side,
            'core': p.core,
            'score': score,
            'entry': p.entry,
            'sl': p.sl,
            'tp': p.tp,
            'closed_at': ts,
            'exit': price,
            'result': 'TP HIT' if result == 'TP' else 'SL HIT',
            'R': rr,
            'equity_after': self.equity,
        }
        self.master_rows.append(row)
        self._refresh_summary_fields()
        self._write_master()
        self._write_summary()
        # Persist immediately after a close. If the runner dies before the end
        # of the scan, the closed trade and equity still survive the restart.
        self.save_state()
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
