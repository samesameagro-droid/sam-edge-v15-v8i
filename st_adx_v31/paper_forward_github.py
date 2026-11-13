"""ST-ADX V3.2 BingX one-shot PAPER runner. Public OHLCV only; never sends exchange orders."""
from __future__ import annotations
import csv,json,logging,os,time
from datetime import datetime,timezone
from pathlib import Path
import ccxt
import pandas as pd
import requests
from strategy_core import add_indicators,LONG_ALLOWLIST,SHORT_ALLOWLIST,ATR_SL_MULTIPLIER,ATR_TP_MULTIPLIER
ROOT=Path(__file__).resolve().parent
STATE=ROOT/"paper_state.json";TRADES=ROOT/"paper_trades.csv";SIGNALS=ROOT/"paper_signals.csv"
ASSETS=sorted(LONG_ALLOWLIST|SHORT_ALLOWLIST);BAR=3600000
FEE=.0005;SLIP=.0002;RISK=.01;CAP=.45;MAX_OPEN_RISK=.05;GRACE=300
PRIORITY={"BTC:SHORT":5.11,"ENA:LONG":2.93,"ARB:SHORT":2.98,"AVAX:SHORT":2.94,"SOL:SHORT":2.88,"SUI:SHORT":2.60,"ETH:SHORT":2.25,"ENA:SHORT":1.82,"ZEC:LONG":1.63,"LINK:SHORT":1.64,"NEAR:SHORT":1.56,"XRP:SHORT":1.56,"UNI:SHORT":1.39,"XRP:LONG":1.17,"TIA:LONG":5.6741,"ORDI:LONG":4.7457,"AKE:LONG":2.6871,"DOT:SHORT":2.5937,"WIF:LONG":2.3025,"APR:LONG":2.2137,"LTC:SHORT":2.0849,"XLM:LONG":2.0285,"DYDX:LONG":1.9173,"VIRTUAL:LONG":1.9133,"ADA:SHORT":1.8243}
logging.basicConfig(level=logging.INFO,format="%(asctime)s | %(levelname)s | %(message)s");log=logging.getLogger("ST_ADX_V31")
def utc(ms):return datetime.fromtimestamp(ms/1000,timezone.utc).isoformat()
def append_csv(path,row):
    if not path.exists() or not path.stat().st_size:
        with path.open("w",newline="",encoding="utf-8-sig") as f:w=csv.DictWriter(f,fieldnames=list(row));w.writeheader();w.writerow(row)
        return
    with path.open("r",newline="",encoding="utf-8-sig") as f:r=csv.DictReader(f);fields=list(r.fieldnames or []);old=list(r)
    nf=fields+[k for k in row if k not in fields]
    if nf!=fields:
        tmp=path.with_suffix(".tmp")
        with tmp.open("w",newline="",encoding="utf-8-sig") as f:w=csv.DictWriter(f,fieldnames=nf);w.writeheader();w.writerows(old)
        tmp.replace(path)
    with path.open("a",newline="",encoding="utf-8-sig") as f:csv.DictWriter(f,fieldnames=nf).writerow({k:row.get(k,"") for k in nf})
def load_state():
    s=json.loads(STATE.read_text()) if STATE.exists() else {"initialized":False,"equity":100000.0,"open_positions":{},"last_signal_ts":{},"last_exit_signal_ts":{},"trade_counter":0}
    for k,v in {"initialized":False,"equity":100000.0,"open_positions":{},"last_signal_ts":{},"last_exit_signal_ts":{},"trade_counter":0}.items():s.setdefault(k,v)
    return s
def save_state(s):
    t=STATE.with_suffix(".tmp");t.write_text(json.dumps(s,indent=2));t.replace(STATE)
def transient(e):
    m=str(e).lower()
    return any(x in m for x in ("100410","100500","system is busy","please try again later")) or isinstance(e,(ccxt.RequestTimeout,ccxt.ExchangeNotAvailable,ccxt.DDoSProtection))
def fetch_retry(ex,sym):
    for n,delay in enumerate((2,4,8,None),1):
        try:return ex.fetch_ohlcv(sym,timeframe="1h",limit=300)
        except Exception as e:
            if delay is None or not transient(e):raise
            log.warning("BingX %s temporary error %s; retry %d/4 in %ds",sym,type(e).__name__,n+1,delay);time.sleep(delay)
def resolve(ex,base):
    for sym in (f"{base}/USDT:USDT",f"{base}/USDT"):
        m=ex.markets.get(sym)
        if m and m.get("swap") and m.get("linear") and m.get("quote")=="USDT" and m.get("active") is not False:return sym
    for sym,m in ex.markets.items():
        if m.get("base")==base and m.get("quote")=="USDT" and m.get("swap") and m.get("linear") and m.get("active") is not False:return sym
    raise ValueError(f"Linear USDT perpetual not found: {base}")
def telegram(msg):
    tok=os.getenv("TELEGRAM_BOT_TOKEN","").strip();chat=os.getenv("TELEGRAM_CHAT_ID","").strip()
    if not tok or not chat:log.warning("Telegram secrets missing; message skipped.");return
    r=requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",json={"chat_id":chat,"text":msg,"disable_web_page_preview":True},timeout=20);r.raise_for_status()
    if not r.json().get("ok"):raise RuntimeError(r.text)
def estimated_risk_per_unit(entry,stop,side):
    """Estimated all-in loss per unit if the protective stop fills with adverse slippage."""
    stop_fill=stop*(1-SLIP if side=="LONG" else 1+SLIP)
    return abs(entry-stop)+entry*FEE+abs(stop_fill)*FEE+abs(stop_fill-stop)
def estimated_stop_risk(p):
    """USDT risk reserved to stop, including entry/exit fees and adverse stop slippage."""
    entry=float(p["entry_price"]);stop=float(p["stop"]);qty=float(p["qty"]);side=p["side"]
    stop_fill=stop*(1-SLIP if side=="LONG" else 1+SLIP)
    entry_fee=float(p.get("entry_fee",entry*qty*FEE))
    return abs(entry-stop)*qty+entry_fee+abs(stop_fill*qty)*FEE+abs(stop_fill-stop)*qty
def portfolio_open_risk(s):
    return sum(estimated_stop_risk(p) for p in s.get("open_positions",{}).values())
def close_pos(s,base,p,c,raw,reason):
    side=p["side"];slip=0.0 if reason.startswith("TARGET") else SLIP
    px=raw*(1-slip if side=="LONG" else 1+slip);q=float(p["qty"])
    gross=(px-p["entry_price"])*q if side=="LONG" else (p["entry_price"]-px)*q
    ef=abs(px*q)*FEE;net=gross-float(p["entry_fee"])-ef
    s["equity"]=float(s["equity"])+net;s["trade_counter"]=int(s["trade_counter"])+1;n=s["trade_counter"]
    append_csv(TRADES,{"trade_no":n,"symbol":p["symbol"],"base":base,"side":side,"entry_time_utc":utc(int(p["entry_time"])),"entry_price":p["entry_price"],"qty":q,"entry_notional":p["entry_notional"],"stop":p["stop"],"target":p["target"],"exit_time_utc":utc(int(c.timestamp)),"exit_price":px,"exit_reason":reason,"gross_pnl_after_slippage":gross,"entry_fee":p["entry_fee"],"exit_fee":ef,"net_pnl_ex_funding":net,"equity_after":s["equity"],"funding_included":False})
    s["last_exit_signal_ts"][base]=int(c.timestamp);s["open_positions"].pop(base,None)
    msg=f"📘 ST-ADX PAPER CLOSE #{n}\n{p['symbol']} {side}\nExit {px:.8g} ({reason})\nNet PnL ex-funding {net:+.2f} USDT\nEquity {s['equity']:.2f} USDT\nPaper only; funding excluded."
    log.info(msg.replace("\n"," | "))
    try:telegram(msg)
    except Exception as e:log.error("Telegram close failed: %s",e)
def bracket(p,c):
    o,h,l=float(c.open),float(c.high),float(c.low);st,tp=float(p["stop"]),float(p["target"]);side=p["side"]
    if side=="LONG":
        if o<=st:return o,"STOP_GAP"
        if o>=tp:return o,"TARGET_GAP"
        hs,ht=l<=st,h>=tp
    else:
        if o>=st:return o,"STOP_GAP"
        if o<=tp:return o,"TARGET_GAP"
        hs,ht=h>=st,l<=tp
    if hs and ht:
        hf=abs(o-h)<abs(o-l)
        if side=="LONG":return (tp,"TARGET_BOTH_TOUCHED") if hf else (st,"STOP_BOTH_TOUCHED")
        return (st,"STOP_BOTH_TOUCHED") if hf else (tp,"TARGET_BOTH_TOUCHED")
    if hs:return st,"STOP"
    if ht:return tp,"TARGET"
    return None
def manage(s,frames,now):
    for base,p in list(s["open_positions"].items()):
        f=frames[base];closed=f[f.timestamp+BAR<=now];last=int(p.get("last_processed_bar",int(p["entry_time"])-BAR))
        for _,c in closed[closed.timestamp>last].iterrows():
            if base not in s["open_positions"]:break
            p=s["open_positions"][base]
            if p.get("pending_flip"):close_pos(s,base,p,c,float(c.open),"ST_FLIP");break
            hit=bracket(p,c)
            if hit:close_pos(s,base,p,c,hit[0],hit[1]);break
            if p["side"]=="LONG" and int(c.st_direction)>0:p["pending_flip"]=True
            elif p["side"]=="SHORT" and int(c.st_direction)<0:p["pending_flip"]=True
            p["last_processed_bar"]=int(c.timestamp)
def signals(s,frames,symbols,now):
    cand=[]
    for base,f in frames.items():
        closed=f[f.timestamp+BAR<=now]
        if closed.empty:continue
        r=closed.iloc[-1];ts=int(r.timestamp)
        if ts<=int(s["last_signal_ts"].get(base,-1)):continue
        nr=f[f.timestamp==ts+BAR]
        if nr.empty:continue
        s["last_signal_ts"][base]=ts;age=(now-ts-BAR)/1000
        if age<0 or age>GRACE:log.info("%s signal skipped, entry candle age %.0fs",base,age);continue
        if base in s["open_positions"]:continue
        le=s["last_exit_signal_ts"].get(base)
        if le is not None and ts-int(le)<BAR:continue
        side="LONG" if bool(r.long_signal) and base in LONG_ALLOWLIST else "SHORT" if bool(r.short_signal) and base in SHORT_ALLOWLIST else None
        if side is None:
            log.info("EVAL %s | ADX %.2f rising=%s RSI %.2f ST=%s | NO SIGNAL",base,float(r.adx),"OK" if r.adx_rising else "NO",float(r.rsi),"BULL" if r.st_bull else "BEAR");continue
        cand.append((PRIORITY.get(f"{base}:{side}",0),base,side,r,nr.iloc[0]))
    cand.sort(key=lambda x:x[0],reverse=True)
    for priority,base,side,r,nxt in cand:
        atr=float(r.atr_risk)
        if not (atr>0 and pd.notna(atr)):continue
        raw=float(nxt.open);entry=raw*(1+SLIP if side=="LONG" else 1-SLIP)
        sd=atr*ATR_SL_MULTIPLIER;td=atr*ATR_TP_MULTIPLIER
        stop=entry-sd if side=="LONG" else entry+sd;target=entry+td if side=="LONG" else entry-td
        eq=float(s["equity"])
        unit_risk=estimated_risk_per_unit(entry,stop,side)
        qty=min(eq*RISK/unit_risk,eq*CAP/entry)
        if qty<=0 or not pd.notna(qty):continue
        nt=entry*qty;fee=nt*FEE
        candidate_risk=qty*unit_risk
        current_risk=portfolio_open_risk(s)
        risk_limit=eq*MAX_OPEN_RISK
        if current_risk+candidate_risk>risk_limit+1e-8:
            append_csv(SIGNALS,{"symbol":symbols[base],"side":side,"signal_time_utc":utc(int(r.timestamp)),"status":"SKIPPED_PORTFOLIO_RISK_CAP","priority":priority,"candidate_risk_usdt":candidate_risk,"portfolio_open_risk_usdt":current_risk,"portfolio_open_risk_pct":current_risk/eq*100 if eq>0 else 0,"portfolio_risk_cap_usdt":risk_limit,"portfolio_risk_cap_pct":MAX_OPEN_RISK*100})
            log.info("RISK CAP SKIP %s %s | candidate=%.2f USDT | open=%.2f/%.2f USDT (%.2f%%/%.2f%%)",base,side,candidate_risk,current_risk,risk_limit,current_risk/eq*100 if eq>0 else 0,MAX_OPEN_RISK*100)
            continue
        s["open_positions"][base]={"symbol":symbols[base],"base":base,"side":side,"signal_time":int(r.timestamp),"entry_time":int(nxt.timestamp),"raw_entry":raw,"entry_price":entry,"signal_atr":atr,"stop":stop,"target":target,"qty":qty,"entry_notional":nt,"entry_fee":fee,"risk_reserved_usdt":candidate_risk,"pending_flip":False,"last_processed_bar":int(nxt.timestamp)-BAR}
        aggregate_risk=portfolio_open_risk(s)
        aggregate_pct=aggregate_risk/eq*100 if eq>0 else 0
        append_csv(SIGNALS,{"symbol":symbols[base],"side":side,"signal_time_utc":utc(int(r.timestamp)),"entry_time_utc":utc(int(nxt.timestamp)),"reference_entry_open":raw,"paper_entry":entry,"stop":stop,"target":target,"qty":qty,"notional":nt,"adx":float(r.adx),"rsi":float(r.rsi),"atr10":atr,"status":"PAPER_OPEN","priority":priority,"risk_reserved_usdt":candidate_risk,"portfolio_open_risk_usdt":aggregate_risk,"portfolio_open_risk_pct":aggregate_pct,"portfolio_risk_cap_pct":MAX_OPEN_RISK*100})
        msg=f"🧪 ST-ADX V3.2 PAPER ONLY\n{symbols[base]} · {side} · 1H\nSignal: {utc(int(r.timestamp))}\nEntry: {entry:.8g}\nSL: {stop:.8g}\nTP: {target:.8g}\nR:R 1:{ATR_TP_MULTIPLIER/ATR_SL_MULTIPLIER:.2f}\nADX {float(r.adx):.1f} | RSI {float(r.rsi):.1f} | ATR10 {atr:.8g}\nQty {qty:.6f} | Notional {nt:.2f} USDT\nRisk reserve: {candidate_risk:.2f} USDT ({candidate_risk/eq*100:.2f}%)\nPortfolio stop-risk: {aggregate_risk:.2f}/{risk_limit:.2f} USDT ({aggregate_pct:.2f}%/{MAX_OPEN_RISK*100:.2f}%)\nFee 0.05%/side; adverse stop slippage 2bps; funding excluded. No live order sent."
        log.info(msg.replace("\n"," | "))
        try:telegram(msg)
        except Exception as e:log.error("Telegram signal failed: %s",e)
def main():
    ex=ccxt.bingx({"enableRateLimit":True,"timeout":20000,"options":{"defaultType":"swap","defaultSubType":"linear"}});ex.load_markets()
    try:start=ex.fetch_time() or ex.milliseconds()
    except Exception:start=ex.milliseconds()
    frames={};symbols={}
    for base in ASSETS:
        sym=resolve(ex,base);raw=fetch_retry(ex,sym)
        if not raw or len(raw)<120:raise RuntimeError(f"Insufficient OHLCV: {base}")
        f=pd.DataFrame(raw,columns=["timestamp","open","high","low","close","volume"])
        f=f.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
        if len(f)<120 or not (f.timestamp.diff().dropna()>0).all():raise RuntimeError(f"Invalid OHLCV: {base}")
        frames[base]=add_indicators(f);symbols[base]=sym;log.info("FEED %s %d candles",sym,len(f));time.sleep(.15)
    try:now=ex.fetch_time() or ex.milliseconds()
    except Exception:now=max(start,ex.milliseconds())
    for b,f in frames.items():
        c=f[f.timestamp+BAR<=now]
        if c.empty:raise RuntimeError(f"No closed candle: {b}")
        log.info("CLOSED %s %s",b,utc(int(c.iloc[-1].timestamp)))
    s=load_state();log.info("ST-ADX V3.2 PAPER ONLY | symbols=%d | max_open_risk=%.1f%% | per_trade_target=%.1f%% | active=%d | open_risk=%.2f USDT | equity=%.2f",len(ASSETS),MAX_OPEN_RISK*100,RISK*100,len(s["open_positions"]),portfolio_open_risk(s),float(s["equity"]))
    if not s["initialized"]:
        for b,f in frames.items():s["last_signal_ts"][b]=int(f[f.timestamp+BAR<=now].iloc[-1].timestamp)
        s["initialized"]=True;save_state(s);log.warning("PAPER ENGINE ARMED; historical candles ignored.");return 0
    manage(s,frames,now);signals(s,frames,symbols,now);save_state(s)
    open_risk=portfolio_open_risk(s);risk_pct=open_risk/float(s["equity"])*100 if float(s["equity"])>0 else 0.0
    log.info("CYCLE COMPLETE | equity=%.2f | active=%d | open_risk=%.2f USDT (%.2f%%/%.2f%% cap) | closed=%d",float(s["equity"]),len(s["open_positions"]),open_risk,risk_pct,MAX_OPEN_RISK*100,int(s["trade_counter"]))
    return 0
if __name__=="__main__":
    try:raise SystemExit(main())
    except Exception as e:log.exception("ST-ADX PAPER CYCLE FAILED: %s",e);raise SystemExit(2)
