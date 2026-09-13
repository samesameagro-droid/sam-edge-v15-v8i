
from __future__ import annotations
import os, json, time, signal
from dataclasses import dataclass, asdict
from pathlib import Path
from datetime import datetime, timezone

import ccxt
import pandas as pd
import numpy as np
from dotenv import load_dotenv

load_dotenv()

# UNIVERSAL FORWARD MODE:
# - V8I is frozen.
# - The historical 13-coin dataset was only the validation universe.
# - Forward scanner dynamically discovers eligible BingX USDT linear swaps.
# - Default: top 100 by 24h quote volume to keep the scan operational.
# - Set UNIVERSE_LIMIT=0 to scan every eligible symbol (much slower).
TIMEFRAME="15m"
TRACK_TIMEFRAME="5m"
START_EQUITY=100.0
RISK_PCT=0.005
RR=1.25
SCAN_SEC=300
HISTORY_15M=3600
FETCH_CHUNK=900
REQUEST_TIMEOUT_MS=15000
MIN_VOLUME_USDT=float(os.getenv("MIN_VOLUME_USDT","10000000"))
UNIVERSE_LIMIT=int(os.getenv("UNIVERSE_LIMIT","100"))
CRYPTO_ONLY=True

STATE_FILE=Path("paper_v8i_state.json")
JOURNAL_FILE=Path("paper_v8i_trades.csv")
SIGNAL_HISTORY=Path("paper_v8i_signal_history.json")
UNIVERSE_FILE=Path("paper_v8i_universe.json")

@dataclass
class Position:
    coin:str
    side:str
    entry:float
    sl:float
    tp:float
    risk_R:float
    risk_cash:float
    opened_at:str
    core:str="V8I_ADX_ASYM"
    status:str="OPEN"

class PaperEngine:
    def __init__(self):
        self.exchange=ccxt.bingx({
            "enableRateLimit":True,
            "timeout":REQUEST_TIMEOUT_MS,
            "options":{"defaultType":"swap"}
        })
        self.equity=START_EQUITY
        self.closed=[]
        self.positions={}
        self.signal_history=set()
        self.markets={}
        self.load_state()
        self.load_signal_history()

    def load_state(self):
        if STATE_FILE.exists():
            try:
                d=json.loads(STATE_FILE.read_text(encoding="utf-8"))
                self.equity=float(d.get("equity",START_EQUITY))
                self.closed=d.get("closed",[])
                self.positions={k:Position(**v) for k,v in d.get("positions",{}).items()}
                print(f"STATE RESTORED | equity=${self.equity:.2f} | active={len(self.positions)} | closed={len(self.closed)}")
            except Exception as e:
                print("STATE RESTORE ERROR:",e)

    def load_signal_history(self):
        if SIGNAL_HISTORY.exists():
            try:self.signal_history=set(json.loads(SIGNAL_HISTORY.read_text(encoding="utf-8")))
            except Exception as e:print("SIGNAL HISTORY RESTORE ERROR:",e)

    def save_state(self):
        STATE_FILE.write_text(json.dumps({
            "equity":self.equity,
            "closed":self.closed[-2000:],
            "positions":{k:asdict(v) for k,v in self.positions.items()}
        },indent=2),encoding="utf-8")
        SIGNAL_HISTORY.write_text(json.dumps(list(self.signal_history)[-20000:]),encoding="utf-8")
        UNIVERSE_FILE.write_text(json.dumps({
            "updated_at":datetime.now(timezone.utc).isoformat(),
            "mode":"TOP_VOLUME" if UNIVERSE_LIMIT else "ALL_ELIGIBLE",
            "limit":UNIVERSE_LIMIT,
            "min_volume_usdt":MIN_VOLUME_USDT,
            "symbols":self.universe_symbols
        },indent=2),encoding="utf-8")

    @staticmethod
    def is_crypto_market(m):
        if not CRYPTO_ONLY:
            return True
        base = str(m.get("base") or "").upper()
        sym = str(m.get("symbol") or "").upper()
        info = m.get("info") if isinstance(m.get("info"), dict) else {}
        hay = " ".join([
            base, sym,
            str(info.get("symbol") or ""),
            str(info.get("displayName") or ""),
            str(info.get("assetClass") or ""),
            str(info.get("category") or ""),
            str(info.get("productType") or ""),
        ]).upper()

        # Reject only explicit non-crypto families. Do NOT reject generic
        # namespace/provider prefixes such as NCCO.
        for tok in ("GOLD","XAU","SILVER","XAG","OIL","WTI","BRENT",
                    "FOREX","COMMODITY","INDEX","SPX","SP500","NAS100",
                    "NASDAQ","DOW30","US30","USTEC","GER40"):
            if tok in hay:
                return False

        classes = " ".join([
            str(info.get("assetClass") or ""),
            str(info.get("category") or ""),
            str(info.get("productType") or "")
        ]).lower()
        if any(x in classes for x in ("commodity","forex","index","metal")):
            return False

        # Stablecoin/fiat-like bases are not useful candidates.
        if base in {"USDC","USDT","USD","DAI","FDUSD","TUSD","USDP","EUR","GBP","JPY"}:
            return False
        return True
    def discover_universe(self):
        self.exchange.load_markets(reload=True)
        tickers=self.exchange.fetch_tickers()
        eligible=[]
        rejected_noncrypto=0
        for sym,m in self.exchange.markets.items():
            try:
                if not m.get("active",True): continue
                if m.get("swap") is not True: continue
                if m.get("linear") is not True: continue
                if m.get("quote")!="USDT": continue
                if not self.is_crypto_market(m):
                    rejected_noncrypto += 1
                    continue
                t=tickers.get(sym,{})
                qv=t.get("quoteVolume")
                if qv is None:
                    info=t.get("info",{}) if isinstance(t,dict) else {}
                    qv=info.get("quoteVolume") or info.get("turnover")
                qv=float(qv or 0)
                if qv<MIN_VOLUME_USDT: continue
                eligible.append((qv,sym))
            except Exception:
                continue
        eligible.sort(reverse=True)
        if UNIVERSE_LIMIT>0:
            eligible=eligible[:UNIVERSE_LIMIT]
        self.markets=self.exchange.markets
        self.universe_symbols=[s for _,s in eligible]
        self.universe_stats={
            "eligible_after_volume":len(eligible),
            "rejected_noncrypto":rejected_noncrypto,
            "min_volume_usdt":MIN_VOLUME_USDT,
            "mode":"TOP_VOLUME" if UNIVERSE_LIMIT else "ALL_ELIGIBLE"
        }
        print(f"UNIVERSE DISCOVERED | {len(self.universe_symbols)} symbols | mode={self.universe_stats['mode']} | minVol=${MIN_VOLUME_USDT:,.0f} | rejected_noncrypto={self.universe_stats.get('rejected_noncrypto',0)}")
        print("TOP:",", ".join(self.universe_symbols[:15]))
        return self.universe_symbols

    def fetch_df(self,symbol,timeframe=TIMEFRAME,limit=HISTORY_15M):
        out=[]; remaining=int(limit); end_ms=None; chunk=0
        while remaining>0:
            n=min(FETCH_CHUNK,remaining); chunk+=1
            if end_ms is None:
                rows=self.exchange.fetch_ohlcv(symbol,timeframe=timeframe,limit=n)
            else:
                rows=self.exchange.fetch_ohlcv(symbol,timeframe=timeframe,limit=n,params={"endTime":end_ms})
            if not rows: break
            out=rows+out
            oldest=int(rows[0][0]); new_end=oldest-1
            if end_ms is not None and new_end>=end_ms: break
            end_ms=new_end
            remaining-=len(rows)
            if len(rows)<n: break
        if not out:return None
        df=pd.DataFrame(out,columns=["timestamp","open","high","low","close","volume"])
        df=df.drop_duplicates("timestamp").sort_values("timestamp").tail(limit).reset_index(drop=True)
        df["timestamp"]=pd.to_datetime(df["timestamp"],unit="ms",utc=True)
        return df

    def get_signal(self,symbol):
        from core_engine_v8 import enrich,signal_mask
        df=self.fetch_df(symbol)
        if df is None or len(df)<3300:
            return None
        x=enrich(df)
        lm,sm=signal_mask(x,"V8I_ADX_ASYM")
        i=len(x)-2
        side="LONG" if bool(lm.iloc[i]) else ("SHORT" if bool(sm.iloc[i]) else None)
        if side is None:return None
        entry=float(x.close.iloc[i]); atr=float(x.atr.iloc[i])
        if not np.isfinite(atr) or atr<=0:return None
        if side=="LONG":
            sl=float(x.low.iloc[max(0,i-20):i].min())-atr
            risk=entry-sl; tp=entry+RR*risk
        else:
            sl=float(x.high.iloc[max(0,i-20):i].max())+atr
            risk=sl-entry; tp=entry-RR*risk
        if risk<=0:return None
        key=f"{symbol}|{side}|{x.timestamp.iloc[i].isoformat()}|V8I"
        if key in self.signal_history:return None
        self.signal_history.add(key)
        return Position(symbol,side,entry,float(sl),float(tp),1.0,self.equity*RISK_PCT,x.timestamp.iloc[i].isoformat())

    def track(self,p):
        df=self.fetch_df(p.coin,TRACK_TIMEFRAME,20)
        if df is None or len(df)<3:return None
        for _,r in df.iloc[:-1].iterrows():
            h=float(r.high);l=float(r.low)
            sl=l<=p.sl if p.side=="LONG" else h>=p.sl
            tp=h>=p.tp if p.side=="LONG" else l<=p.tp
            if sl and tp:return ("SL",p.sl,r.timestamp.isoformat())
            if sl:return ("SL",p.sl,r.timestamp.isoformat())
            if tp:return ("TP",p.tp,r.timestamp.isoformat())
        return None

    def close(self,key,result,price,ts):
        p=self.positions.pop(key)
        rr=RR if result=="TP" else -1.0
        risk_now=self.equity*RISK_PCT
        self.equity += risk_now*rr
        rec=asdict(p);rec.update({"closed_at":ts,"exit":price,"result":result,"R":rr,"equity_after":self.equity})
        self.closed.append(rec)
        row=pd.DataFrame([rec])
        row.to_csv(JOURNAL_FILE,mode="a",header=not JOURNAL_FILE.exists(),index=False)
        print(f"🎯 {result} | {p.coin} | {p.side} | R={rr:+.2f} | Equity=${self.equity:.2f}")

    def report(self):
        if not self.closed:
            print(f"PAPER REPORT | closed=0 | active={len(self.positions)} | equity=${self.equity:.2f}");return
        r=np.array([float(x["R"]) for x in self.closed])
        w=int((r>0).sum());l=int((r<0).sum())
        pf=float(r[r>0].sum()/(-r[r<0].sum())) if l else float("inf")
        dd=np.maximum.accumulate(np.cumsum(r))-np.cumsum(r)
        print(f"PAPER REPORT | closed={len(r)} | W/L={w}/{l} | WR={w/len(r)*100:.2f}% | NetR={r.sum():+.2f} | PF={pf:.3f} | DD_R={dd.max():.2f} | Equity=${self.equity:.2f}")

    def scan(self):
        t0=time.perf_counter()
        print("\n"+"="*92)
        print("SAM EDGE V15 | V8I FROZEN | UNIVERSAL PAPER FORWARD")
        print(f"TIMEFRAME={TIMEFRAME} | TRACK={TRACK_TIMEFRAME} | EQUITY=${self.equity:.2f} | RISK={RISK_PCT*100:.2f}%")
        syms=self.discover_universe()
        print(f"ACTIVE={len(self.positions)} | CLOSED={len(self.closed)} | SCAN_UNIVERSE={len(syms)}")
        for key,p in list(self.positions.items()):
            try:
                res=self.track(p)
                if res:self.close(key,*res)
            except Exception as e:print(f"TRACK ERROR | {p.coin} | {e}")

        # Max concurrent paper positions kept modest to control risk while
        # still permitting broad discovery.
        MAX_ACTIVE=int(os.getenv("MAX_ACTIVE_POSITIONS","5"))
        signals=0
        for n,sym in enumerate(syms,1):
            if len(self.positions)>=MAX_ACTIVE:
                print(f"POSITION CAP REACHED | {MAX_ACTIVE}");break
            if sym in self.positions:continue
            try:
                sig=self.get_signal(sym)
                if sig:
                    self.positions[sym]=sig;signals+=1
                    print(f"✅ SIGNAL [{n}/{len(syms)}] | {sym} | {sig.side} | Entry={sig.entry:.8g} SL={sig.sl:.8g} TP={sig.tp:.8g}")
                    try:
                        from notifier import send_signal
                        send_signal(asdict(sig),self.equity)
                    except Exception as e:print("TELEGRAM ERROR:",e)
                elif n%25==0:
                    print(f"PROGRESS {n}/{len(syms)} | signals={signals}")
            except Exception as e:
                print(f"SCAN ERROR [{n}/{len(syms)}] | {sym} | {type(e).__name__}: {e}")

        self.save_state();self.report()
        print(f"SCAN COMPLETE | elapsed={time.perf_counter()-t0:.1f}s | next in ~{SCAN_SEC}s")

def main():
    eng=PaperEngine();stop=False
    def handler(sig,frame):
        nonlocal stop;stop=True;print("\nStopping...")
    signal.signal(signal.SIGINT,handler)
    print("STARTUP OK | V8I FROZEN | UNIVERSAL UNIVERSE | PAPER ONLY")
    while not stop:
        try:eng.scan()
        except Exception as e:print("CYCLE ERROR:",type(e).__name__,e)
        if stop:break
        for _ in range(SCAN_SEC):
            if stop:break
            time.sleep(1)

if __name__=="__main__":main()
