import os
from typing import Dict, Any, List, Optional
from binance.client import Client
from binance.exceptions import BinanceAPIException

TESTNET = os.getenv('TESTNET', 'true').lower() == 'true'

class Exchange:
    def __init__(self, api_key: str, api_secret: str):
        self.client = Client(api_key, api_secret, testnet=TESTNET)
        self.client.FUTURES_URL = 'https://testnet.binancefuture.com' if TESTNET else 'https://fapi.binance.com'

    def set_one_way_mode(self):
        try:
            return self.client.futures_change_position_mode(dualSidePosition=False)
        except BinanceAPIException as e:
            print(f'[WARN] set_one_way_mode failed: {e}')

    def set_leverage(self, symbol: str, leverage: int = 30):
        try:
            return self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
        except BinanceAPIException as e:
            print(f'[WARN] set_leverage {symbol} failed: {e}')

    def account_balance(self) -> float:
        try:
            balances = self.client.futures_account_balance()
            for b in balances:
                if b.get('asset') == 'USDT':
                    return float(b.get('balance', 0))
        except BinanceAPIException as e:
            print(f'[WARN] account_balance failed: {e}')
        return 0.0

    def klines(self, symbol: str, interval: str = '5m', limit: int = 500) -> List[List[Any]]:
        return self.client.futures_klines(symbol=symbol, interval=interval, limit=limit)

    def exchange_info(self) -> Dict[str, Any]:
        return self.client.futures_exchange_info()

    def ticker_price(self, symbol: str) -> float:
        t = self.client.futures_symbol_ticker(symbol=symbol)
        return float(t['price'])

    def top_symbols_by_quote_volume(self, limit: int = 10) -> List[str]:
        stats = self.client.futures_ticker()
        usdt_pairs = [s for s in stats if str(s.get('symbol','')).endswith('USDT')]
        usdt_pairs.sort(key=lambda x: float(x.get('quoteVolume', 0.0)), reverse=True)
        return [x['symbol'] for x in usdt_pairs[:limit]]

    def new_market_order(self, symbol: str, side: str, quantity: float, reduce_only: bool = False):
        params = dict(symbol=symbol, side=side, type='MARKET', quantity=str(quantity))
        if reduce_only:
            params['reduceOnly'] = True
        return self.client.futures_create_order(**params)

    def new_stop_market_close(self, symbol: str, side: str, stop_price: float):
        order_side = 'SELL' if side == 'LONG' else 'BUY'
        params = dict(symbol=symbol, side=order_side, type='STOP_MARKET', stopPrice=self._fmt_price(symbol, stop_price), closePosition=True, timeInForce='GTC')
        return self.client.futures_create_order(**params)

    def cancel_all(self, symbol: str):
        try:
            return self.client.futures_cancel_all_open_orders(symbol=symbol)
        except BinanceAPIException as e:
            print(f'[WARN] cancel_all {symbol} failed: {e}')

    def position_info(self, symbol: Optional[str] = None):
        if symbol:
            return self.client.futures_position_information(symbol=symbol)
        return self.client.futures_position_information()

    def symbol_filters(self) -> Dict[str, Dict[str, float]]:
        info = self.exchange_info()
        out: Dict[str, Dict[str, float]] = {}
        for s in info.get('symbols', []):
            if s.get('status') != 'TRADING':
                continue
            sym = s['symbol']
            f = {'stepSize': 0.0, 'tickSize': 0.0, 'minQty': 0.0, 'minNotional': 0.0}
            for flt in s.get('filters', []):
                if flt['filterType'] == 'LOT_SIZE':
                    f['stepSize'] = float(flt['stepSize']); f['minQty'] = float(flt['minQty'])
                elif flt['filterType'] == 'PRICE_FILTER':
                    f['tickSize'] = float(flt['tickSize'])
                elif flt['filterType'] == 'MIN_NOTIONAL':
                    f['minNotional'] = float(flt.get('notional', 0.0))
            out[sym] = f
        return out

    def _fmt_price(self, symbol: str, price: float) -> str:
        tick = self._tick_size.get(symbol, 0.01)
        if tick > 0:
            precision = max(0, min(8, len(str(tick).split('.')[-1]) if '.' in str(tick) else 0))
            price = round(round(price / tick) * tick, precision)
        return f"{price:.8f}".rstrip('0').rstrip('.')

    def _fmt_qty(self, symbol: str, qty: float) -> float:
        step = self._step_size.get(symbol, 0.001)
        if step > 0:
            qty = round(round(qty / step) * step, 8)
        return max(qty, self._min_qty.get(symbol, 0.0))

    def prime_filters(self):
        filters = self.symbol_filters()
        self._tick_size = {s: v.get('tickSize', 0.01) for s, v in filters.items()}
        self._step_size = {s: v.get('stepSize', 0.001) for s, v in filters.items()}
        self._min_qty = {s: v.get('minQty', 0.0) for s, v in filters.items()}
        self._min_notional = {s: v.get('minNotional', 0.0) for s, v in filters.items()}

    def round_price(self, symbol: str, price: float) -> float:
        return float(self._fmt_price(symbol, price))

    def round_qty(self, symbol: str, qty: float) -> float:
        return float(self._fmt_qty(symbol, qty))

    def min_notional(self, symbol: str) -> float:
        return float(getattr(self, '_min_notional', {}).get(symbol, 0.0))
