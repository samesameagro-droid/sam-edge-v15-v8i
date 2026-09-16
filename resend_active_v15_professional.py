from __future__ import annotations

import json
from pathlib import Path

from run_v15_forward_test_pro import _make_chart, _send_photo, _fmt
import notifiers

STATE_FILE = Path("paper_v15_state.json")


def main():
    if not STATE_FILE.exists():
        print("NO STATE FILE | paper_v15_state.json not found")
        return
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"STATE READ ERROR: {e}")
        return

    positions = state.get("positions", {})
    if not positions:
        print("NO ACTIVE POSITION TO RESEND")
        return

    for _, p in positions.items():
        payload = dict(p)
        payload.setdefault("selection_score", 0)
        payload["telegram_status"] = "EXECUTED · ACTIVE"
        caption = (
            "<b>🏆 SAM EDGE V15 | ACTIVE TRADE</b>\n"
            f"{'🟢' if p['side'] == 'LONG' else '🔴'} <b>{p['coin']} · {p['side']}</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<b>STATUS:</b> ACTIVE / PAPER ONLY\n"
            f"<b>CORE:</b> {p.get('core', 'V15_ADX4H_CANDLE2H')}\n"
            "<b>TIMEFRAME:</b> 15M\n\n"
            "<b>TRADE PLAN</b>\n"
            f"ENTRY  <code>{_fmt(p['entry'])}</code>\n"
            f"SL     <code>{_fmt(p['sl'])}</code>\n"
            f"TP     <code>{_fmt(p['tp'])}</code>\n"
            "RR     <b>1.25R</b>\n\n"
            "<i>Live BingX 15M chart. The trade remains active until TP or SL is confirmed.</i>"
        )
        chart = _make_chart(payload)
        if chart and _send_photo(chart, caption):
            print(f"ACTIVE CHART SENT | {p['coin']} | {p['side']}")
        else:
            print(f"ACTIVE CHART FAILED | {p['coin']} | {p['side']}")


if __name__ == "__main__":
    main()
