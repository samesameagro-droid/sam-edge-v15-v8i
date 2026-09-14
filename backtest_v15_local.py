from pathlib import Path
import pandas as pd
from core_engine_v15 import COINS, CORE_NAME, RR, load_coin, enrich, backtest_core

ROOT = Path(r"C:\Users\User\SAM_EDGE_V15\BINANCE_V15_DATA")
OUT = Path("v15_backtest_results.csv")

rows = []
for coin in COINS:
    try:
        raw = load_coin(ROOT, coin)
        x = enrich(raw)
        trades = backtest_core(x, CORE_NAME, RR)
        if not trades.empty:
            trades.insert(0, "coin", coin)
            rows.append(trades)
        print(f"{coin:5s} | candles={len(x):7d} | trades={len(trades):4d}")
    except Exception as e:
        print(f"{coin:5s} | ERROR | {e}")

if not rows:
    raise SystemExit("No trades produced.")

tr = pd.concat(rows, ignore_index=True).sort_values("entry_time").reset_index(drop=True)
tr.to_csv(OUT, index=False)

wins = (tr.result == "TP").sum()
losses = (tr.result == "SL").sum()
r_total = tr.R.sum()
winrate = 100 * wins / len(tr)
gross_profit_r = tr.loc[tr.R > 0, "R"].sum()
gross_loss_r = -tr.loc[tr.R < 0, "R"].sum()
pf = gross_profit_r / gross_loss_r if gross_loss_r else float("inf")
eq = r_total / len(tr)
equity = tr.R.cumsum()
peak = equity.cummax()
dd = equity - peak
max_dd_r = dd.min()

print("\n" + "=" * 72)
print(f"CORE       : {CORE_NAME}")
print(f"RR         : {RR}")
print(f"COINS      : {', '.join(COINS)}")
print("=" * 72)
print(f"TRADES     : {len(tr)}")
print(f"WINS       : {wins}")
print(f"LOSSES     : {losses}")
print(f"WINRATE    : {winrate:.2f}%")
print(f"NET R      : {r_total:.2f}R")
print(f"PROFIT FACT: {pf:.3f}")
print(f"EXPECTANCY : {eq:.4f}R/trade")
print(f"MAX DD     : {max_dd_r:.2f}R")
print(f"RESULT CSV : {OUT.resolve()}")
print("=" * 72)

print("\nBY COIN")
by = tr.groupby("coin").agg(
    trades=("R", "size"),
    wins=("result", lambda s: (s == "TP").sum()),
    losses=("result", lambda s: (s == "SL").sum()),
    net_R=("R", "sum"),
)
by["winrate_%"] = 100 * by.wins / by.trades
print(by.sort_values("net_R", ascending=False).to_string(float_format=lambda v: f"{v:.2f}"))
