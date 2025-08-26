from typing import Optional, Tuple, Dict, Any
import pandas as pd
from utils.indicators import ema, atr, donchian

def build_df(klines) -> pd.DataFrame:
    cols = ["open_time","open","high","low","close","volume","close_time","qav","trades","taker_base","taker_quote","ignore"]
    df = pd.DataFrame(klines, columns=cols)
    for c in ["open","high","low","close","volume"]:
        df[c] = df[c].astype(float)
    return df

def generate_signal(df: pd.DataFrame, ema_fast=50, ema_slow=200, don_len=20, atr_len=14) -> Tuple[Optional[str], Dict[str, Any]]:
    df = df.copy()
    df["ema_fast"] = ema(df["close"], ema_fast)
    df["ema_slow"] = ema(df["close"], ema_slow)
    up, low, mid = donchian(df["high"], df["low"], don_len)
    df["don_up"], df["don_low"] = up, low
    df["atr"] = atr(df["high"], df["low"], df["close"], atr_len)

    last = df.iloc[-1]
    prev = df.iloc[-2]

    signal = None
    reason = None
    if last["close"] > last["ema_slow"] and prev["close"] <= prev["don_up"] and last["close"] > last["don_up"]:
        signal = "LONG"; reason = "BreakUp+EMA200Up"
    elif last["close"] < last["ema_slow"] and prev["close"] >= prev["don_low"] and last["close"] < last["don_low"]:
        signal = "SHORT"; reason = "BreakDown+EMA200Down"

    info = {"entry_price": last["close"], "atr": last["atr"], "don_up": last["don_up"], "don_low": last["don_low"], "ema_slow": last["ema_slow"], "reason": reason}
    return signal, info

def initial_stop(entry: float, atr_val: float, side: str, atr_mult_sl: float = 1.5) -> float:
    return entry - atr_mult_sl * atr_val if side == "LONG" else entry + atr_mult_sl * atr_val

def trail_stop(highest: float, lowest: float, atr_val: float, side: str, atr_mult_trail: float = 2.0) -> float:
    return highest - atr_mult_trail * atr_val if side == "LONG" else lowest + atr_mult_trail * atr_val

def should_pyramid(side: str, last_price: float, last_add_price: float, atr_val: float, step_atr: float = 1.0, max_adds: int = 2, adds_done: int = 0) -> bool:
    if adds_done >= max_adds: return False
    return last_price >= last_add_price + step_atr * atr_val if side == "LONG" else last_price <= last_add_price - step_atr * atr_val
