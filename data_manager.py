# -*- coding: utf-8 -*-
"""
کلاس‌های دریافت داده و صرافی
"""

import os
import time
import hmac
import hashlib
import logging
import requests
import pandas as pd

from config import (
    BASE_URL,
    CACHE_DIR,
    HISTORY_BARS,
    TICK_SIZES,
    PRICE_PRECISION,
    SYMBOLS,
)

from utils import send_telegram_message, format_iran_time

logger = logging.getLogger(__name__)


class TrueTradePublicData:
    def __init__(self):
        self.base_url = BASE_URL
        self._data_cache = {}
        os.makedirs(CACHE_DIR, exist_ok=True)
        self._load_cached_data()

    def _get_cache_file(self, symbol):
        return os.path.join(CACHE_DIR, f"{symbol.lower()}_1m.csv")

    def _load_cached_data(self):
        for symbol in SYMBOLS:
            cache_file = self._get_cache_file(symbol)
            if os.path.exists(cache_file):
                try:
                    df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
                    if not df.empty:
                        if df.index.tz is None:
                            df.index = df.index.tz_localize('UTC')
                        else:
                            df.index = df.index.tz_convert('UTC')
                        self._data_cache[symbol] = df
                        logger.info(f"[CACHE] Loaded {len(df)} candles for {symbol}")
                except Exception as e:
                    logger.error(f"[CACHE] Error loading {symbol}: {e}")

    def _save_cached_data(self, symbol):
        if symbol in self._data_cache:
            try:
                self._data_cache[symbol].to_csv(self._get_cache_file(symbol))
            except Exception as e:
                logger.error(f"[CACHE] Error saving {symbol}: {e}")

    def fetch_ohlcv(self, symbol, timeframe='1m', limit=HISTORY_BARS):
        symbol_clean = symbol.upper()
        resolution_map = {
            "1m": "1", "5m": "5", "15m": "15", "30m": "30",
            "1h": "60", "4h": "240", "1d": "D", "1w": "W", "1M": "M"
        }
        resolution = resolution_map.get(timeframe, "1")

        if symbol_clean in self._data_cache and not self._data_cache[symbol_clean].empty:
            cached_df = self._data_cache[symbol_clean]
            from_timestamp = int(cached_df.index[-1].timestamp()) + 60
        else:
            from_timestamp = int(time.time()) - (limit * 60)
            cached_df = None

        to_timestamp = int(time.time())
        uri = f"/futures/udf/history?symbol={symbol_clean}&resolution={resolution}&from={from_timestamp}&to={to_timestamp}&countback={limit}"

        try:
            response = requests.get(f"{self.base_url}{uri}", timeout=15)
            response.raise_for_status()
            data = response.json()

            if not data or data.get('s') != 'ok':
                return cached_df if cached_df is not None else None

            new_df = pd.DataFrame({
                'timestamp': pd.to_datetime(data['t'], unit='s', utc=True),
                'open': pd.to_numeric(data['o']),
                'high': pd.to_numeric(data['h']),
                'low': pd.to_numeric(data['l']),
                'close': pd.to_numeric(data['c']),
                'volume': pd.to_numeric(data['v'])
            })
            new_df.set_index('timestamp', inplace=True)

            if cached_df is not None and not cached_df.empty:
                new_df = new_df[~new_df.index.isin(cached_df.index)]
                if new_df.empty:
                    return cached_df
                
                combined_df = pd.concat([cached_df, new_df])
                combined_df = combined_df[~combined_df.index.duplicated(keep='last')]
                combined_df.sort_index(inplace=True)
                
                if len(combined_df) > HISTORY_BARS * 2:
                    combined_df = combined_df.tail(HISTORY_BARS * 2)
                
                self._data_cache[symbol_clean] = combined_df
                self._save_cached_data(symbol_clean)
                logger.info(f"[FETCH] {symbol_clean}: +{len(new_df)} new, total={len(combined_df)}")
                return combined_df
            else:
                if len(new_df) > HISTORY_BARS:
                    new_df = new_df.tail(HISTORY_BARS)
                self._data_cache[symbol_clean] = new_df
                self._save_cached_data(symbol_clean)
                logger.info(f"[FETCH] {symbol_clean}: Initial {len(new_df)} candles")
                return new_df

        except Exception as e:
            logger.error(f"[FETCH ERROR] {symbol}: {e}")
            return cached_df if cached_df is not None else None


class TrueTradePrivateExchange:
    def __init__(self, api_key, api_secret, base_url):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self.session = requests.Session()
        self.connected = False
        self._last_response = None

    def _sign_request(self, method, uri, timestamp):
        payload = f"{timestamp}{method.upper()}{uri}"
        return hmac.new(self.api_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    def _request(self, method, uri, data=None):
        timestamp = str(int(time.time() * 1000))
        signature = self._sign_request(method, uri, timestamp)
        headers = {
            "X-API-Key": self.api_key,
            "X-Timestamp": timestamp,
            "X-Signature": signature,
            "Content-Type": "application/json"
        }
        response = self.session.request(method, f"{self.base_url}{uri}", headers=headers, json=data, timeout=15)
        
        self._last_response = response
        
        if not response.ok:
            if response.status_code in [401, 403]:
                self.connected = False
            logger.error(f"[EXCHANGE ERROR] {method} {uri} | Status: {response.status_code} | Body: {response.text[:500]}")
            response.raise_for_status()
        else:
            self.connected = True
        return response.json()

    def test_connection(self):
        try:
            self._request('GET', '/futures/positions')
            self.connected = True
            return True
        except Exception as e:
            self.connected = False
            logger.error(f"[EXCHANGE] اتصال برقرار نیست: {e}")
            return False

    def fetch_balance(self):
        try:
            timestamp = str(int(time.time() * 1000))
            signature = self._sign_request("GET", "/futures/assets", timestamp)

            response = self.session.get(
                f"{self.base_url}/futures/assets",
                headers={
                    "X-API-Key": self.api_key,
                    "X-Timestamp": timestamp,
                    "X-Signature": signature,
                    "Content-Type": "application/json"
                },
                timeout=15
            )

            response.raise_for_status()
            data = response.json()

            assets_list = []
            if isinstance(data, dict) and 'assets' in data:
                assets_list = data['assets']
            elif isinstance(data, list):
                assets_list = data

            for asset in assets_list:
                if asset.get('symbol') == 'USDT':
                    balance = float(asset.get('availableBalance', asset.get('totalAssets', 0)))
                    logger.info(f"[BALANCE] Futures USDT: {balance:.2f}")
                    return balance

            return 0

        except Exception as e:
            logger.error(f"[BALANCE ERROR] {e}")
            return None

    def fetch_trade_history(self, symbol=None, start_time=None, end_time=None):
        params = {}
        if symbol:
            params['symbol'] = symbol.upper()
        if start_time:
            params['start'] = start_time
        if end_time:
            params['end'] = end_time
        
        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        uri = f"/futures/trades{'?' + query_string if query_string else ''}"
        
        try:
            data = self._request('GET', uri)
            logger.info(f"[TRADE HISTORY] Retrieved {len(data) if isinstance(data, list) else 'non-list'} trades.")
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"[TRADE HISTORY ERROR] {e}")
            return []

    def fetch_open_positions(self):
        try:
            data = self._request('GET', '/futures/positions?active=true')
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"[FETCH POSITIONS ERROR] {e}")
            return []

    def create_order(self, symbol, order_type, side, capital, price=None, params=None):
        if params:
            if 'stopLoss' in params:
                params['stopLoss'] = self._round_price(params['stopLoss'], symbol)
            if 'takeProfit' in params:
                params['takeProfit'] = self._round_price(params['takeProfit'], symbol)

        prec = PRICE_PRECISION.get(symbol.upper(), 2)

        order_data = {
            "symbol": symbol.upper(),
            "side": side.upper(),
            "tradeType": order_type.upper(),
            "leverage": params.get('leverage', 1) if params else 1,
            "cost": f"{capital:.{prec}f}",
            "walletType": "debit"
        }

        if order_type.upper() == "LIMIT" and price:
            order_data["price"] = str(price)

        if params:
            if 'stopLoss' in params:
                order_data["stopLoss"] = f"{params['stopLoss']:.{prec}f}"
            if 'takeProfit' in params:
                order_data["takeProfit"] = f"{params['takeProfit']:.{prec}f}"

        send_telegram_message(
            f"📤 ثبت سفارش - درخواست\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔹 Symbol: {symbol}\n"
            f"🔸 Side: {side.upper()}\n"
            f"🔸 Type: {order_type.upper()}\n"
            f"💰 Cost: {capital:.{prec}f}\n"
            f"🔧 Leverage: {order_data['leverage']}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🕒 {format_iran_time()}"
        )

        try:
            result = self._request('POST', '/futures/positions', order_data)

            send_telegram_message(
                f"📥 ثبت سفارش - پاسخ\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🔹 Symbol: {symbol}\n"
                f"✅ Success - Position ID: {result.get('positionId', 'N/A')}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🕒 {format_iran_time()}"
            )

            return {
                'id': result.get('positionId'),
                'symbol': symbol,
                'side': side,
                'type': order_type,
                'capital': capital
            }

        except Exception as e:
            error_detail = ""
            error_body = ""
            status_code = ""
            
            if hasattr(self, '_last_response'):
                response = self._last_response
                status_code = response.status_code
                try:
                    error_body = response.text
                    error_json = response.json()
                    if 'errors' in error_json:
                        if isinstance(error_json['errors'], list):
                            for err in error_json['errors']:
                                error_detail += f"• {err.get('message', '')}"
                                if err.get('field'):
                                    error_detail += f" (field: {err['field']})"
                                error_detail += "\n"
                        elif isinstance(error_json['errors'], dict):
                            for field, msgs in error_json['errors'].items():
                                if isinstance(msgs, list):
                                    for msg in msgs:
                                        error_detail += f"• {field}: {msg}\n"
                                else:
                                    error_detail += f"• {field}: {msgs}\n"
                    elif 'message' in error_json:
                        error_detail = error_json['message']
                except:
                    error_detail = error_body[:500]

            send_telegram_message(
                f"❌ ثبت سفارش - خطا\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🔹 Symbol: {symbol}\n"
                f"🔸 Side: {side.upper()}\n"
                f"📊 Status: {status_code}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📝 دلیل خطا:\n{error_detail if error_detail else str(e)[:200]}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🕒 {format_iran_time()}"
            )
            raise

    def _round_price(self, price, symbol):
        tick = TICK_SIZES.get(symbol.upper(), 0.01)
        precision = PRICE_PRECISION.get(symbol.upper(), 2)
        rounded = round(price / tick) * tick
        return round(rounded, precision)
