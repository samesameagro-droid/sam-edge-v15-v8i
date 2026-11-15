"""V3.4 pre-forward smoke/parity checks."""
import numpy as np, pandas as pd
from strategy_core import add_indicators, btc_long_regime_ok, VERSION, LONG_ALLOWLIST, SHORT_ALLOWLIST
assert VERSION == "3.4"
assert btc_long_regime_ok(-1) is True
assert btc_long_regime_ok(1) is False
assert btc_long_regime_ok(0) is False
assert btc_long_regime_ok(None) is False
assert len(LONG_ALLOWLIST | SHORT_ALLOWLIST) == 23
assert len(LONG_ALLOWLIST) == 11 and len(SHORT_ALLOWLIST) == 14
rows=[]
for i in range(300):
    c=100.0 + 0.025*i + ((i%9)-4)*0.07
    rows.append([i*3600000,c-0.03,c+0.25,c-0.22,c,1000.0])
df=pd.DataFrame(rows,columns=["timestamp","open","high","low","close","volume"])
out=add_indicators(df)
assert len(out)==300 and "long_signal" in out and "short_signal" in out and "st_direction" in out
assert out.iloc[-1].st_direction in (-1,1)
print("ST-ADX V3.4 PAPER PREFLIGHT PASS | BTC gate truth table PASS | 23-coin allowlists PASS | indicator smoke PASS")
