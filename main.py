# -*- coding: utf-8 -*-
"""
DTM Divergence Auto-Trading Bot - TheTrueTrade
===============================================
منطق تشخیص سیگنال: دقیقاً مطابق PyneCore اصلی (بدون MTF)
"""

import os
import time
import threading
import hashlib
import hmac
import requests
import json
import logging
import re
import math
from datetime import datetime, timezone, timedelta
from flask import Flask
import pandas as pd
import numpy as np

# =====================================================================================
# تنظیمات logging
# =====================================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# =====================================================================================
# کلیدهای API — فقط از متغیر محیطی
# =====================================================================================
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
BASE_URL = os.getenv("BASE_URL", "https://apiv2.thetruetrade.io")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not API_KEY or not API_SECRET:
    raise RuntimeError("API_KEY / API_SECRET باید به‌عنوان متغیر محیطی ست شوند.")
if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID باید به‌عنوان متغیر محیطی ست شوند.")

HISTORY_FILE = "trades_history_hybrid.json"
STATE_FILE = "pivot_state.json"
CACHE_DIR = "ohlcv_cache"

# =====================================================================================
# هشتگ‌ها
# =====================================================================================
HASHTAGS = {
    "startup": "#Online",
    "diagnostic": "#Diagnostic",
    "signal": "#Signal",
    "log": "#Log",
    "alert": "#Alert",
    "pivot": "#Pivot",
    "target": "#Target",
    "stop": "#Stop",
    "daily": "#Daily",
    "monthly": "#Monthly",
    "order_request": "#OrderReq",
    "order_response": "#OrderOK",
    "order_error": "#OrderErr",
    "connection": "#Connected",
    "connection_change": "#Reconnected",
    "capital_reduced": "#LowCapital",
}

# =====================================================================================
# ثابت‌های استراتژی (مطابق PyneCore اصلی)
# =====================================================================================
PIVOT_MODE = "سریع (5/3)"
RSI_LEN = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIG = 9
TREND_LOOKBACK = 20
TREND_SLOPE_MIN_PCT = 0.05
MIN_CONFIRMATIONS = "۳ تعییدیه (حداقل مجاز)"
ENABLE_HIDDEN = True
ENABLE_MACD_COLOR_FILTER = True  # فیلتر تغییر رنگ Histogram
FIB_USE_618 = True
FIB_USE_786 = True
FIB_TOLERANCE_PCT = 0.5
FIB_TREND_SEARCH_BARS = 100
SHADOW_TO_BODY_RATIO = 2.0
MAX_OPPOSITE_SHADOW_PCT = 20.0
MIN_CANDLE_ATR_RATIO = 0.3
BIG_CANDLE_AVG_LEN = 14
BIG_CANDLE_MULTIPLIER = 1.5
ENABLE_MTF = False
MTF_TIMEFRAME = "240"

LEFT_BARS = 5
RIGHT_BARS = 5 if PIVOT_MODE == 'استاندارد (5/5)' else 3
STOP_BUFFER_PCT = 0.05
HISTORY_BARS = 500
API_RETURNS_OPEN_CANDLE = False

# =====================================================================================
# Tick Size و Price Precision
# =====================================================================================
TICK_SIZES = {
    "LTCUSDT": 0.01,
    "DOGEUSDT": 0.00001,
    "ETHUSDT": 0.01,
}

PRICE_PRECISION = {
    "LTCUSDT": 2,
    "DOGEUSDT": 5,
    "ETHUSDT": 2,
}

# =====================================================================================
# کلاس دریافت داده (با کش پیوسته - CSV به جای parquet)
# =====================================================================================
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

# =====================================================================================
# کلاس صرافی
# =====================================================================================
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

# =====================================================================================
# ======================== توابع کمکی سراسری =========================================
# =====================================================================================

def send_telegram_message(message: str):
    try:
        clean_message = re.sub(r'```[^`]*```', '', message)
        clean_message = re.sub(r'[*_~`]', '', clean_message)
        if len(clean_message) > 4000:
            clean_message = clean_message[:4000] + "\n... (ادامه)"
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        response = requests.post(
            url, 
            json={
                "chat_id": TELEGRAM_CHAT_ID, 
                "text": clean_message
            }, 
            timeout=30
        )
        if response.status_code != 200:
            logger.error(f"[TELEGRAM] Status: {response.status_code}, Response: {response.text[:200]}")
    except Exception as e:
        logger.error(f"[TELEGRAM] Error: {e}")

def format_iran_time(dt=None):
    if dt is None:
        dt = datetime.now(timezone(timedelta(hours=3, minutes=30)))
    return dt.strftime('%Y-%m-%d %H:%M:%S')

def format_iran_date(dt=None):
    if dt is None:
        dt = datetime.now(timezone(timedelta(hours=3, minutes=30)))
    return dt.strftime('%Y-%m-%d')

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE) as f:
                return json.load(f)
        except:
            return []
    return []

def save_history(h):
    with open(HISTORY_FILE, 'w') as f:
        json.dump(h, f, indent=2)

def get_next_signal_number():
    global SIGNAL_COUNTER
    SIGNAL_COUNTER += 1
    return SIGNAL_COUNTER

def load_signal_counter():
    global SIGNAL_COUNTER
    history = load_history()
    if history:
        SIGNAL_COUNTER = len(history)
    else:
        SIGNAL_COUNTER = 0

def update_trade_result(signal_time, result, close_price, close_time, pnl=None, commission=None):
    h = load_history()
    for t in h:
        if t.get('signal_time') == signal_time:
            t['result'] = result
            t['close_price'] = close_price
            t['close_time'] = close_time
            if pnl is not None:
                t['realized_pnl'] = pnl
            if commission is not None:
                t['commission'] = commission
            logger.info(f"[HISTORY] Updated trade {signal_time}: Result={result}, PnL={pnl}")
            break
    save_history(h)

def save_debug_log_to_file(symbol, debug_log_lines):
    try:
        today = format_iran_date()
        log_file = "full_debug_log.txt"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n{'═' * 80}\n")
            f.write(f"📅 DATE: {today} | SYMBOL: {symbol}\n")
            f.write(f"{'═' * 80}\n\n")
            for line in debug_log_lines:
                f.write(line + "\n")
            f.write("-" * 70 + "\n\n")
    except Exception as e:
        logger.error(f"[DEBUG FILE] Error writing log: {e}")

# =====================================================================================
# ======================== توابع محاسباتی (مطابق PyneCore) ==========================
# =====================================================================================

def calc_rma(series, length):
    """معادل ta.rma در Pine Script"""
    n = len(series)
    rma = pd.Series(np.nan, index=series.index)
    if n == 0:
        return rma
    alpha = 1.0 / length
    vals = series.to_numpy(dtype=float)

    leading_na = 0
    while leading_na < n and np.isnan(vals[leading_na]):
        leading_na += 1

    seed_idx = leading_na + length - 1
    if seed_idx >= n:
        return rma

    prev = vals[seed_idx - length + 1: seed_idx + 1].mean()
    rma.iloc[seed_idx] = prev
    for i in range(seed_idx + 1, n):
        prev = alpha * vals[i] + (1 - alpha) * prev
        rma.iloc[i] = prev
    return rma

def calc_rsi(close, length=14):
    """معادل ta.rsi در Pine Script"""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = calc_rma(gain, length)
    avg_loss = calc_rma(loss, length)
    rsi = pd.Series(np.nan, index=close.index)
    for i in range(len(close)):
        ag = avg_gain.iloc[i]
        al = avg_loss.iloc[i]
        if pd.isna(ag) or pd.isna(al):
            continue
        if al == 0:
            rsi.iloc[i] = 100.0
        elif ag == 0:
            rsi.iloc[i] = 0.0
        else:
            rsi.iloc[i] = 100.0 - (100.0 / (1.0 + ag / al))
    return rsi

def calc_macd(close, fast=12, slow=26, signal=9):
    """معادل ta.macd در Pine Script"""
    def calc_ema(series, length):
        alpha = 2.0 / (length + 1)
        ema = pd.Series(np.nan, index=series.index)
        if len(series) == 0:
            return ema
        first_valid = series.first_valid_index()
        if first_valid is None:
            return ema
        start_pos = series.index.get_loc(first_valid)
        ema.iloc[start_pos] = series.iloc[start_pos]
        prev = ema.iloc[start_pos]
        for i in range(start_pos + 1, len(series)):
            prev = alpha * series.iloc[i] + (1 - alpha) * prev
            ema.iloc[i] = prev
        return ema
    
    ema_fast = calc_ema(close, fast)
    ema_slow = calc_ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calc_ema(macd_line, signal)
    hist_line = macd_line - signal_line
    return macd_line, signal_line, hist_line

def calc_atr(high, low, close, length=14):
    """معادل ta.atr در Pine Script"""
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return calc_rma(tr, length)

def calc_linreg(series, length, offset=0):
    """معادل ta.linreg در Pine Script"""
    result = pd.Series(np.nan, index=series.index)
    for i in range(length - 1, len(series)):
        y = series.iloc[i - length + 1: i + 1].values
        x = np.arange(length)
        slope, intercept = np.polyfit(x, y, 1)
        result.iloc[i] = intercept + slope * (length - 1 + offset)
    return result

def calc_sma(series, length):
    """معادل ta.sma در Pine Script"""
    return series.rolling(window=length).mean()

def calc_lowest(series, length):
    """معادل ta.lowest در Pine Script"""
    return series.rolling(window=length).min()

def calc_highest(series, length):
    """معادل ta.highest در Pine Script"""
    return series.rolling(window=length).max()

def find_pivot_high(high, left_bars=LEFT_BARS, right_bars=RIGHT_BARS):
    """
    معادل دقیق ta.pivothigh در Pine Script
    اصلاح‌شده: استفاده از > به جای >= برای تطبیق با Pine
    """
    n = len(high)
    result = pd.Series(np.nan, index=high.index, dtype=float)

    for i in range(left_bars, n - right_bars):
        candidate = high.iloc[i]

        # بررسی سمت چپ - استفاده از > برای تطبیق با Pine
        left_ok = True
        for j in range(1, left_bars + 1):
            if high.iloc[i - j] > candidate:
                left_ok = False
                break

        if not left_ok:
            continue

        # بررسی سمت راست - استفاده از > برای تطبیق با Pine
        right_ok = True
        for j in range(1, right_bars + 1):
            if high.iloc[i + j] > candidate:
                right_ok = False
                break

        if right_ok:
            # ثبت Pivot روی کندل مرکزی (i)
            result.iloc[i] = candidate

    return result

def find_pivot_low(low, left_bars=LEFT_BARS, right_bars=RIGHT_BARS):
    """
    معادل دقیق ta.pivotlow در Pine Script
    اصلاح‌شده: استفاده از < به جای <= برای تطبیق با Pine
    """
    n = len(low)
    result = pd.Series(np.nan, index=low.index, dtype=float)

    for i in range(left_bars, n - right_bars):
        candidate = low.iloc[i]

        # بررسی سمت چپ - استفاده از < برای تطبیق با Pine
        left_ok = True
        for j in range(1, left_bars + 1):
            if low.iloc[i - j] < candidate:
                left_ok = False
                break

        if not left_ok:
            continue

        # بررسی سمت راست - استفاده از < برای تطبیق با Pine
        right_ok = True
        for j in range(1, right_bars + 1):
            if low.iloc[i + j] < candidate:
                right_ok = False
                break

        if right_ok:
            # ثبت Pivot روی کندل مرکزی (i)
            result.iloc[i] = candidate

    return result

def histogram_changed_phase(hist_series, bar_start_ts, bar_end_ts):
    """
    بررسی تغییر واقعی فاز Histogram (عبور از خط صفر)
    بین bar_start و bar_end
    """
    found = False
    if bar_start_ts is not None and bar_end_ts is not None and bar_end_ts > bar_start_ts:
        try:
            start_pos = hist_series.index.get_loc(bar_start_ts)
            end_pos = hist_series.index.get_loc(bar_end_ts)
            
            # بررسی از bar_start+1 تا bar_end-1
            for i in range(start_pos + 1, end_pos):
                if i >= len(hist_series) or i + 1 >= len(hist_series):
                    break
                h1 = hist_series.iloc[i]
                h2 = hist_series.iloc[i + 1]
                
                if pd.isna(h1) or pd.isna(h2):
                    continue
                
                # عبور از مثبت به منفی یا منفی به مثبت
                crossed_up = h1 > 0 and h2 <= 0
                crossed_down = h1 < 0 and h2 >= 0
                
                if crossed_up or crossed_down:
                    found = True
                    break
        except KeyError:
            pass
    return found

def calc_linreg_pine(series, start_index, length):
    """
    معادل دقیق ta.linreg در Pine Script
    خروجی: مقدار انتهایی خط رگرسیون (نه شیب، نه وسط)
    """
    # بازه‌ی داده‌ها
    window = series.iloc[start_index : start_index + length]

    if len(window) < length:
        return np.nan

    y = window.values
    x = np.arange(length)

    # محاسبه شیب و عرض از مبدا
    slope, intercept = np.polyfit(x, y, 1)

    # مقدار انتهایی خط (دقیقاً مثل Pine)
    return intercept + slope * (length - 1)

def is_trending_up(close_series, ref_ts):
    """
    معادل دقیق isTrendingUp در Pine Script
    """
    if ref_ts is None:
        return False

    try:
        offset = close_series.index.get_loc(ref_ts)

        # باید دو بازه پشت‌سرهم داشته باشیم
        if offset < 0 or offset + TREND_LOOKBACK * 2 >= len(close_series):
            return False

        # linreg بازه اول
        linreg_current = calc_linreg_pine(close_series, offset, TREND_LOOKBACK)

        # linreg بازه دوم
        linreg_past = calc_linreg_pine(close_series, offset + TREND_LOOKBACK, TREND_LOOKBACK)

        # شیب
        slope = linreg_current - linreg_past

        # میانگین قیمت بازه اول
        avg_price = close_series.iloc[offset : offset + TREND_LOOKBACK].mean()

        slope_pct = (slope / avg_price) * 100 if avg_price != 0 else 0.0

        return slope_pct > TREND_SLOPE_MIN_PCT

    except (KeyError, IndexError):
        return False

def is_trending_down(close_series, ref_ts):
    """
    معادل دقیق isTrendingDown در Pine Script
    """
    if ref_ts is None:
        return False

    try:
        offset = close_series.index.get_loc(ref_ts)

        if offset < 0 or offset + TREND_LOOKBACK * 2 >= len(close_series):
            return False

        linreg_current = calc_linreg_pine(close_series, offset, TREND_LOOKBACK)
        linreg_past = calc_linreg_pine(close_series, offset + TREND_LOOKBACK, TREND_LOOKBACK)

        slope = linreg_current - linreg_past

        avg_price = close_series.iloc[offset : offset + TREND_LOOKBACK].mean()

        slope_pct = (slope / avg_price) * 100 if avg_price != 0 else 0.0

        return slope_pct < -TREND_SLOPE_MIN_PCT

    except (KeyError, IndexError):
        return False

def find_trend_start_low(low_series, ref_ts):
    """
    معادل دقیق findTrendStartLow در Pine Script
    برای واگرایی نزولی (روند صعودی): کمترین low در بازه‌ی شیفت‌شده
    اصلاح‌شده: فقط تا refBar بررسی می‌کند، بدون Look-ahead
    """
    if ref_ts is None:
        return None

    try:
        ref_bar = low_series.index.get_loc(ref_ts)
        current_index = len(low_series) - 1
        
        start = ref_bar - FIB_TREND_SEARCH_BARS + 1
        end = ref_bar + 1

        if start < 0 or end > current_index + 1:
            return None

        window = low_series.iloc[start:end]
        result = window.min()

        return None if pd.isna(result) else float(result)

    except (KeyError, IndexError):
        return None

def find_trend_start_high(high_series, ref_ts):
    """
    معادل دقیق findTrendStartHigh در Pine Script
    برای واگرایی صعودی (روند نزولی): بیشترین high در بازه‌ی شیفت‌شده
    اصلاح‌شده: فقط تا refBar بررسی می‌کند، بدون Look-ahead
    """
    if ref_ts is None:
        return None

    try:
        ref_bar = high_series.index.get_loc(ref_ts)
        current_index = len(high_series) - 1
        
        start = ref_bar - FIB_TREND_SEARCH_BARS + 1
        end = ref_bar + 1

        if start < 0 or end > current_index + 1:
            return None

        window = high_series.iloc[start:end]
        result = window.max()

        return None if pd.isna(result) else float(result)

    except (KeyError, IndexError):
        return None

def check_fib_level(fib_start, fib_end, target_price, is_retrace_down):
    """
    معادل دقیق checkFibLevel در Pine Script
    بررسی سطح 0.618 و 0.786 با تلورانس
    اصلاح‌شده: کنترل جهت صحیح distance
    """
    if fib_start is None or fib_end is None or target_price is None:
        return False
    if fib_start == fib_end:
        return False

    ok = False

    if is_retrace_down:
        # روند صعودی → اصلاح نزولی
        distance = fib_end - fib_start
        
        if distance <= 0:
            return False
        
        level618 = fib_end - distance * 0.618
        level786 = fib_end - distance * 0.786
    else:
        # روند نزولی → اصلاح صعودی
        distance = fib_start - fib_end
        
        if distance <= 0:
            return False
        
        level618 = fib_end + distance * 0.618
        level786 = fib_end + distance * 0.786

    tolerance = distance * (FIB_TOLERANCE_PCT / 100.0)

    valid618 = FIB_USE_618 and abs(target_price - level618) <= tolerance
    valid786 = FIB_USE_786 and abs(target_price - level786) <= tolerance

    ok = valid618 or valid786

    return ok

def candle_confirmation(
    open_series,
    close_series,
    high_series,
    low_series,
    atr_series,
    bigCandleAvgLen,
    shadowToBodyRatio,
    maxOppositeShadowPct,
    minCandleATRRatio,
    bigCandleMultiplier,
    pivot_ts
):
    """
    معادل دقیق کندل تأییدیه در Pine Script
    اصلاح‌شده: محاسبه روی کندل Pivot با [rightBars]
    خروجی:
        bullish_confirmed (bool)
        bearish_confirmed (bool)
    """

    try:
        pivot_pos = open_series.index.get_loc(pivot_ts)
    except KeyError:
        return False, False

    # داده‌های کندل Pivot دوم (روی کندل Pivot واقعی)
    o = open_series.iloc[pivot_pos]
    c = close_series.iloc[pivot_pos]
    h = high_series.iloc[pivot_pos]
    l = low_series.iloc[pivot_pos]

    pivot_range = h - l
    pivot_body = abs(c - o)
    upper_shadow = h - max(c, o)
    lower_shadow = min(c, o) - l

    # ATR روی کندل Pivot
    atr_at_pivot = atr_series.iloc[pivot_pos]

    # میانگین بدنه برای کندل بزرگ روی کندل Pivot
    if pivot_pos - bigCandleAvgLen >= 0:
        avg_body_at_pivot = abs(
            close_series.iloc[pivot_pos - bigCandleAvgLen: pivot_pos] - 
            open_series.iloc[pivot_pos - bigCandleAvgLen: pivot_pos]
        ).mean()
    else:
        avg_body_at_pivot = pivot_body

    # شرط حداقل اندازه کندل نسبت به ATR
    pivot_size_ok = (
        pivot_range > 0
        and not pd.isna(atr_at_pivot)
        and pivot_range >= minCandleATRRatio * atr_at_pivot
    )

    # ============================
    # Bullish Confirmation
    # ============================

    # Pin Bar / Hammer (بدون شرط رنگ، مطابق کد اصلی)
    bullish_wick = (
        pivot_size_ok
        and lower_shadow >= shadowToBodyRatio * pivot_body
        and (upper_shadow / pivot_range) * 100 <= maxOppositeShadowPct
    )

    # Big Green Candle
    big_green = (
        pivot_size_ok
        and not pd.isna(avg_body_at_pivot)
        and c > o
        and pivot_body >= bigCandleMultiplier * avg_body_at_pivot
    )

    bullishconfirmed = bullish_wick or big_green

    # ============================
    # Bearish Confirmation
    # ============================

    # Shooting Star (بدون شرط رنگ، مطابق کد اصلی)
    bearish_wick = (
        pivot_size_ok
        and upper_shadow >= shadowToBodyRatio * pivot_body
        and (lower_shadow / pivot_range) * 100 <= maxOppositeShadowPct
    )

    # Big Red Candle
    big_red = (
        pivot_size_ok
        and not pd.isna(avg_body_at_pivot)
        and c < o
        and pivot_body >= bigCandleMultiplier * avg_body_at_pivot
    )

    bearishconfirmed = bearish_wick or big_red

    return bullishconfirmed, bearishconfirmed

def passes_min_requirement(base3, fib_ok, pa_ok, color_filter_ok):
    """
    تابع نهایی تأییدها با فیلتر تغییر رنگ مستقل
    """
    result = False
    if base3 and color_filter_ok:
        if MIN_CONFIRMATIONS == '۳ تعییدیه (حداقل مجاز)':
            result = True
        elif MIN_CONFIRMATIONS == '۳ تعییدیه + فیبوناچی (۴ امتیاز) [Custom]':
            result = fib_ok
        elif MIN_CONFIRMATIONS == '۳ تعییدیه + پرایس‌اکشن (۴ امتیاز) [Custom]':
            result = pa_ok
        elif MIN_CONFIRMATIONS == '۵ امتیاز کامل (ایده‌آل)':
            result = fib_ok and pa_ok
    return result

def calc_price_action(df, atr_series):
    """
    معادل محاسبات Price Action در PyneCore
    """
    candle_range = df['high'] - df['low']
    candle_body = (df['close'] - df['open']).abs()
    upper_shadow = df['high'] - df[['close', 'open']].max(axis=1)
    lower_shadow = df[['close', 'open']].min(axis=1) - df['low']
    avg_body = calc_sma(candle_body, BIG_CANDLE_AVG_LEN)
    size_ok = candle_range >= MIN_CANDLE_ATR_RATIO * atr_series
    
    # Bullish
    bullish_wick = (candle_range > 0) & \
                   (lower_shadow >= SHADOW_TO_BODY_RATIO * candle_body) & \
                   ((upper_shadow / candle_range * 100) <= MAX_OPPOSITE_SHADOW_PCT) & \
                   size_ok
    big_green_candle = (df['close'] > df['open']) & \
                       (candle_body >= BIG_CANDLE_MULTIPLIER * avg_body) & \
                       size_ok
    price_action_bullish = bullish_wick | big_green_candle
    
    # Bearish
    bearish_wick = (candle_range > 0) & \
                   (upper_shadow >= SHADOW_TO_BODY_RATIO * candle_body) & \
                   ((lower_shadow / candle_range * 100) <= MAX_OPPOSITE_SHADOW_PCT) & \
                   size_ok
    bearish_hanging_man = (candle_range > 0) & \
                          (lower_shadow >= SHADOW_TO_BODY_RATIO * candle_body) & \
                          ((upper_shadow / candle_range * 100) <= MAX_OPPOSITE_SHADOW_PCT) & \
                          size_ok
    big_red_candle = (df['close'] < df['open']) & \
                     (candle_body >= BIG_CANDLE_MULTIPLIER * avg_body) & \
                     size_ok
    price_action_bearish = bearish_wick | bearish_hanging_man | big_red_candle
    
    return price_action_bullish, price_action_bearish

# =====================================================================================
# کلاس وضعیت (مطابق PyneCore Persistent)
# =====================================================================================
class SymbolState:
    def __init__(self):
        # Pivot High state
        self.ph_price_2 = None
        self.ph_price_1 = None
        self.ph_ts_2 = None
        self.ph_ts_1 = None
        self.ph_bar_2 = None
        self.ph_bar_1 = None
        self.ph_rsi_2 = None
        self.ph_rsi_1 = None
        self.ph_macdline_2 = None
        self.ph_macdline_1 = None
        self.ph_hist_2 = None
        self.ph_hist_1 = None
        
        # Pivot Low state
        self.pl_price_2 = None
        self.pl_price_1 = None
        self.pl_ts_2 = None
        self.pl_ts_1 = None
        self.pl_bar_2 = None
        self.pl_bar_1 = None
        self.pl_rsi_2 = None
        self.pl_rsi_1 = None
        self.pl_macdline_2 = None
        self.pl_macdline_1 = None
        self.pl_hist_2 = None
        self.pl_hist_1 = None
        
        # لیست pivot ها برای محاسبه stop و target
        self.pivot_highs = []
        self.pivot_lows = []
        
        # Stored Stop و Target برای مدیریت موقعیت
        self.stored_long_stop = None
        self.stored_short_stop = None
        self.stored_long_tp = None
        self.stored_short_tp = None
        
        self.last_processed_ts = None
        self.last_processed_pivot_bar = None
        self.alert_sent = False

    def to_dict(self):
        return {
            'ph_price_2': self.ph_price_2,
            'ph_price_1': self.ph_price_1,
            'ph_ts_2': str(self.ph_ts_2) if self.ph_ts_2 else None,
            'ph_ts_1': str(self.ph_ts_1) if self.ph_ts_1 else None,
            'ph_bar_2': self.ph_bar_2,
            'ph_bar_1': self.ph_bar_1,
            'ph_rsi_2': self.ph_rsi_2,
            'ph_rsi_1': self.ph_rsi_1,
            'ph_macdline_2': self.ph_macdline_2,
            'ph_macdline_1': self.ph_macdline_1,
            'ph_hist_2': self.ph_hist_2,
            'ph_hist_1': self.ph_hist_1,
            'pl_price_2': self.pl_price_2,
            'pl_price_1': self.pl_price_1,
            'pl_ts_2': str(self.pl_ts_2) if self.pl_ts_2 else None,
            'pl_ts_1': str(self.pl_ts_1) if self.pl_ts_1 else None,
            'pl_bar_2': self.pl_bar_2,
            'pl_bar_1': self.pl_bar_1,
            'pl_rsi_2': self.pl_rsi_2,
            'pl_rsi_1': self.pl_rsi_1,
            'pl_macdline_2': self.pl_macdline_2,
            'pl_macdline_1': self.pl_macdline_1,
            'pl_hist_2': self.pl_hist_2,
            'pl_hist_1': self.pl_hist_1,
            'pivot_highs': [
                {'price': p['price'], 'ts': str(p['ts'])} 
                for p in self.pivot_highs
            ] if self.pivot_highs else [],
            'pivot_lows': [
                {'price': p['price'], 'ts': str(p['ts'])} 
                for p in self.pivot_lows
            ] if self.pivot_lows else [],
            'stored_long_stop': self.stored_long_stop,
            'stored_short_stop': self.stored_short_stop,
            'stored_long_tp': self.stored_long_tp,
            'stored_short_tp': self.stored_short_tp,
            'last_processed_ts': str(self.last_processed_ts) if self.last_processed_ts else None,
            'last_processed_pivot_bar': self.last_processed_pivot_bar,
            'alert_sent': self.alert_sent
        }

    @classmethod
    def from_dict(cls, data):
        state = cls()
        if data:
            state.ph_price_2 = data.get('ph_price_2')
            state.ph_price_1 = data.get('ph_price_1')
            state.ph_ts_2 = pd.Timestamp(data['ph_ts_2']) if data.get('ph_ts_2') else None
            state.ph_ts_1 = pd.Timestamp(data['ph_ts_1']) if data.get('ph_ts_1') else None
            state.ph_bar_2 = data.get('ph_bar_2')
            state.ph_bar_1 = data.get('ph_bar_1')
            state.ph_rsi_2 = data.get('ph_rsi_2')
            state.ph_rsi_1 = data.get('ph_rsi_1')
            state.ph_macdline_2 = data.get('ph_macdline_2')
            state.ph_macdline_1 = data.get('ph_macdline_1')
            state.ph_hist_2 = data.get('ph_hist_2')
            state.ph_hist_1 = data.get('ph_hist_1')
            state.pl_price_2 = data.get('pl_price_2')
            state.pl_price_1 = data.get('pl_price_1')
            state.pl_ts_2 = pd.Timestamp(data['pl_ts_2']) if data.get('pl_ts_2') else None
            state.pl_ts_1 = pd.Timestamp(data['pl_ts_1']) if data.get('pl_ts_1') else None
            state.pl_bar_2 = data.get('pl_bar_2')
            state.pl_bar_1 = data.get('pl_bar_1')
            state.pl_rsi_2 = data.get('pl_rsi_2')
            state.pl_rsi_1 = data.get('pl_rsi_1')
            state.pl_macdline_2 = data.get('pl_macdline_2')
            state.pl_macdline_1 = data.get('pl_macdline_1')
            state.pl_hist_2 = data.get('pl_hist_2')
            state.pl_hist_1 = data.get('pl_hist_1')
            
            # بازسازی pivot_highs و pivot_lows
            state.pivot_highs = []
            for p in data.get('pivot_highs', []):
                state.pivot_highs.append({
                    'price': p['price'],
                    'ts': pd.Timestamp(p['ts']) if p.get('ts') else None
                })
            
            state.pivot_lows = []
            for p in data.get('pivot_lows', []):
                state.pivot_lows.append({
                    'price': p['price'],
                    'ts': pd.Timestamp(p['ts']) if p.get('ts') else None
                })
            
            state.stored_long_stop = data.get('stored_long_stop')
            state.stored_short_stop = data.get('stored_short_stop')
            state.stored_long_tp = data.get('stored_long_tp')
            state.stored_short_tp = data.get('stored_short_tp')
            state.last_processed_ts = pd.Timestamp(data['last_processed_ts']) if data.get('last_processed_ts') else None
            state.last_processed_pivot_bar = data.get('last_processed_pivot_bar')
            state.alert_sent = data.get('alert_sent', False)
        return state

SYMBOLS = ["LTCUSDT", "DOGEUSDT", "ETHUSDT"]
SYMBOL_STATES = {s: SymbolState() for s in SYMBOLS}
SIGNAL_COUNTER = 0

def save_states():
    data = {s: SYMBOL_STATES[s].to_dict() for s in SYMBOLS}
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"[STATE] Error saving states: {e}")

def load_states():
    global SYMBOL_STATES
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                data = json.load(f)
            for s in SYMBOLS:
                if s in data:
                    SYMBOL_STATES[s] = SymbolState.from_dict(data[s])
            logger.info(f"[STATE] Loaded states from {STATE_FILE}")
        except Exception as e:
            logger.error(f"[STATE] Error loading states: {e}")
    else:
        logger.info(f"[STATE] No state file found, starting fresh")

# =====================================================================================
# ======================== توابع تبدیل =============================================
# =====================================================================================

def resolve_bar_from_ts(df_indexed, ts):
    """دریافت ایندکس کندل بر اساس timestamp"""
    if ts is None:
        return None
    try:
        if ts.tzinfo is None:
            ts = ts.tz_localize('UTC')
        if df_indexed.index.tzinfo is None:
            df_index = df_indexed.index.tz_localize('UTC')
        else:
            df_index = df_indexed.index
        return df_index.get_loc(ts)
    except KeyError:
        return None

def resolve_ts_from_bar(df_indexed, bar):
    """دریافت timestamp از ایندکس کندل"""
    if bar is None or bar < 0 or bar >= len(df_indexed):
        return None
    return df_indexed.index[bar]

# =====================================================================================
# ======================== محاسبه استاپ و تارگت =====================================
# =====================================================================================

def compute_stop_and_targets(pivot_highs, pivot_lows, direction, df_indexed, atr_val, stop_buffer_pct=STOP_BUFFER_PCT, min_rr=2.0):
    """
    محاسبه Stop Loss و Take Profit بر اساس منطق صحیح واگرایی
    
    LONG:
        - Stop = min(دو دره) - buffer * ATR
        - Target = قله بین دو دره (mid_peak)
        - اگر RRR < 2 → target رو بالا ببر تا RRR = 2
    
    SHORT:
        - Stop = max(دو قله) + buffer * ATR
        - Target = دره بین دو قله (mid_trough)
        - اگر RRR < 2 → target رو پایین ببر تا RRR = 2
    """
    entry_price = float(df_indexed['close'].iloc[-1])
    
    if direction == "long":
        if len(pivot_lows) < 2:
            logger.warning("[STOP] Not enough pivot lows for LONG")
            return None, None, None
        
        pl_1, pl_2 = pivot_lows[-2], pivot_lows[-1]
        bar1 = resolve_bar_from_ts(df_indexed, pl_1['ts'])
        bar2 = resolve_bar_from_ts(df_indexed, pl_2['ts'])
        
        if bar1 is None or bar2 is None or bar2 <= bar1:
            logger.warning("[STOP] Invalid bar indices for LONG")
            return None, None, None
        
        # Stop Loss: پایین‌ترین دره - buffer * ATR
        stop_price = min(pl_1['price'], pl_2['price']) - stop_buffer_pct * atr_val
        
        # Take Profit اولیه: قله بین دو دره
        try:
            mid_peak = df_indexed["high"].iloc[bar1+1:bar2].max()
            if pd.isna(mid_peak):
                logger.warning("[STOP] No mid peak found")
                return None, None, None
        except:
            return None, None, None
        
        target_price = float(mid_peak)
        
        # محاسبه RRR و اصلاح target در صورت نیاز
        risk = abs(entry_price - stop_price)
        reward = abs(target_price - entry_price)
        rr = reward / risk if risk > 0 else 0
        
        if rr < min_rr:
            target_price = entry_price + risk * min_rr
            logger.info(f"[STOP] LONG RRR={rr:.2f} < {min_rr}, target adjusted to {target_price:.4f}")
        
        logger.info(f"[STOP] LONG: entry={entry_price:.4f}, stop={stop_price:.4f}, target={target_price:.4f}, RRR={max(rr, min_rr):.2f}")
        return stop_price, target_price, mid_peak
        
    elif direction == "short":
        if len(pivot_highs) < 2:
            logger.warning("[STOP] Not enough pivot highs for SHORT")
            return None, None, None
        
        ph_1, ph_2 = pivot_highs[-2], pivot_highs[-1]
        bar1 = resolve_bar_from_ts(df_indexed, ph_1['ts'])
        bar2 = resolve_bar_from_ts(df_indexed, ph_2['ts'])
        
        if bar1 is None or bar2 is None or bar2 <= bar1:
            logger.warning("[STOP] Invalid bar indices for SHORT")
            return None, None, None
        
        # Stop Loss: بالاترین قله + buffer * ATR
        stop_price = max(ph_1['price'], ph_2['price']) + stop_buffer_pct * atr_val
        
        # Take Profit اولیه: دره بین دو قله
        try:
            mid_trough = df_indexed["low"].iloc[bar1+1:bar2].min()
            if pd.isna(mid_trough):
                logger.warning("[STOP] No mid trough found")
                return None, None, None
        except:
            return None, None, None
        
        target_price = float(mid_trough)
        
        # محاسبه RRR و اصلاح target در صورت نیاز
        risk = abs(stop_price - entry_price)
        reward = abs(entry_price - target_price)
        rr = reward / risk if risk > 0 else 0
        
        if rr < min_rr:
            target_price = entry_price - risk * min_rr
            logger.info(f"[STOP] SHORT RRR={rr:.2f} < {min_rr}, target adjusted to {target_price:.4f}")
        
        logger.info(f"[STOP] SHORT: entry={entry_price:.4f}, stop={stop_price:.4f}, target={target_price:.4f}, RRR={max(rr, min_rr):.2f}")
        return stop_price, target_price, mid_trough
    
    return None, None, None

# =====================================================================================
# تابع تشخیص سیگنال (مطابق PyneCore - بدون MTF)
# =====================================================================================

def detect_signal(df, state, symbol):
    debug_log = []
    debug_file_lines = []
    
    def log(msg):
        debug_log.append(msg)
        debug_file_lines.append(msg)
        logger.info(msg)
    
    log(f"🔍 DTM — {symbol} | {format_iran_time()}")
    
    if API_RETURNS_OPEN_CANDLE:
        closed_df = df.iloc[:-1].copy()
    else:
        closed_df = df.copy()
    
    if len(closed_df) > 0:
        last_bar_start = closed_df.index[-1]
        if last_bar_start.tzinfo is None:
            last_bar_start = last_bar_start.tz_localize('UTC')
        last_bar_end = last_bar_start + pd.Timedelta(minutes=1)
        now_utc = pd.Timestamp.now(tz='UTC')
        if now_utc < last_bar_end:
            closed_df = closed_df.iloc[:-1].copy()
    
    if len(closed_df) > HISTORY_BARS:
        closed_df = closed_df.tail(HISTORY_BARS)
    
    n = len(closed_df)
    if n < 33:
        log(f"❌ داده ناکافی: {n}")
        return None, None, None, None, False, None, None, 0, [], None, None
    
    close_series = closed_df["close"]
    high_series = closed_df["high"]
    low_series = closed_df["low"]
    open_series = closed_df["open"]
    
    # محاسبه اندیکاتورها
    rsi_val = calc_rsi(close_series, RSI_LEN)
    macd_line, signal_line, hist_line = calc_macd(close_series, MACD_FAST, MACD_SLOW, MACD_SIG)
    atr14 = calc_atr(high_series, low_series, close_series, 14)
    
    # محاسبه pivotها
    pivot_high = find_pivot_high(high_series, LEFT_BARS, RIGHT_BARS)
    pivot_low = find_pivot_low(low_series, LEFT_BARS, RIGHT_BARS)
    
    # آخرین کندل تأیید شده
    last_confirmed_pos = n - 1 - RIGHT_BARS
    last_confirmed_ts = closed_df.index[last_confirmed_pos]
    
    # بازیابی state
    ph_price_2 = state.ph_price_2
    ph_price_1 = state.ph_price_1
    ph_ts_2 = state.ph_ts_2
    ph_ts_1 = state.ph_ts_1
    ph_bar_2 = state.ph_bar_2
    ph_bar_1 = state.ph_bar_1
    ph_rsi_2 = state.ph_rsi_2
    ph_rsi_1 = state.ph_rsi_1
    ph_macdline_2 = state.ph_macdline_2
    ph_macdline_1 = state.ph_macdline_1
    ph_hist_2 = state.ph_hist_2
    ph_hist_1 = state.ph_hist_1
    
    pl_price_2 = state.pl_price_2
    pl_price_1 = state.pl_price_1
    pl_ts_2 = state.pl_ts_2
    pl_ts_1 = state.pl_ts_1
    pl_bar_2 = state.pl_bar_2
    pl_bar_1 = state.pl_bar_1
    pl_rsi_2 = state.pl_rsi_2
    pl_rsi_1 = state.pl_rsi_1
    pl_macdline_2 = state.pl_macdline_2
    pl_macdline_1 = state.pl_macdline_1
    pl_hist_2 = state.pl_hist_2
    pl_hist_1 = state.pl_hist_1
    
    new_pivot_high = False
    new_pivot_low = False
    pivot_processed_high = False
    pivot_processed_low = False
    
    # تشخیص پیوت جدید مطابق PyneCore
    # اصلاح‌شده: بررسی تکراری نبودن با last_processed_pivot_bar
    if not pd.isna(pivot_high.iloc[last_confirmed_pos]):
        real_pivot_pos = last_confirmed_pos - RIGHT_BARS
        
        # بررسی تکراری نبودن
        if state.last_processed_pivot_bar == real_pivot_pos:
            log(f"   ⏭️ Pivot High {real_pivot_pos} قبلاً پردازش شده")
        else:
            ph_price_1 = ph_price_2
            ph_ts_1 = ph_ts_2
            ph_bar_1 = ph_bar_2
            ph_rsi_1 = ph_rsi_2
            ph_macdline_1 = ph_macdline_2
            ph_hist_1 = ph_hist_2
            
            real_pivot_ts = closed_df.index[real_pivot_pos]
            
            ph_price_2 = float(pivot_high.iloc[last_confirmed_pos])
            ph_ts_2 = real_pivot_ts
            ph_bar_2 = real_pivot_pos
            ph_rsi_2 = float(rsi_val.iloc[real_pivot_pos])
            ph_macdline_2 = float(macd_line.iloc[real_pivot_pos])
            ph_hist_2 = float(hist_line.iloc[real_pivot_pos])
            new_pivot_high = True
            pivot_processed_high = True
            
            # ذخیره در state
            state.ph_price_2 = ph_price_2
            state.ph_price_1 = ph_price_1
            state.ph_ts_2 = ph_ts_2
            state.ph_ts_1 = ph_ts_1
            state.ph_bar_2 = ph_bar_2
            state.ph_bar_1 = ph_bar_1
            state.ph_rsi_2 = ph_rsi_2
            state.ph_rsi_1 = ph_rsi_1
            state.ph_macdline_2 = ph_macdline_2
            state.ph_macdline_1 = ph_macdline_1
            state.ph_hist_2 = ph_hist_2
            state.ph_hist_1 = ph_hist_1
            
            # افزودن به لیست pivot_highs
            state.pivot_highs.append({
                'price': ph_price_2,
                'ts': ph_ts_2
            })
            # محدود کردن طول لیست
            if len(state.pivot_highs) > 10:
                state.pivot_highs = state.pivot_highs[-10:]
            
            logger.info(f"[PIVOT] {symbol} New Pivot High: price={ph_price_2:.4f}, ts={ph_ts_2}")
    
    if not pd.isna(pivot_low.iloc[last_confirmed_pos]):
        real_pivot_pos = last_confirmed_pos - RIGHT_BARS
        
        # بررسی تکراری نبودن
        if state.last_processed_pivot_bar == real_pivot_pos:
            log(f"   ⏭️ Pivot Low {real_pivot_pos} قبلاً پردازش شده")
        else:
            pl_price_1 = pl_price_2
            pl_ts_1 = pl_ts_2
            pl_bar_1 = pl_bar_2
            pl_rsi_1 = pl_rsi_2
            pl_macdline_1 = pl_macdline_2
            pl_hist_1 = pl_hist_2
            
            real_pivot_ts = closed_df.index[real_pivot_pos]
            
            pl_price_2 = float(pivot_low.iloc[last_confirmed_pos])
            pl_ts_2 = real_pivot_ts
            pl_bar_2 = real_pivot_pos
            pl_rsi_2 = float(rsi_val.iloc[real_pivot_pos])
            pl_macdline_2 = float(macd_line.iloc[real_pivot_pos])
            pl_hist_2 = float(hist_line.iloc[real_pivot_pos])
            new_pivot_low = True
            pivot_processed_low = True
            
            # ذخیره در state
            state.pl_price_2 = pl_price_2
            state.pl_price_1 = pl_price_1
            state.pl_ts_2 = pl_ts_2
            state.pl_ts_1 = pl_ts_1
            state.pl_bar_2 = pl_bar_2
            state.pl_bar_1 = pl_bar_1
            state.pl_rsi_2 = pl_rsi_2
            state.pl_rsi_1 = pl_rsi_1
            state.pl_macdline_2 = pl_macdline_2
            state.pl_macdline_1 = pl_macdline_1
            state.pl_hist_2 = pl_hist_2
            state.pl_hist_1 = pl_hist_1
            
            # افزودن به لیست pivot_lows
            state.pivot_lows.append({
                'price': pl_price_2,
                'ts': pl_ts_2
            })
            # محدود کردن طول لیست
            if len(state.pivot_lows) > 10:
                state.pivot_lows = state.pivot_lows[-10:]
            
            logger.info(f"[PIVOT] {symbol} New Pivot Low: price={pl_price_2:.4f}, ts={pl_ts_2}")
    
    # به‌روزرسانی last_processed_pivot_bar اگر Pivot جدید پردازش شده
    if pivot_processed_high or pivot_processed_low:
        state.last_processed_pivot_bar = last_confirmed_pos - RIGHT_BARS
    
    state.last_processed_ts = last_confirmed_ts
    early_signal = new_pivot_high or new_pivot_low
    
    log(f"   n={n}, last_confirmed={last_confirmed_ts}")
    log(f"   new_high={1 if new_pivot_high else 0}, new_low={1 if new_pivot_low else 0}")
    
    # فیلتر مستقل Histogram
    histogram_phase_changed_for_highs = False
    histogram_phase_changed_for_lows = False
    
    if new_pivot_high and ph_ts_1 is not None:
        histogram_phase_changed_for_highs = histogram_changed_phase(
            hist_line, ph_ts_1, ph_ts_2
        )
    
    if new_pivot_low and pl_ts_1 is not None:
        histogram_phase_changed_for_lows = histogram_changed_phase(
            hist_line, pl_ts_1, pl_ts_2
        )
    
    best_signal = None
    best_entry = None
    best_stop = None
    best_target = None
    best_emoji = None
    best_label = None
    best_score = 0
    best_details = []
    best_pivot1 = None
    best_pivot2 = None
    
    # ============================================================
    # 1. Classic Bearish
    # ============================================================
    if new_pivot_high and ph_ts_1 is not None:
        price_higher_high = ph_price_2 > ph_price_1
        rsi_lower_high = ph_rsi_2 < ph_rsi_1
        macd_lower_high = ph_macdline_2 < ph_macdline_1
        hist_lower_high = ph_hist_2 < ph_hist_1
        both_peaks_green = ph_hist_1 > 0 and ph_hist_2 > 0
        
        trend_ok = is_trending_up(close_series, ph_ts_1)
        
        fib_ok = False
        trend_start = find_trend_start_low(low_series, ph_ts_1)
        fib_ok = check_fib_level(trend_start, ph_price_1, ph_price_2, True)
        
        # تعریف واگرایی پایه بدون فیلتر تغییر رنگ
        classic_bearish_cond1_rsi = price_higher_high and rsi_lower_high
        classic_bearish_cond2_macdl = price_higher_high and macd_lower_high
        classic_bearish_cond3_macdh = price_higher_high and hist_lower_high and both_peaks_green
        classic_bearish_base3 = price_higher_high and trend_ok and classic_bearish_cond3_macdh and classic_bearish_cond1_rsi and classic_bearish_cond2_macdl
        
        # فیلتر مستقل MACD Color
        macd_color_filter_bearish = (
            (not ENABLE_MACD_COLOR_FILTER)
            or (both_peaks_green and histogram_phase_changed_for_highs)
        )
        
        # استفاده از Candle Confirmation روی کندل Pivot
        pa_bullish, pa_bearish = candle_confirmation(
            open_series, close_series, high_series, low_series, atr14,
            BIG_CANDLE_AVG_LEN, SHADOW_TO_BODY_RATIO, MAX_OPPOSITE_SHADOW_PCT,
            MIN_CANDLE_ATR_RATIO, BIG_CANDLE_MULTIPLIER, ph_ts_2
        )
        pa_ok = pa_bearish
        
        # محاسبه امتیاز بدون فیلتر تغییر رنگ
        score = (
            (1 if classic_bearish_cond1_rsi else 0)
            + (1 if classic_bearish_cond2_macdl else 0)
            + (1 if classic_bearish_cond3_macdh else 0)
            + (1 if fib_ok else 0)
            + (1 if pa_ok else 0)
        )
        
        log(f"   🔴 CD- check | PH1={ph_price_1:.4f} (RSI={ph_rsi_1:.2f}) → PH2={ph_price_2:.4f} (RSI={ph_rsi_2:.2f})")
        log(f"      ColorFilter: {'✅' if macd_color_filter_bearish else '❌'} (phase_changed={histogram_phase_changed_for_highs})")
        
        # استفاده از تابع نهایی با color_filter
        if passes_min_requirement(classic_bearish_base3, fib_ok, pa_ok, macd_color_filter_bearish):
            entry_price = float(close_series.iloc[-1])
            
            # محاسبه استاپ و تارگت با منطق صحیح
            stop_price, target_price, mid_peak = compute_stop_and_targets(
                state.pivot_highs, state.pivot_lows, "short", 
                closed_df, atr14.iloc[-1]
            )
            
            if stop_price is not None and target_price is not None:
                # ذخیره Stop و Target در state
                state.stored_short_stop = stop_price
                state.stored_short_tp = target_price
                
                details = [
                    f"✅ priceHigherHigh and rsiLowerHighOnPeaks",
                    f"✅ priceHigherHigh and macdLineLowerHighOnPeaks",
                    f"✅ priceHigherHigh and histLowerHighOnPeaks and bothPeaksGreen",
                    f"✅ trendOkForBearish",
                    f"✅ fibScoreBearish" if fib_ok else "❌ fibScoreBearish",
                    f"✅ priceActionBearishAtPivot" if pa_ok else "❌ priceActionBearishAtPivot",
                    f"✅ macdColorFilter" if macd_color_filter_bearish else "❌ macdColorFilter"
                ]
                
                if score > best_score:
                    best_signal = "SELL"
                    best_entry = entry_price
                    best_stop = stop_price
                    best_target = target_price
                    best_emoji = "🔴"
                    best_label = "Classic Bearish"
                    best_score = score
                    best_details = details
                    best_pivot1 = {'price': ph_price_1, 'rsi': ph_rsi_1, 'macdline': ph_macdline_1, 'hist': ph_hist_1, 'ts': ph_ts_1}
                    best_pivot2 = {'price': ph_price_2, 'rsi': ph_rsi_2, 'macdline': ph_macdline_2, 'hist': ph_hist_2, 'ts': ph_ts_2}
        else:
            log(f"   🔴 CD- score={score}/5")
            log(f"      {'✅' if rsi_lower_high else '❌'} RSI")
            log(f"      {'✅' if macd_lower_high else '❌'} MACD Line")
            log(f"      {'✅' if hist_lower_high and both_peaks_green else '❌'} MACD Histogram")
            log(f"      {'✅' if trend_ok else '❌'} Trend")
            log(f"      {'✅' if macd_color_filter_bearish else '❌'} Color Filter")
            log(f"      ❌ Base3 برقرار نیست")
    
    # ============================================================
    # 2. Classic Bullish
    # ============================================================
    if new_pivot_low and pl_ts_1 is not None:
        price_lower_low = pl_price_2 < pl_price_1
        rsi_higher_low = pl_rsi_2 > pl_rsi_1
        macd_higher_low = pl_macdline_2 > pl_macdline_1
        hist_higher_low = pl_hist_2 > pl_hist_1
        both_troughs_red = pl_hist_1 < 0 and pl_hist_2 < 0
        
        trend_ok = is_trending_down(close_series, pl_ts_1)
        
        fib_ok = False
        trend_start = find_trend_start_high(high_series, pl_ts_1)
        fib_ok = check_fib_level(trend_start, pl_price_1, pl_price_2, False)
        
        # تعریف واگرایی پایه بدون فیلتر تغییر رنگ
        classic_bullish_cond1_rsi = price_lower_low and rsi_higher_low
        classic_bullish_cond2_macdl = price_lower_low and macd_higher_low
        classic_bullish_cond3_macdh = price_lower_low and hist_higher_low and both_troughs_red
        classic_bullish_base3 = price_lower_low and trend_ok and classic_bullish_cond3_macdh and classic_bullish_cond1_rsi and classic_bullish_cond2_macdl
        
        # فیلتر مستقل MACD Color
        macd_color_filter_bullish = (
            (not ENABLE_MACD_COLOR_FILTER)
            or (both_troughs_red and histogram_phase_changed_for_lows)
        )
        
        # استفاده از Candle Confirmation روی کندل Pivot
        pa_bullish, pa_bearish = candle_confirmation(
            open_series, close_series, high_series, low_series, atr14,
            BIG_CANDLE_AVG_LEN, SHADOW_TO_BODY_RATIO, MAX_OPPOSITE_SHADOW_PCT,
            MIN_CANDLE_ATR_RATIO, BIG_CANDLE_MULTIPLIER, pl_ts_2
        )
        pa_ok = pa_bullish
        
        # محاسبه امتیاز بدون فیلتر تغییر رنگ
        score = (
            (1 if classic_bullish_cond1_rsi else 0)
            + (1 if classic_bullish_cond2_macdl else 0)
            + (1 if classic_bullish_cond3_macdh else 0)
            + (1 if fib_ok else 0)
            + (1 if pa_ok else 0)
        )
        
        log(f"   🟢 CD+ check | PL1={pl_price_1:.4f} (RSI={pl_rsi_1:.2f}) → PL2={pl_price_2:.4f} (RSI={pl_rsi_2:.2f})")
        log(f"      ColorFilter: {'✅' if macd_color_filter_bullish else '❌'} (phase_changed={histogram_phase_changed_for_lows})")
        
        # استفاده از تابع نهایی با color_filter
        if passes_min_requirement(classic_bullish_base3, fib_ok, pa_ok, macd_color_filter_bullish):
            entry_price = float(close_series.iloc[-1])
            
            stop_price, target_price, mid_trough = compute_stop_and_targets(
                state.pivot_highs, state.pivot_lows, "long", 
                closed_df, atr14.iloc[-1]
            )
            
            if stop_price is not None and target_price is not None:
                # ذخیره Stop و Target در state
                state.stored_long_stop = stop_price
                state.stored_long_tp = target_price
                
                details = [
                    f"✅ priceLowerLow and rsiHigherLowOnTroughs",
                    f"✅ priceLowerLow and macdLineHigherLowOnTroughs",
                    f"✅ priceLowerLow and histHigherLowOnTroughs and bothTroughsRed",
                    f"✅ trendOkForBullish",
                    f"✅ fibScoreBullish" if fib_ok else "❌ fibScoreBullish",
                    f"✅ priceActionBullishAtPivot" if pa_ok else "❌ priceActionBullishAtPivot",
                    f"✅ macdColorFilter" if macd_color_filter_bullish else "❌ macdColorFilter"
                ]
                
                if score > best_score:
                    best_signal = "BUY"
                    best_entry = entry_price
                    best_stop = stop_price
                    best_target = target_price
                    best_emoji = "🟢"
                    best_label = "Classic Bullish"
                    best_score = score
                    best_details = details
                    best_pivot1 = {'price': pl_price_1, 'rsi': pl_rsi_1, 'macdline': pl_macdline_1, 'hist': pl_hist_1, 'ts': pl_ts_1}
                    best_pivot2 = {'price': pl_price_2, 'rsi': pl_rsi_2, 'macdline': pl_macdline_2, 'hist': pl_hist_2, 'ts': pl_ts_2}
        else:
            log(f"   🟢 CD+ score={score}/5")
            log(f"      {'✅' if rsi_higher_low else '❌'} RSI")
            log(f"      {'✅' if macd_higher_low else '❌'} MACD Line")
            log(f"      {'✅' if hist_higher_low and both_troughs_red else '❌'} MACD Histogram")
            log(f"      {'✅' if trend_ok else '❌'} Trend")
            log(f"      {'✅' if macd_color_filter_bullish else '❌'} Color Filter")
            log(f"      ❌ Base3 برقرار نیست")
    
    # ============================================================
    # 3. Hidden Bullish
    # ============================================================
    if new_pivot_low and pl_ts_1 is not None and ENABLE_HIDDEN:
        price_higher_low = pl_price_2 > pl_price_1
        rsi_lower_low = pl_rsi_2 < pl_rsi_1
        macd_lower_low = pl_macdline_2 < pl_macdline_1
        hist_lower_low = pl_hist_2 < pl_hist_1
        both_troughs_red = pl_hist_1 < 0 and pl_hist_2 < 0
        
        trend_ok = is_trending_up(close_series, pl_ts_1)
        
        fib_ok = False
        trend_start = find_trend_start_high(high_series, pl_ts_1)
        fib_ok = check_fib_level(trend_start, pl_price_1, pl_price_2, False)
        
        # تعریف واگرایی پایه بدون فیلتر تغییر رنگ
        hidden_bullish_cond1_rsi = price_higher_low and rsi_lower_low
        hidden_bullish_cond2_macdl = price_higher_low and macd_lower_low
        hidden_bullish_cond3_macdh = price_higher_low and hist_lower_low and both_troughs_red
        hidden_bullish_base3 = price_higher_low and hidden_bullish_cond3_macdh and hidden_bullish_cond1_rsi and hidden_bullish_cond2_macdl
        
        # فیلتر مستقل MACD Color
        macd_color_filter_bullish = (
            (not ENABLE_MACD_COLOR_FILTER)
            or (both_troughs_red and histogram_phase_changed_for_lows)
        )
        
        # استفاده از Candle Confirmation روی کندل Pivot
        pa_bullish, pa_bearish = candle_confirmation(
            open_series, close_series, high_series, low_series, atr14,
            BIG_CANDLE_AVG_LEN, SHADOW_TO_BODY_RATIO, MAX_OPPOSITE_SHADOW_PCT,
            MIN_CANDLE_ATR_RATIO, BIG_CANDLE_MULTIPLIER, pl_ts_2
        )
        pa_ok = pa_bullish
        
        # محاسبه امتیاز بدون فیلتر تغییر رنگ
        score = (
            (1 if hidden_bullish_cond1_rsi else 0)
            + (1 if hidden_bullish_cond2_macdl else 0)
            + (1 if hidden_bullish_cond3_macdh else 0)
            + (1 if fib_ok else 0)
            + (1 if pa_ok else 0)
        )
        
        log(f"   🔵 HD+ check | PL1={pl_price_1:.4f} (RSI={pl_rsi_1:.2f}) → PL2={pl_price_2:.4f} (RSI={pl_rsi_2:.2f})")
        log(f"      ColorFilter: {'✅' if macd_color_filter_bullish else '❌'} (phase_changed={histogram_phase_changed_for_lows})")
        
        # استفاده از تابع نهایی با color_filter
        if passes_min_requirement(hidden_bullish_base3, fib_ok, pa_ok, macd_color_filter_bullish):
            entry_price = float(close_series.iloc[-1])
            
            stop_price, target_price, mid_trough = compute_stop_and_targets(
                state.pivot_highs, state.pivot_lows, "long", 
                closed_df, atr14.iloc[-1]
            )
            
            if stop_price is not None and target_price is not None:
                # ذخیره Stop و Target در state
                state.stored_long_stop = stop_price
                state.stored_long_tp = target_price
                
                details = [
                    f"✅ priceHigherLow and rsiLowerLowOnTroughs",
                    f"✅ priceHigherLow and macdLineLowerLowOnTroughs",
                    f"✅ priceHigherLow and histLowerLowOnTroughs and bothTroughsRed",
                    f"✅ trendOkForBullish" if trend_ok else "❌ trendOkForBullish",
                    f"✅ fibScoreBullish" if fib_ok else "❌ fibScoreBullish",
                    f"✅ priceActionBullishAtPivot" if pa_ok else "❌ priceActionBullishAtPivot",
                    f"✅ macdColorFilter" if macd_color_filter_bullish else "❌ macdColorFilter"
                ]
                
                if score > best_score:
                    best_signal = "BUY"
                    best_entry = entry_price
                    best_stop = stop_price
                    best_target = target_price
                    best_emoji = "🔵"
                    best_label = "Hidden Bullish"
                    best_score = score
                    best_details = details
                    best_pivot1 = {'price': pl_price_1, 'rsi': pl_rsi_1, 'macdline': pl_macdline_1, 'hist': pl_hist_1, 'ts': pl_ts_1}
                    best_pivot2 = {'price': pl_price_2, 'rsi': pl_rsi_2, 'macdline': pl_macdline_2, 'hist': pl_hist_2, 'ts': pl_ts_2}
        else:
            log(f"   🔵 HD+ score={score}/5")
            log(f"      {'✅' if rsi_lower_low else '❌'} RSI")
            log(f"      {'✅' if macd_lower_low else '❌'} MACD Line")
            log(f"      {'✅' if hist_lower_low and both_troughs_red else '❌'} MACD Histogram")
            log(f"      {'✅' if macd_color_filter_bullish else '❌'} Color Filter")
            log(f"      ❌ Base3 برقرار نیست")
    
    # ============================================================
    # 4. Hidden Bearish
    # ============================================================
    if new_pivot_high and ph_ts_1 is not None and ENABLE_HIDDEN:
        price_lower_high = ph_price_2 < ph_price_1
        rsi_higher_high = ph_rsi_2 > ph_rsi_1
        macd_higher_high = ph_macdline_2 > ph_macdline_1
        hist_higher_high = ph_hist_2 > ph_hist_1
        both_peaks_green = ph_hist_1 > 0 and ph_hist_2 > 0
        
        trend_ok = is_trending_down(close_series, ph_ts_1)
        
        fib_ok = False
        trend_start = find_trend_start_low(low_series, ph_ts_1)
        fib_ok = check_fib_level(trend_start, ph_price_1, ph_price_2, True)
        
        # تعریف واگرایی پایه بدون فیلتر تغییر رنگ
        hidden_bearish_cond1_rsi = price_lower_high and rsi_higher_high
        hidden_bearish_cond2_macdl = price_lower_high and macd_higher_high
        hidden_bearish_cond3_macdh = price_lower_high and hist_higher_high and both_peaks_green
        hidden_bearish_base3 = price_lower_high and hidden_bearish_cond3_macdh and hidden_bearish_cond1_rsi and hidden_bearish_cond2_macdl
        
        # فیلتر مستقل MACD Color
        macd_color_filter_bearish = (
            (not ENABLE_MACD_COLOR_FILTER)
            or (both_peaks_green and histogram_phase_changed_for_highs)
        )
        
        # استفاده از Candle Confirmation روی کندل Pivot
        pa_bullish, pa_bearish = candle_confirmation(
            open_series, close_series, high_series, low_series, atr14,
            BIG_CANDLE_AVG_LEN, SHADOW_TO_BODY_RATIO, MAX_OPPOSITE_SHADOW_PCT,
            MIN_CANDLE_ATR_RATIO, BIG_CANDLE_MULTIPLIER, ph_ts_2
        )
        pa_ok = pa_bearish
        
        # محاسبه امتیاز بدون فیلتر تغییر رنگ
        score = (
            (1 if hidden_bearish_cond1_rsi else 0)
            + (1 if hidden_bearish_cond2_macdl else 0)
            + (1 if hidden_bearish_cond3_macdh else 0)
            + (1 if fib_ok else 0)
            + (1 if pa_ok else 0)
        )
        
        log(f"   🟠 HD- check | PH1={ph_price_1:.4f} (RSI={ph_rsi_1:.2f}) → PH2={ph_price_2:.4f} (RSI={ph_rsi_2:.2f})")
        log(f"      ColorFilter: {'✅' if macd_color_filter_bearish else '❌'} (phase_changed={histogram_phase_changed_for_highs})")
        
        # استفاده از تابع نهایی با color_filter
        if passes_min_requirement(hidden_bearish_base3, fib_ok, pa_ok, macd_color_filter_bearish):
            entry_price = float(close_series.iloc[-1])
            
            stop_price, target_price, mid_peak = compute_stop_and_targets(
                state.pivot_highs, state.pivot_lows, "short", 
                closed_df, atr14.iloc[-1]
            )
            
            if stop_price is not None and target_price is not None:
                # ذخیره Stop و Target در state
                state.stored_short_stop = stop_price
                state.stored_short_tp = target_price
                
                details = [
                    f"✅ priceLowerHigh and rsiHigherHighOnPeaks",
                    f"✅ priceLowerHigh and macdLineHigherHighOnPeaks",
                    f"✅ priceLowerHigh and histHigherHighOnPeaks and bothPeaksGreen",
                    f"✅ trendOkForBearish" if trend_ok else "❌ trendOkForBearish",
                    f"✅ fibScoreBearish" if fib_ok else "❌ fibScoreBearish",
                    f"✅ priceActionBearishAtPivot" if pa_ok else "❌ priceActionBearishAtPivot",
                    f"✅ macdColorFilter" if macd_color_filter_bearish else "❌ macdColorFilter"
                ]
                
                if score > best_score:
                    best_signal = "SELL"
                    best_entry = entry_price
                    best_stop = stop_price
                    best_target = target_price
                    best_emoji = "🟠"
                    best_label = "Hidden Bearish"
                    best_score = score
                    best_details = details
                    best_pivot1 = {'price': ph_price_1, 'rsi': ph_rsi_1, 'macdline': ph_macdline_1, 'hist': ph_hist_1, 'ts': ph_ts_1}
                    best_pivot2 = {'price': ph_price_2, 'rsi': ph_rsi_2, 'macdline': ph_macdline_2, 'hist': ph_hist_2, 'ts': ph_ts_2}
        else:
            log(f"   🟠 HD- score={score}/5")
            log(f"      {'✅' if rsi_higher_high else '❌'} RSI")
            log(f"      {'✅' if macd_higher_high else '❌'} MACD Line")
            log(f"      {'✅' if hist_higher_high and both_peaks_green else '❌'} MACD Histogram")
            log(f"      {'✅' if macd_color_filter_bearish else '❌'} Color Filter")
            log(f"      ❌ Base3 برقرار نیست")
    
    save_states()
    
    if best_signal is None:
        log(f"   ⚪ No signal (none passed Base3 + ColorFilter)")
    
    save_debug_log_to_file(symbol, debug_file_lines)
    
    if best_signal is not None and best_stop is not None and best_target is not None:
        return (best_signal, best_entry, best_stop, best_target, early_signal, 
                best_emoji, best_label, best_score, best_details, best_pivot1, best_pivot2)
    
    return None, None, None, None, early_signal, None, None, 0, [], None, None

# =====================================================================================
# ======================== توابع گزارش‌گیری =========================================
# =====================================================================================

def generate_daily_report_text(trades):
    today_str = format_iran_date()
    if not trades:
        return None
    total_trades = len(trades)
    total_realized_pnl = sum(float(t.get('realized_pnl', 0)) for t in trades)
    wins = len([t for t in trades if float(t.get('realized_pnl', 0)) > 0])
    losses = len([t for t in trades if float(t.get('realized_pnl', 0)) < 0])
    win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
    message = f"""📊 گزارش روزانه — {today_str} {HASHTAGS['daily']}
━━━━━━━━━━━━━━━━━━━━━━
📈 کل معاملات بسته شده: {total_trades} عدد
✅ سودآور: {wins} ({win_rate:.1f}%)
❌ ضررده: {losses}
💰 سود/زیان خالص: {total_realized_pnl:.2f} USDT
📊 نرخ موفقیت: {win_rate:.1f}%
💪 وضعیت: {'عالی! 🚀' if total_realized_pnl > 0 else 'نیاز به بررسی 📊'}
━━━━━━━━━━━━━━━━━━━━━━
🕒 {format_iran_time()}"""
    return message

def generate_monthly_report_text(trades):
    if not trades:
        return None
    total_trades = len(trades)
    total_realized_pnl = sum(float(t.get('realized_pnl', 0)) for t in trades)
    wins = len([t for t in trades if float(t.get('realized_pnl', 0)) > 0])
    losses = len([t for t in trades if float(t.get('realized_pnl', 0)) < 0])
    win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
    message = f"""📈 گزارش ۳۰ روز گذشته {HASHTAGS['monthly']}
━━━━━━━━━━━━━━━━━━━━━━
📊 کل معاملات: {total_trades} عدد
✅ سودآور: {wins} ({win_rate:.1f}%)
❌ ضررده: {losses}
💰 سود/زیان خالص: {total_realized_pnl:.2f} USDT
📈 نرخ موفقیت: {win_rate:.1f}%
💪 ارزیابی: {'پروژه موفق! 🎉' if total_realized_pnl > 0 else 'نیاز به بهینه‌سازی ⚙️'}
━━━━━━━━━━━━━━━━━━━━━━
🕒 {format_iran_time()}"""
    return message

def send_reports():
    try:
        today_str = format_iran_date()
        history = load_history()
        today_trades = [t for t in history if t.get('signal_time', '').startswith(today_str)]
        if today_trades:
            total = len(today_trades)
            wins = len([t for t in today_trades if t.get('result') == 'TAKE_PROFIT'])
            losses = len([t for t in today_trades if t.get('result') == 'STOP_LOSS'])
            closed = wins + losses
            win_rate = (wins / closed * 100) if closed > 0 else 0
            total_pnl = sum([t.get('realized_pnl', 0) for t in today_trades if t.get('result') is not None])
            
            daily_msg = f"""📊 گزارش روزانه (محلی) — {today_str} {HASHTAGS['daily']}
━━━━━━━━━━━━━━━━━━━━━━
📈 کل معاملات: {total} عدد
✅ موفق: {wins} ({win_rate:.1f}%)
❌ ناموفق: {losses}
💰 سود/زیان خالص: {total_pnl:.2f} USDT
📊 نرخ موفقیت: {win_rate:.1f}%
━━━━━━━━━━━━━━━━━━━━━━
🕒 {format_iran_time()}"""
            send_telegram_message(daily_msg)
            logger.info("[REPORT] Local daily report sent.")
    except Exception as e:
        logger.error(f"[REPORT ERROR] Local daily: {e}")
    
    try:
        history = load_history()
        if history:
            monthly_msg = generate_monthly_report_text(history)
            if monthly_msg:
                send_telegram_message(monthly_msg)
                logger.info("[REPORT] Monthly report sent.")
    except Exception as e:
        logger.error(f"[REPORT ERROR] Monthly: {e}")

# =====================================================================================
# ======================== Startup Diagnostic =========================================
# =====================================================================================

def run_startup_diagnostic():
    logger.info("Running Startup Diagnostic...")
    diagnostic_log = []
    diagnostic_log.append(f"🔍 بررسی سلامت سیستم {HASHTAGS['diagnostic']}")
    diagnostic_log.append("━━━━━━━━━━━━━━━━━━━━━━")
    try:
        requests.get("https://www.google.com", timeout=5)
        diagnostic_log.append("🟢 اتصال اینترنت")
    except:
        diagnostic_log.append("🔴 اتصال اینترنت")
    
    public_data = TrueTradePublicData()
    df = None
    try:
        df = public_data.fetch_ohlcv("LTCUSDT", "1m", HISTORY_BARS)
        if df is not None and not df.empty:
            diagnostic_log.append(f"🟢 دریافت داده: {len(df)} کندل")
        else:
            diagnostic_log.append("🔴 دریافت داده")
    except Exception as e:
        diagnostic_log.append(f"🔴 دریافت داده: {str(e)[:50]}")
    
    try:
        if df is not None and not df.empty:
            rsi = calc_rsi(df['close'], 14)
            diagnostic_log.append(f"🟢 RSI(14): {rsi.iloc[-1]:.2f}")
            diagnostic_log.append("🟢 MACD(12,26,9): فعال")
            atr = calc_atr(df['high'], df['low'], df['close'], 14)
            diagnostic_log.append(f"🟢 ATR(14): {atr.iloc[-1]:.4f}")
            ph = find_pivot_high(df['high'], 5, 3)
            pl = find_pivot_low(df['low'], 5, 3)
            diagnostic_log.append(f"🟢 Pivot High(5,3): {ph.notna().sum()} عدد")
            diagnostic_log.append(f"🟢 Pivot Low(5,3): {pl.notna().sum()} عدد")
            diagnostic_log.append("🟢 تشخیص روند: فعال")
            diagnostic_log.append("🟢 Candle Confirmation: فعال")
    except Exception as e:
        diagnostic_log.append(f"🔴 خطا: {str(e)[:50]}")
    
    diagnostic_log.append(f"🟢 پارامترهای استراتژی (مطابق PyneCore):")
    diagnostic_log.append(f"   • PIVOT_MODE: {PIVOT_MODE}")
    diagnostic_log.append(f"   • RSI_LEN: {RSI_LEN}")
    diagnostic_log.append(f"   • MACD: {MACD_FAST}/{MACD_SLOW}/{MACD_SIG}")
    diagnostic_log.append(f"   • TREND_LOOKBACK: {TREND_LOOKBACK}")
    diagnostic_log.append(f"   • TREND_SLOPE_MIN_PCT: {TREND_SLOPE_MIN_PCT}%")
    diagnostic_log.append(f"   • MIN_CONFIRMATIONS: {MIN_CONFIRMATIONS}")
    diagnostic_log.append(f"   • ENABLE_HIDDEN: {ENABLE_HIDDEN}")
    diagnostic_log.append(f"   • ENABLE_MACD_COLOR_FILTER: {ENABLE_MACD_COLOR_FILTER}")
    
    diagnostic_log.append("🟢 موتور امتیازدهی: Base3 + Trend/Fib/PA + ColorFilter")
    diagnostic_log.append("🟢 اتصال به تلگرام")
    
    exchange = TrueTradePrivateExchange(API_KEY, API_SECRET, BASE_URL)
    conn = exchange.test_connection()
    if conn:
        diagnostic_log.append("🟢 اتصال به صرافی: برقرار")
        balance = exchange.fetch_balance()
        if balance:
            diagnostic_log.append(f"🟢 موجودی: {balance:.2f} USDT")
    else:
        diagnostic_log.append("🔴 اتصال به صرافی: قطع")
    
    diagnostic_log.append("\n━━━━━━━━━━━━━━━━━━━━━━")
    diagnostic_log.append("✅ تمام بخش‌ها فعال هستند" if conn else "⚠️ برخی بخش‌ها غیرفعال هستند")
    diagnostic_log.append(f"🕒 {format_iran_time()}")
    send_telegram_message("\n".join(diagnostic_log))
    logger.info("Startup Diagnostic Complete")

# =====================================================================================
# ======================== تابع اجرای اصلی ===========================================
# =====================================================================================

def analyze_and_execute():
    logger.info("[ANALYZE] شروع...")
    exchange = TrueTradePrivateExchange(API_KEY, API_SECRET, BASE_URL)
    conn = exchange.test_connection()
    balance = exchange.fetch_balance() if conn else 0
    if balance is None:
        balance = 0

    data = TrueTradePublicData()
    side_map = {"BUY": "LONG", "SELL": "SHORT"}
    leverage_map = {"LTCUSDT": 75, "DOGEUSDT": 75, "ETHUSDT": 50}

    for symbol in SYMBOLS:
        try:
            df = data.fetch_ohlcv(symbol, '1m', HISTORY_BARS)
            if df is None or df.empty:
                logger.warning(f"[SKIP] {symbol}")
                continue
            
            logger.info(f"[DATA] {symbol}: {len(df)} کندل")

            result = detect_signal(df, SYMBOL_STATES[symbol], symbol)
            
            if len(result) >= 11:
                signal, entry, stop, target, early, emoji, label, score, details, pivot1, pivot2 = result
            else:
                signal, entry, stop, target, early, emoji, label, score = result[:8]
                details = result[8] if len(result) > 8 else []
                pivot1 = result[9] if len(result) > 9 else None
                pivot2 = result[10] if len(result) > 10 else None

            if early and not SYMBOL_STATES[symbol].alert_sent:
                SYMBOL_STATES[symbol].alert_sent = True
                send_telegram_message(f"⚡ Pivot جدید — {symbol} {HASHTAGS['pivot']}\n💰 {df['close'].iloc[-1]:.4f}\n⏳ ~۲ دقیقه تا تأیید\n🕒 {format_iran_time()}")

            if signal and stop and target:
                # ============================================================
                # ✅ اعتبارسنجی و اصلاح Stop Loss / Take Profit
                # ============================================================
                if signal == "BUY":
                    # LONG: stop < entry < target
                    if stop >= entry:
                        logger.warning(f"[ORDER] {symbol} LONG: stop ({stop}) >= entry ({entry}), adjusting...")
                        stop = entry * 0.98  # 2% زیر entry
                    if target <= entry:
                        logger.warning(f"[ORDER] {symbol} LONG: target ({target}) <= entry ({entry}), adjusting...")
                        target = entry * 1.05  # 5% بالای entry
                        
                elif signal == "SELL":
                    # SHORT: target < entry < stop
                    if stop <= entry:
                        logger.warning(f"[ORDER] {symbol} SHORT: stop ({stop}) <= entry ({entry}), adjusting...")
                        stop = entry * 1.02  # 2% بالای entry
                    if target >= entry:
                        logger.warning(f"[ORDER] {symbol} SHORT: target ({target}) >= entry ({entry}), adjusting...")
                        target = entry * 0.95  # 5% زیر entry

                # گرد کردن قیمت‌ها
                entry = exchange._round_price(entry, symbol)
                stop = exchange._round_price(stop, symbol)
                target = exchange._round_price(target, symbol)

                # محاسبه درصدها
                profit_pct = (target-entry)/entry*100 if signal=="BUY" else (entry-target)/entry*100
                loss_pct = (entry-stop)/entry*100 if signal=="BUY" else (stop-entry)/entry*100
                rr = abs(profit_pct/loss_pct) if loss_pct != 0 else 0
                direction_text = "LONG" if signal == "BUY" else "SHORT"
                direction_emoji = "🟢" if signal == "BUY" else "🔴"

                signal_number = get_next_signal_number()

                TARGET_RISK = 3.5
                leverage = leverage_map.get(symbol, 50)
                stop_pct = abs(entry - stop) / entry
                old_leverage = 1.0 / stop_pct if stop_pct > 0 else 999999

                if old_leverage <= leverage:
                    required_capital = TARGET_RISK
                    used_leverage = old_leverage
                else:
                    required_capital = TARGET_RISK * (old_leverage / leverage)
                    used_leverage = leverage

                capital_reduced = False
                if balance >= required_capital:
                    capital = required_capital
                    actual_risk = TARGET_RISK
                else:
                    capital = balance * 0.98
                    actual_risk = capital * used_leverage * stop_pct
                    capital_reduced = True

                qty = (capital * used_leverage) / entry
                potential_profit = capital * used_leverage * (profit_pct / 100)

                signal_type = "CD+" if signal == "BUY" and "Classic" in label else "HD+" if signal == "BUY" else "CD-" if "Classic" in label else "HD-"
                
                pivot1_info = f"Pivot اول: قیمت {pivot1['price']:.4f} @ {pivot1['ts']} (RSI={pivot1['rsi']:.2f})" if pivot1 else "Pivot اول: نامشخص"
                pivot2_info = f"Pivot دوم: قیمت {pivot2['price']:.4f} @ {pivot2['ts']} (RSI={pivot2['rsi']:.2f})" if pivot2 else "Pivot دوم: نامشخص"
                
                signal_message = (
                    f"{emoji} {signal_type} — {symbol} #Signal_{signal_number}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 Score: {score}/5\n"
                    f"🔸 Direction: {direction_text}\n"
                    f"📍 Entry: {entry:.{PRICE_PRECISION.get(symbol, 2)}f}\n"
                    f"🛑 Stop Loss: {stop:.{PRICE_PRECISION.get(symbol, 2)}f}\n"
                    f"🎯 Take Profit: {target:.{PRICE_PRECISION.get(symbol, 2)}f}\n"
                    f"📈 Profit: +{profit_pct:.2f}% | 📉 Loss: -{loss_pct:.2f}%\n"
                    f"⚖️ R/R: {rr:.2f}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 Pivot‌ها:\n"
                    f"• {pivot1_info}\n"
                    f"• {pivot2_info}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🕒 {format_iran_time()}"
                )
                
                send_telegram_message(signal_message)
                time.sleep(0.5)

                if exchange.connected:
                    try:
                        order_result = exchange.create_order(symbol, "market", side_map[signal], capital, None,
                            {'leverage': int(used_leverage), 'stopLoss': stop, 'takeProfit': target})
                        
                        position_id = order_result.get('id', 'N/A')

                        history = load_history()
                        history.append({
                            'symbol': symbol, 'direction': signal,
                            'entry_price': entry, 'stop_loss': stop, 'take_profit': target,
                            'signal_time': format_iran_time(), 'result': None,
                            'score': score, 'label': label, 'capital': capital,
                            'leverage': int(used_leverage), 'qty': qty,
                            'signal_number': signal_number,
                            'position_id': position_id,
                            'pivot1_ts': str(pivot1['ts']) if pivot1 else None,
                            'pivot1_price': pivot1['price'] if pivot1 else None,
                            'pivot1_rsi': pivot1['rsi'] if pivot1 else None,
                            'pivot2_ts': str(pivot2['ts']) if pivot2 else None,
                            'pivot2_price': pivot2['price'] if pivot2 else None,
                            'pivot2_rsi': pivot2['rsi'] if pivot2 else None
                        })
                        save_history(history)

                        order_message = (
                            f"✅ سفارش ثبت شد — {symbol} #سیگنال_{signal_number}\n"
                            f"🔸 {side_map[signal]} | 💰 {capital:.2f} USDT | 🔧 {int(used_leverage)}x\n"
                        )
                        if capital_reduced:
                            order_message += (
                                f"⚠️ سرمایه کاهش یافت! (لازم: {required_capital:.2f} | موجود: {balance:.2f})\n"
                            )
                        order_message += (
                            f"🛑 {stop:.4f} | 🎯 {target:.4f}\n"
                            f"📉 ریسک: {actual_risk:.2f} USDT | 📈 سود: {potential_profit:.2f} USDT\n"
                            f"🕒 {format_iran_time()}"
                        )
                        send_telegram_message(order_message)
                    except Exception as e:
                        send_telegram_message(f"❌ خطا — {symbol} #سیگنال_{signal_number}\n{str(e)[:200]}\n🕒 {format_iran_time()}")
                SYMBOL_STATES[symbol].alert_sent = False
            else:
                logger.info(f"[ANALYSIS] {symbol}: بدون سیگنال")
        except Exception as e:
            logger.error(f"[ERROR] {symbol}: {e}")
    
    save_states()

# =====================================================================================
# ======================== حلقه اصلی =================================================
# =====================================================================================

def main_loop():
    last_daily_report_date = None
    
    while True:
        try:
            logger.info(f"[LOOP] {format_iran_time()}")
            analyze_and_execute()
            
            today = format_iran_date()
            if last_daily_report_date != today:
                try:
                    send_reports()
                    last_daily_report_date = today
                except Exception as e:
                    logger.error(f"[REPORT ERROR] {e}")
            
            time.sleep(60)
        except Exception as e:
            logger.error(f"[LOOP] {e}")
            time.sleep(60)

app = Flask(__name__)

@app.route("/")
def health():
    return "OK", 200

if __name__ == "__main__":
    logger.info("DTM Bot Starting...")
    load_signal_counter()
    load_states()

    send_telegram_message(
        f"🤖 DTM Divergence Light — آنلاین {HASHTAGS['startup']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🧠 منطق: دقیقاً مطابق PyneCore (بدون MTF)\n"
        f"📊 Pivot: {PIVOT_MODE} ({LEFT_BARS}/{RIGHT_BARS})\n"
        f"⚙️ Base3: RSI + MACD Line + MACD Histogram\n"
        f"⚙️ Trend + Fibonacci + PA (امتیازی)\n"
        f"⚙️ Color Filter: {'فعال' if ENABLE_MACD_COLOR_FILTER else 'غیرفعال'}\n"
        f"⚙️ ۴ نوع واگرایی: Classic/Hidden + BUY/SELL\n"
        f"🔧 ETH=50x | LTC/DOGE=75x\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕒 {format_iran_time()}"
    )
    
    run_startup_diagnostic()
    
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=10000), daemon=True).start()
    logger.info("[STARTUP] Flask روی پورت 10000")
    main_loop()
