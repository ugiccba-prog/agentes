"""Referencia retrospectiva de timing para entradas adicionales en paper."""
import hashlib
import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from candle_clock import closed_only

REPORT = Path(__file__).with_name("impulse_study.json")
START = datetime(2026, 11, 14, 0, 59, 57, tzinfo=timezone.utc)

def utc(value):
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)

def update_study(state, history):
    report = json.loads(REPORT.read_text()) if REPORT.exists() else {"controls": {}}
    controls = report["controls"]
    for trade in state.closed_trades:
        if trade["strategy"] not in ("momentum_extra", "momentum_shark"):
            continue
        key = "|".join((trade["symbol"], trade["entry_time"], trade["id"]))
        if key in controls:
            continue
        entry, exit_time = utc(trade["entry_time"]), utc(trade["exit_time"])
        bars = max(1, round((exit_time - entry).total_seconds() / 900))
        df, _ = history(trade["symbol"], interval="15m", limit=300, use_cache=False, live_only=True)
        df = closed_only(df, "15m")
        if df is None or len(df) <= bars:
            continue
        prices = {t.to_pydatetime()+timedelta(minutes=15): float(c)
                  for t,c in zip(pd.to_datetime(df["timestamp"], utc=True),df["close"])
                  if math.isfinite(float(c)) and float(c)>0}
        notional = trade["size"] * trade["entry_price"]
        sign = 1 if trade["side"]=="LONG" else -1
        fee = 0.0005 if trade["market"].lower()=="crypto" else 0.0001
        candidates = []
        for t, price in sorted(prices.items()):
            end = t + timedelta(minutes=15*bars)
            if max(START,entry-timedelta(hours=24)) <= t and end <= entry and end in prices:
                ratio = prices[end]/price
                candidates.append(notional*(sign*(ratio-1)-fee*(1+ratio)))
        if len(candidates) < 5:
            continue
        rng = random.Random(int.from_bytes(hashlib.sha256(key.encode()).digest(),"big"))
        controls[key] = {"trade_net":trade["pnl_usd"], "windows":len(candidates),
                         "random_net":[round(rng.choice(candidates),2) for _ in range(200)]}
    REPORT.write_text(json.dumps(report,separators=(",",":")),encoding="utf-8")
