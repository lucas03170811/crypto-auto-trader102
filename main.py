import os, time, math
from typing import List, Dict, Any
import pandas as pd
from dotenv import load_dotenv
from exchange import Exchange
from utils.state import load_state, save_state
from strategy.trend import build_df, generate_signal, initial_stop, trail_stop, should_pyramid

load_dotenv()

API_KEY = os.getenv("BINANCE_API_KEY", "")
API_SECRET = os.getenv("BINANCE_API_SECRET", "")

TESTNET = os.getenv("TESTNET", "true").lower() == "true"
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

BASE_EQUITY = float(os.getenv("BASE_EQUITY", "20"))
RISK_PCT = float(os.getenv("RISK_PCT", "0.02"))
LEVERAGE = int(os.getenv("LEVERAGE", "30"))
TIMEFRAME = os.getenv("TIMEFRAME", "5m")

MAX_PARALLEL_SYMBOLS = int(os.getenv("MAX_PARALLEL_SYMBOLS", "6"))

ATR_LEN = int(os.getenv("ATR_LEN", "14"))
ATR_MULT_SL = float(os.getenv("ATR_MULT_SL", "1.5"))
ATR_MULT_TRAIL = float(os.getenv("ATR_MULT_TRAIL", "2.0"))
DONCHIAN_LEN = int(os.getenv("DONCHIAN_LEN", "20"))
EMA_FAST = int(os.getenv("EMA_FAST", "50"))
EMA_SLOW = int(os.getenv("EMA_SLOW", "200"))

LOOP_SLEEP_SECS = int(os.getenv("LOOP_SLEEP_SECS", "30"))

DEFAULT_SYMBOLS = ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","TONUSDT","SUIUSDT","SEIUSDT","XRPUSDT","DOGEUSDT"]

def pick_symbols(ex: Exchange) -> List[str]:
    symbols_str = os.getenv("SYMBOLS", "").strip()
    if symbols_str:
        raw = [s.strip().upper() for s in symbols_str.split(",") if s.strip()]
    else:
        raw = DEFAULT_SYMBOLS.copy()
        top = ex.top_symbols_by_quote_volume(limit=20)
        for s in top:
            if s not in raw and s.endswith("USDT"):
                raw.append(s)
    return raw[:MAX_PARALLEL_SYMBOLS]

def compute_position_size(equity: float, risk_pct: float, atr_val: float, entry_price: float, side: str, symbol: str, ex: Exchange) -> float:
    risk_amount = max(0.5, equity * risk_pct)
    stop_price = initial_stop(entry_price, atr_val, side, ATR_MULT_SL)
    stop_dist = abs(entry_price - stop_price)
    if stop_dist <= 0:
        return 0.0
    qty_by_risk = risk_amount / stop_dist
    max_notional = max(0.0, equity * LEVERAGE * 0.95)
    max_qty_by_margin = max_notional / entry_price if entry_price > 0 else 0.0
    qty = min(qty_by_risk, max_qty_by_margin)
    qty = ex.round_qty(symbol, qty)
    min_notional = ex.min_notional(symbol)
    if min_notional and qty * entry_price < min_notional:
        qty = ex.round_qty(symbol, (min_notional / entry_price) * 1.05)
    return qty

def place_entry_and_sl(ex: Exchange, symbol: str, side: str, qty: float, entry_price: float, atr_val: float):
    if qty <= 0:
        print(f"[{symbol}] qty too small, skip."); return None
    if DRY_RUN:
        print(f"[DRY_RUN] {symbol} {side} MARKET qty={qty}")
        return {"orderId":"DRY","side":side,"qty":qty,"price":entry_price}
    resp = ex.new_market_order(symbol=symbol, side=("BUY" if side=="LONG" else "SELL"), quantity=qty)
    stop_price = initial_stop(entry_price, atr_val, side, ATR_MULT_SL)
    ex.cancel_all(symbol)
    ex.new_stop_market_close(symbol=symbol, side=side, stop_price=stop_price)
    return resp

def manage_trailing_and_pyramid(ex: Exchange, symbol: str, side: str, df, pos_state: Dict[str, Any]):
    last = df.iloc[-1]
    highest = df["high"].rolling(window=100, min_periods=1).max().iloc[-1]
    lowest = df["low"].rolling(window=100, min_periods=1).min().iloc[-1]

    atr_val = last["atr"]
    if math.isnan(atr_val) or atr_val <= 0: return

    new_trail = trail_stop(highest, lowest, atr_val, side, ATR_MULT_TRAIL)
    prev_trail = pos_state.get("trail", None)
    entry_price = pos_state.get("entry_price", last["close"])
    adds_done = pos_state.get("adds_done", 0)
    last_add_price = pos_state.get("last_add_price", entry_price)

    if prev_trail is None or (side=='LONG' and new_trail > prev_trail) or (side=='SHORT' and new_trail < prev_trail):
        if DRY_RUN:
            print(f"[DRY_RUN] {symbol} Update trail to {new_trail}")
        else:
            ex.cancel_all(symbol)
            ex.new_stop_market_close(symbol=symbol, side=side, stop_price=new_trail)
        pos_state["trail"] = new_trail

    price = last["close"]
    if should_pyramid(side, price, last_add_price, atr_val, step_atr=1.0, max_adds=4, adds_done=adds_done):
        add_qty = pos_state.get("qty", 0) * 0.5
        if add_qty > 0:
            if DRY_RUN:
                print(f"[DRY_RUN] {symbol} PYRAMID {side} MARKET add_qty={add_qty}")
            else:
                ex.new_market_order(symbol=symbol, side=('BUY' if side=='LONG' else 'SELL'), quantity=add_qty)
                ex.cancel_all(symbol)
                ex.new_stop_market_close(symbol=symbol, side=side, stop_price=pos_state['trail'])
            pos_state["qty"] = pos_state.get("qty",0) + add_qty
            pos_state["adds_done"] = adds_done + 1
            pos_state["last_add_price"] = price

def main():
    if not API_KEY or not API_SECRET:
        print("Please set BINANCE_API_KEY / BINANCE_API_SECRET"); return

    ex = Exchange(API_KEY, API_SECRET)
    ex.set_one_way_mode()
    ex.prime_filters()

    symbols = pick_symbols(ex)
    print(f"Monitoring symbols: {symbols} | TESTNET={TESTNET} DRY_RUN={DRY_RUN}")

    state = load_state()
    for s in symbols:
        ex.set_leverage(s, LEVERAGE)

    while True:
        try:
            equity = ex.account_balance() or BASE_EQUITY
            for sym in symbols:
                try:
                    kl = ex.klines(sym, interval=TIMEFRAME, limit=max(EMA_SLOW, DONCHIAN_LEN, ATR_LEN)+5)
                    df = build_df(kl)
                    sig, info = generate_signal(df, EMA_FAST, EMA_SLOW, DONCHIAN_LEN, ATR_LEN)
                    last_price = info["entry_price"]
                    atr_val = info["atr"]
                    if math.isnan(atr_val) or atr_val <= 0: continue

                    pos = state["positions"].get(sym, {})
                    side = pos.get("side")

                    if not side:
                        if sig in ("LONG","SHORT"):
                            qty_calc = compute_position_size(equity, RISK_PCT, atr_val, last_price, sig, sym, ex)
                            if qty_calc > 0:
                                _ = place_entry_and_sl(ex, sym, sig, qty_calc, last_price, atr_val)
                                state["positions"][sym] = {"side": sig, "qty": qty_calc, "entry_price": last_price, "trail": initial_stop(last_price, atr_val, sig, ATR_MULT_SL), "adds_done": 0, "last_add_price": last_price}
                                print(f"[{sym}] OPEN {sig} qty={qty_calc} reason={info['reason']} entry={last_price} ATR={atr_val}")
                    else:
                        manage_trailing_and_pyramid(ex, sym, side, df, state["positions"][sym])
                        trail = state["positions"][sym].get("trail")
                        if trail is not None and ((side=='LONG' and last_price <= trail) or (side=='SHORT' and last_price >= trail)):
                            print(f"[{sym}] EXIT detected by price<=trail. Clearing state.")
                            state["positions"][sym] = {}

                except Exception as e_sym:
                    print(f"[ERR] symbol {sym}: {e_sym}")

            save_state(state)
            time.sleep(LOOP_SLEEP_SECS)
        except KeyboardInterrupt:
            print("bye"); break
        except Exception as e:
            print(f"[LOOP ERR] {e}"); time.sleep(5)

if __name__ == "__main__":
    main()
