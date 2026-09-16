from __future__ import annotations

import csv
import json
from pathlib import Path

import main_paper_v15 as base

# Forward-test policy:
# - scanner still scans the whole eligible universe;
# - only ONE selected position is active at a time;
# - Trade # is a MASTER sequence across all coins, not per coin;
# - only SELECTED/CLOSED trades count toward the 20-trade target;
# - WAITLIST signals do not count.
base.MAX_ACTIVE = 1

MASTER_JOURNAL = Path("v15_forward_test_master_journal.csv")
MASTER_STATE = Path("v15_forward_test_master_state.json")
MASTER_SUMMARY = Path("v15_forward_test_summary.json")
TARGET_TRADES = 20

FIELDS = [
    "trade_no",
    "trade_key",
    "signal_time",
    "coin",
    "side",
    "core",
    "score",
    "entry",
    "sl",
    "tp",
    "closed_at",
    "exit",
    "result",
    "R",
    "equity_after",
    "win_loss",
    "win_rate_pct",
    "net_R",
    "profit_factor",
    "drawdown_R",
]


class ForwardPaperEngine(base.PaperEngine):
    def __init__(self):
        self.candidate_meta: dict[str, dict] = {}
        self.master_rows: list[dict] = []
        self._load_master()
        super().__init__()
        print(
            f"FORWARD TEST | completed={len(self.master_rows)}/{TARGET_TRADES} "
            f"| active={len(self.positions)} | policy=ONE_ACTIVE"
        )

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

        # First-run migration: if the existing V15 engine already closed trades
        # (for example AIN/SL), promote those records into the master journal.
        legacy = Path("paper_v15_trades.csv")
        if not legacy.exists():
            return
        try:
            with legacy.open("r", newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))

            trade_no = 0
            for r in rows:
                result = str(r.get("result", "")).upper().strip()
                if result not in {"TP", "SL"}:
                    continue
                trade_no += 1
                self.master_rows.append(
                    {
                        "trade_no": str(trade_no),
                        "trade_key": (
                            f"{r.get('coin', '')}|{r.get('side', '')}|"
                            f"{r.get('opened_at', '')}|{r.get('core', '')}"
                        ),
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
                    }
                )

            if self.master_rows:
                self._refresh_summary_fields()
                self._write_master()
                print(
                    f"MASTER JOURNAL BOOTSTRAPPED | imported={len(self.master_rows)} "
                    f"from paper_v15_trades.csv"
                )
        except Exception as e:
            print(f"MASTER JOURNAL BOOTSTRAP ERROR: {e}")

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
        cumulative = []
        running = 0.0
        peak = 0.0
        max_dd = 0.0
        for x in rs:
            running += x
            peak = max(peak, running)
            max_dd = max(max_dd, peak - running)
        net_r = sum(rs)

        for row in self.master_rows:
            row["win_loss"] = f"{wins}/{losses}"
            row["win_rate_pct"] = f"{win_rate:.2f}"
            row["net_R"] = f"{net_r:+.2f}"
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

        payload = {
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
            "status": "COMPLETE" if len(rs) >= TARGET_TRADES else "RUNNING",
        }
        MASTER_SUMMARY.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        MASTER_STATE.write_text(
            json.dumps(
                {
                    "target": TARGET_TRADES,
                    "next_trade_no": len(self.master_rows) + 1,
                    "closed_trades": len(self.master_rows),
                    "active_positions": list(self.positions),
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

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
            meta["score"]
            for key, meta in self.candidate_meta.items()
            if key.startswith(prefix) and key.endswith(suffix)
        ]
        return matches[-1] if matches else ""

    def close(self, key, result, price, ts):
        p = self.positions[key]
        trade_key = f"{p.coin}|{p.side}|{p.opened_at}|{p.core}"

        # Never append the same completed trade twice after a restart.
        if any(row.get("trade_key") == trade_key for row in self.master_rows):
            return super().close(key, result, price, ts)

        score = self._score_for_position(p)

        # Base engine performs the authoritative TP/SL resolution and equity update.
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
        print(
            f"📘 MASTER FORWARD TEST | Trade #{row['trade_no']} | "
            f"{p.coin} | {p.side} | {row['result']}"
        )

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
        running = 0.0
        peak = 0.0
        max_dd = 0.0
        for x in rs:
            running += x
            peak = max(peak, running)
            max_dd = max(max_dd, peak - running)

        print(
            f"MASTER REPORT | closed={len(rs)}/{TARGET_TRADES} | "
            f"W/L={wins}/{losses} | WR={wins/len(rs)*100:.2f}% | "
            f"NetR={sum(rs):+.2f} | PF={pf:.3f} | DD_R={max_dd:.2f} | "
            f"Equity=${self.equity:.2f}"
        )

    def scan_once(self):
        if len(self.master_rows) >= TARGET_TRADES and not self.positions:
            print(
                f"FORWARD TEST COMPLETE | {len(self.master_rows)} closed trades reached."
            )
            self.running = False
            return

        super().scan_once()

        # Persist the summary even on scans where no trade closes.
        self._refresh_summary_fields()
        self._write_master() if self.master_rows else None
        self._write_summary()

        if len(self.master_rows) >= TARGET_TRADES and not self.positions:
            print(
                f"FORWARD TEST COMPLETE | {len(self.master_rows)} closed trades reached."
            )
            self.running = False


if __name__ == "__main__":
    ForwardPaperEngine().run()
