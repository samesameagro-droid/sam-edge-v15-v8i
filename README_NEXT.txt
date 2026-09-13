SAM EDGE V15 - CLEAN PAPER FORWARD

Active files:
  core_engine_v15.py
  main_paper_v15.py
  notifiers.py
  .env              (keep your existing file; do NOT replace it)

Fresh V15 state files are created automatically:
  paper_v15_state.json
  paper_v15_trades.csv
  paper_v15_signal_history.json
  paper_v15_universe.json

Recommended environment:
  Python 3.11

Install:
  pip install -r requirements.txt

Run:
  python main_paper_v15.py

Default model:
  15m execution
  4h ADX regime gate
  2h strict candle gate
  5m tracking
  RR 1.25
  paper only
  max active positions = 5

Important:
  Start with a fresh V15 state. Do not copy V14/V8I paper state or journal into this folder.
  Keep historical research/backtest data outside the production folder.
