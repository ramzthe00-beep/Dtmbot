# -*- coding: utf-8 -*-
"""
DTM Divergence Auto-Trading Bot - TheTrueTrade
===============================================
منطق تشخیص سیگنال: کاملاً عین dtm(1).py (بدون MTF)
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
# ثابت‌های استراتژی (مطابق کد اول - بدون MTF)
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
FIB_USE_618 = True
FIB_USE_786 = True
FIB_TOLERANCE_PCT = 0.5
FIB_TREND_SEARCH_BARS = 100
SHADOW_TO_BODY_RATIO = 2.0
MAX_OPPOSITE_SHADOW_PCT = 20.0
MIN_CANDLE_ATR_RATIO = 0.3
BIG_CANDLE_AVG_LEN = 14
BIG_CANDLE_MULTIPLIER = 1.5

LEFT_BARS = 5
RIGHT_BARS = 3
STOP_BUFFER_PCT = 0.05
HISTORY_BARS = 150
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
# کلاس دریافت داده
# =====================================================================================
class TrueTradePublicData:
    def __init__(self):
        self.base_url = BASE_URL

    def fetch_ohlcv(self, symbol, timeframe='1m', limit=HISTORY_BARS):
        symbol_clean = symbol.upper()
        resolution_map = {
            "1m": "1", "5m": "5", "15m": "15", "30m": "30",
            "1h": "60", "4h": "240", "1d": "D", "1w": "W", "1M": "M"
        }
        resolution = resolution_map.get(timeframe, "1")

        to_timestamp = int(time.time())
        from_timestamp = to_timestamp - (limit * 60)

        uri = f"/futures/udf/history?symbol={symbol_clean}&resolution={resolution}&from={from_timestamp}&to={to_timestamp}&countback={limit}"

        try:
            response = requests.get(f"{self.base_url}{uri}", timeout=15)
            response.raise_for_status()
            data = response.json()

            if not data or data.get('s') != 'ok':
                return None

            df = pd.DataFrame({
                'timestamp': pd.to_datetime(data['t'], unit='s', utc=True),
                'open': pd.to_numeric(data['o']),
                'high': pd.to_numeric(data['h']),
                'low': pd.to_numeric(data['l']),
                'close': pd.to_numeric(data['c']),
                'volume': pd.to_numeric(data['v'])
            })
            df.set_index('timestamp', inplace=True)
            return df
        except Exception as e:
            logger.error(f"[FETCH ERROR] {symbol}: {e}")
            return None

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
        # حذف کاراکترهای خطرناک Markdown
        clean_message = re.sub(r'```[^`]*```', '', message)
        clean_message = re.sub(r'[*_~`]', '', clean_message)
        # محدودیت 4096 کاراکتر تلگرام
        if len(clean_message) > 4000:
            clean_message = clean_message[:4000] + "\n... (ادامه)"
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        response = requests.post(
            url, 
            json={
                "chat_id": TELEGRAM_CHAT_ID, 
                "text": clean_message, 
                "parse_mode": None
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
# ======================== توابع محاسباتی (دستی - جایگزین pynecore) ==================
# =====================================================================================

def calc_rma(series, length):
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
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return calc_rma(tr, length)

def find_pivot_high(high, left_bars=LEFT_BARS, right_bars=RIGHT_BARS):
    n = len(high)
    result = pd.Series(np.nan, index=high.index, dtype=float)
    
    for i in range(left_bars, n - right_bars):
        candidate = high.iloc[i]
        
        left_ok = True
        for j in range(1, left_bars + 1):
            if high.iloc[i - j] >= candidate:
                left_ok = False
                break
        
        if not left_ok:
            continue
            
        right_ok = True
        for j in range(1, right_bars + 1):
            if high.iloc[i + j] >= candidate:
                right_ok = False
                break
        
        if right_ok:
            result.iloc[i + right_bars] = candidate
            
    return result

def find_pivot_low(low, left_bars=LEFT_BARS, right_bars=RIGHT_BARS):
    n = len(low)
    result = pd.Series(np.nan, index=low.index, dtype=float)
    
    for i in range(left_bars, n - right_bars):
        candidate = low.iloc[i]
        
        left_ok = True
        for j in range(1, left_bars + 1):
            if low.iloc[i - j] <= candidate:
                left_ok = False
                break
        
        if not left_ok:
            continue
            
        right_ok = True
        for j in range(1, right_bars + 1):
            if low.iloc[i + j] <= candidate:
                right_ok = False
                break
        
        if right_ok:
            result.iloc[i + right_bars] = candidate
            
    return result

def check_color_change(hist_series, bar_start, bar_end, need_red_phase):
    found = False
    if bar_start is not None and bar_end is not None and bar_end > bar_start:
        for i in range(bar_start + 1, bar_end + 1):
            if i >= len(hist_series):
                break
            h = hist_series.iloc[i]
            if need_red_phase and h < 0:
                found = True
                break
            if not need_red_phase and h > 0:
                found = True
                break
    return found

def is_trending_up(close_series, ref_bar):
    result = False
    if ref_bar is not None:
        offset = ref_bar
        if offset >= 0 and offset + TREND_LOOKBACK < len(close_series):
            y_current = close_series.iloc[offset - TREND_LOOKBACK + 1:offset + 1].values
            y_past = close_series.iloc[offset - 2 * TREND_LOOKBACK + 1:offset - TREND_LOOKBACK + 1].values
            
            if len(y_current) >= 2 and len(y_past) >= 2:
                x = np.arange(len(y_current))
                slope_current, intercept_current = np.polyfit(x, y_current, 1)
                fitted_end_current = intercept_current + slope_current * (len(y_current) - 1)
                
                x = np.arange(len(y_past))
                slope_past, intercept_past = np.polyfit(x, y_past, 1)
                fitted_end_past = intercept_past + slope_past * (len(y_past) - 1)
                
                total_slope = fitted_end_current - fitted_end_past
                avg_price = y_current.mean()
                slope_pct = (total_slope / avg_price) * 100 if avg_price != 0 else 0.0
                result = slope_pct > TREND_SLOPE_MIN_PCT
    return result

def is_trending_down(close_series, ref_bar):
    result = False
    if ref_bar is not None:
        offset = ref_bar
        if offset >= 0 and offset + TREND_LOOKBACK < len(close_series):
            y_current = close_series.iloc[offset - TREND_LOOKBACK + 1:offset + 1].values
            y_past = close_series.iloc[offset - 2 * TREND_LOOKBACK + 1:offset - TREND_LOOKBACK + 1].values
            
            if len(y_current) >= 2 and len(y_past) >= 2:
                x = np.arange(len(y_current))
                slope_current, intercept_current = np.polyfit(x, y_current, 1)
                fitted_end_current = intercept_current + slope_current * (len(y_current) - 1)
                
                x = np.arange(len(y_past))
                slope_past, intercept_past = np.polyfit(x, y_past, 1)
                fitted_end_past = intercept_past + slope_past * (len(y_past) - 1)
                
                total_slope = fitted_end_current - fitted_end_past
                avg_price = y_current.mean()
                slope_pct = (total_slope / avg_price) * 100 if avg_price != 0 else 0.0
                result = slope_pct < -TREND_SLOPE_MIN_PCT
    return result

def find_trend_start_low(low_series, ref_bar):
    result = None
    if ref_bar is not None:
        offset = ref_bar
        if offset >= 0 and offset + FIB_TREND_SEARCH_BARS < len(low_series):
            result = low_series.iloc[offset:offset + FIB_TREND_SEARCH_BARS].min()
            if pd.isna(result):
                result = None
    return result

def find_trend_start_high(high_series, ref_bar):
    result = None
    if ref_bar is not None:
        offset = ref_bar
        if offset >= 0 and offset + FIB_TREND_SEARCH_BARS < len(high_series):
            result = high_series.iloc[offset:offset + FIB_TREND_SEARCH_BARS].max()
            if pd.isna(result):
                result = None
    return result

def check_fib_level(fib_start, fib_end, target_price, is_retrace_down):
    ok = False
    if fib_start is not None and fib_end is not None and fib_end != fib_start:
        range_ = fib_end - fib_start
        tol = abs(range_) * (FIB_TOLERANCE_PCT / 100.0)
        if is_retrace_down:
            level618 = fib_end - range_ * 0.618
            level786 = fib_end - range_ * 0.786
        else:
            level618 = fib_end + abs(range_) * 0.618
            level786 = fib_end + abs(range_) * 0.786
        if FIB_USE_618 and abs(target_price - level618) <= tol:
            ok = True
        if FIB_USE_786 and abs(target_price - level786) <= tol:
            ok = True
    return ok

def passes_min_requirement(base3, fib_ok, pa_ok):
    result = False
    if base3:
        if MIN_CONFIRMATIONS == '۳ تعییدیه (حداقل مجاز)':
            result = True
        elif MIN_CONFIRMATIONS == '۳ تعییدیه + فیبوناچی (۴ امتیاز) [Custom]':
            result = fib_ok
        elif MIN_CONFIRMATIONS == '۳ تعییدیه + پرایس\u200cاکشن (۴ امتیاز) [Custom]':
            result = pa_ok
        elif MIN_CONFIRMATIONS == '۵ امتیاز کامل (ایده\u200cآل)':
            result = fib_ok and pa_ok
    return result

def check_price_action(df, confirm_bar, direction, atr_val):
    if confirm_bar is None or confirm_bar < 0 or confirm_bar >= len(df):
        return False, []
    
    last = df.iloc[confirm_bar]
    candle_range = last['high'] - last['low']
    candle_body = abs(last['close'] - last['open'])
    upper_shadow = last['high'] - max(last['close'], last['open'])
    lower_shadow = min(last['close'], last['open']) - last['low']
    
    size_ok = candle_range >= MIN_CANDLE_ATR_RATIO * atr_val
    
    start_idx = max(0, confirm_bar - BIG_CANDLE_AVG_LEN + 1)
    window = df.iloc[start_idx:confirm_bar + 1]
    avg_body = (window['close'] - window['open']).abs().mean()
    if pd.isna(avg_body) or avg_body == 0:
        avg_body = candle_body if candle_body > 0 else 0.00001
    
    pa = False
    pa_reasons = []
    
    if direction == "BUY":
        bullish_wick = (candle_range > 0 and
                        lower_shadow >= SHADOW_TO_BODY_RATIO * candle_body and
                        (upper_shadow / candle_range) * 100 <= MAX_OPPOSITE_SHADOW_PCT and
                        size_ok)
        big_green = (last['close'] > last['open'] and
                     candle_body >= BIG_CANDLE_MULTIPLIER * avg_body and
                     size_ok)
        if bullish_wick:
            pa = True
            pa_reasons.append("Bullish Wick")
        if big_green:
            pa = True
            pa_reasons.append("Big Green Candle")
    else:
        bearish_wick = (candle_range > 0 and
                        upper_shadow >= SHADOW_TO_BODY_RATIO * candle_body and
                        (lower_shadow / candle_range) * 100 <= MAX_OPPOSITE_SHADOW_PCT and
                        size_ok)
        bearish_hanging = (candle_range > 0 and
                           lower_shadow >= SHADOW_TO_BODY_RATIO * candle_body and
                           (upper_shadow / candle_range) * 100 <= MAX_OPPOSITE_SHADOW_PCT and
                           size_ok)
        big_red = (last['close'] < last['open'] and
                   candle_body >= BIG_CANDLE_MULTIPLIER * avg_body and
                   size_ok)
        if bearish_wick:
            pa = True
            pa_reasons.append("Bearish Wick")
        if bearish_hanging:
            pa = True
            pa_reasons.append("Bearish Hanging Man")
        if big_red:
            pa = True
            pa_reasons.append("Big Red Candle")
    
    return pa, pa_reasons

# =====================================================================================
# کلاس وضعیت
# =====================================================================================
class SymbolState:
    def __init__(self):
        self.ph_price_2 = None
        self.ph_price_1 = None
        self.ph_bar_2 = None
        self.ph_bar_1 = None
        self.ph_rsi_2 = None
        self.ph_rsi_1 = None
        self.ph_macdline_2 = None
        self.ph_macdline_1 = None
        self.ph_hist_2 = None
        self.ph_hist_1 = None
        
        self.pl_price_2 = None
        self.pl_price_1 = None
        self.pl_bar_2 = None
        self.pl_bar_1 = None
        self.pl_rsi_2 = None
        self.pl_rsi_1 = None
        self.pl_macdline_2 = None
        self.pl_macdline_1 = None
        self.pl_hist_2 = None
        self.pl_hist_1 = None
        
        self.last_processed_ts = None
        self.alert_sent = False

    def to_dict(self):
        return {
            'ph_price_2': self.ph_price_2,
            'ph_price_1': self.ph_price_1,
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
            'pl_bar_2': self.pl_bar_2,
            'pl_bar_1': self.pl_bar_1,
            'pl_rsi_2': self.pl_rsi_2,
            'pl_rsi_1': self.pl_rsi_1,
            'pl_macdline_2': self.pl_macdline_2,
            'pl_macdline_1': self.pl_macdline_1,
            'pl_hist_2': self.pl_hist_2,
            'pl_hist_1': self.pl_hist_1,
            'last_processed_ts': str(self.last_processed_ts) if self.last_processed_ts else None,
            'alert_sent': self.alert_sent
        }

    @classmethod
    def from_dict(cls, data):
        state = cls()
        if data:
            state.ph_price_2 = data.get('ph_price_2')
            state.ph_price_1 = data.get('ph_price_1')
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
            state.pl_bar_2 = data.get('pl_bar_2')
            state.pl_bar_1 = data.get('pl_bar_1')
            state.pl_rsi_2 = data.get('pl_rsi_2')
            state.pl_rsi_1 = data.get('pl_rsi_1')
            state.pl_macdline_2 = data.get('pl_macdline_2')
            state.pl_macdline_1 = data.get('pl_macdline_1')
            state.pl_hist_2 = data.get('pl_hist_2')
            state.pl_hist_1 = data.get('pl_hist_1')
            state.last_processed_ts = pd.Timestamp(data['last_processed_ts']) if data.get('last_processed_ts') else None
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
# تابع تشخیص سیگنال (بدون MTF)
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
        closed_df = closed_df.tail(HISTORY_BARS).copy()
    
    closed_df_reset = closed_df.reset_index(drop=True)
    n = len(closed_df_reset)
    if n < 33:
        log(f"❌ داده ناکافی: {n}")
        return None, None, None, None, False, None, None, 0, [], None, None
    
    close_series = closed_df_reset["close"]
    high_series = closed_df_reset["high"]
    low_series = closed_df_reset["low"]
    
    rsi_val = calc_rsi(close_series, RSI_LEN)
    macd_line, signal_line, hist_line = calc_macd(close_series, MACD_FAST, MACD_SLOW, MACD_SIG)
    atr14 = calc_atr(high_series, low_series, close_series, 14)
    
    pivot_high = find_pivot_high(high_series, LEFT_BARS, RIGHT_BARS)
    pivot_low = find_pivot_low(low_series, LEFT_BARS, RIGHT_BARS)
    
    last_confirmed = n - 1 - RIGHT_BARS
    
    ph_price_2 = state.ph_price_2
    ph_price_1 = state.ph_price_1
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
    
    if not pd.isna(pivot_high.iloc[last_confirmed]):
        ph_price_1 = ph_price_2
        ph_bar_1 = ph_bar_2
        ph_rsi_1 = ph_rsi_2
        ph_macdline_1 = ph_macdline_2
        ph_hist_1 = ph_hist_2
        
        real_bar = last_confirmed - RIGHT_BARS
        ph_price_2 = float(pivot_high.iloc[last_confirmed])
        ph_bar_2 = real_bar
        ph_rsi_2 = float(rsi_val.iloc[real_bar])
        ph_macdline_2 = float(macd_line.iloc[real_bar])
        ph_hist_2 = float(hist_line.iloc[real_bar])
        new_pivot_high = True
        
        state.ph_price_2 = ph_price_2
        state.ph_price_1 = ph_price_1
        state.ph_bar_2 = ph_bar_2
        state.ph_bar_1 = ph_bar_1
        state.ph_rsi_2 = ph_rsi_2
        state.ph_rsi_1 = ph_rsi_1
        state.ph_macdline_2 = ph_macdline_2
        state.ph_macdline_1 = ph_macdline_1
        state.ph_hist_2 = ph_hist_2
        state.ph_hist_1 = ph_hist_1
        
        logger.info(f"[PIVOT] {symbol} New Pivot High: price={ph_price_2:.4f}, bar={ph_bar_2}")
    
    if not pd.isna(pivot_low.iloc[last_confirmed]):
        pl_price_1 = pl_price_2
        pl_bar_1 = pl_bar_2
        pl_rsi_1 = pl_rsi_2
        pl_macdline_1 = pl_macdline_2
        pl_hist_1 = pl_hist_2
        
        real_bar = last_confirmed - RIGHT_BARS
        pl_price_2 = float(pivot_low.iloc[last_confirmed])
        pl_bar_2 = real_bar
        pl_rsi_2 = float(rsi_val.iloc[real_bar])
        pl_macdline_2 = float(macd_line.iloc[real_bar])
        pl_hist_2 = float(hist_line.iloc[real_bar])
        new_pivot_low = True
        
        state.pl_price_2 = pl_price_2
        state.pl_price_1 = pl_price_1
        state.pl_bar_2 = pl_bar_2
        state.pl_bar_1 = pl_bar_1
        state.pl_rsi_2 = pl_rsi_2
        state.pl_rsi_1 = pl_rsi_1
        state.pl_macdline_2 = pl_macdline_2
        state.pl_macdline_1 = pl_macdline_1
        state.pl_hist_2 = pl_hist_2
        state.pl_hist_1 = pl_hist_1
        
        logger.info(f"[PIVOT] {symbol} New Pivot Low: price={pl_price_2:.4f}, bar={pl_bar_2}")
    
    state.last_processed_ts = closed_df.index[last_confirmed]
    early_signal = new_pivot_high or new_pivot_low
    
    log(f"   n={n}, last_confirmed={last_confirmed}")
    log(f"   new_high={1 if new_pivot_high else 0}, new_low={1 if new_pivot_low else 0} | mem: H={len([x for x in [ph_price_2, ph_price_1] if x is not None])} L={len([x for x in [pl_price_2, pl_price_1] if x is not None])}")
    
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
    if new_pivot_high and ph_bar_1 is not None:
        price_higher_high = ph_price_2 > ph_price_1
        rsi_lower_high = ph_rsi_2 < ph_rsi_1
        macd_lower_high = ph_macdline_2 < ph_macdline_1
        hist_lower_high = ph_hist_2 < ph_hist_1
        both_peaks_green = ph_hist_1 > 0 and ph_hist_2 > 0
        macd_color_high = check_color_change(hist_line, ph_bar_1, ph_bar_2, True)
        
        trend_ok = is_trending_up(close_series, ph_bar_1)
        
        fib_ok = False
        if ph_bar_1 is not None:
            trend_start = find_trend_start_low(low_series, ph_bar_1)
            fib_ok = check_fib_level(trend_start, ph_price_1, ph_price_2, True)
        
        confirm_bar = min(ph_bar_2 + RIGHT_BARS, n - 1)
        pa_ok, _ = check_price_action(closed_df_reset, confirm_bar, "SELL", atr14.iloc[confirm_bar])
        
        classic_bearish_cond1_rsi = price_higher_high and rsi_lower_high
        classic_bearish_cond2_macdl = price_higher_high and macd_lower_high
        classic_bearish_cond3_macdh = price_higher_high and hist_lower_high and both_peaks_green and macd_color_high
        classic_bearish_base3 = price_higher_high and trend_ok and classic_bearish_cond3_macdh and classic_bearish_cond1_rsi and classic_bearish_cond2_macdl
        
        log(f"   🔴 CD- check | PH1={ph_price_1:.4f} (RSI={ph_rsi_1:.2f}) → PH2={ph_price_2:.4f} (RSI={ph_rsi_2:.2f})")
        
        if classic_bearish_base3:
            if passes_min_requirement(classic_bearish_base3, fib_ok, pa_ok):
                entry_price = float(close_series.iloc[-1])
                
                if pl_price_2 is not None and pl_price_1 is not None:
                    stop_price = min(pl_price_1, pl_price_2) - STOP_BUFFER_PCT * atr14.iloc[-1]
                    mid_peak = high_series.iloc[pl_bar_1+1:pl_bar_2].max() if pl_bar_1 is not None and pl_bar_2 is not None else None
                    if mid_peak is not None and not pd.isna(mid_peak):
                        target_price = mid_peak
                        
                        details = [
                            f"✅ priceHigherHigh and rsiLowerHighOnPeaks",
                            f"✅ priceHigherHigh and macdLineLowerHighOnPeaks",
                            f"✅ priceHigherHigh and histLowerHighOnPeaks and bothPeaksGreen and macdColorChangedForHighs",
                            f"✅ trendOkForBearish",
                            f"✅ fibScoreBearish" if fib_ok else "❌ fibScoreBearish",
                            f"✅ priceActionBearishAtPivot" if pa_ok else "❌ priceActionBearishAtPivot"
                        ]
                        score = 3 + (1 if trend_ok else 0) + (1 if fib_ok else 0) + (1 if pa_ok else 0)
                        
                        if score > best_score:
                            best_signal = "SELL"
                            best_entry = entry_price
                            best_stop = stop_price
                            best_target = target_price
                            best_emoji = "🔴"
                            best_label = "Classic Bearish"
                            best_score = score
                            best_details = details
                            best_pivot1 = {'price': ph_price_1, 'rsi': ph_rsi_1, 'macdline': ph_macdline_1, 'hist': ph_hist_1, 'bar': ph_bar_1}
                            best_pivot2 = {'price': ph_price_2, 'rsi': ph_rsi_2, 'macdline': ph_macdline_2, 'hist': ph_hist_2, 'bar': ph_bar_2}
        else:
            log(f"   🔴 CD- score=0/6")
            log(f"      {'✅' if rsi_lower_high else '❌'} RSI (rsiLowerHighOnPeaks)")
            log(f"      {'✅' if macd_lower_high else '❌'} MACD Line (macdLineLowerHighOnPeaks)")
            log(f"      {'✅' if hist_lower_high and both_peaks_green and macd_color_high else '❌'} MACD Histogram")
            log(f"      {'✅' if trend_ok else '❌'} Trend (trendOkForBearish)")
            log(f"      ❌ Base3 برقرار نیست")
    
    # ============================================================
    # 2. Classic Bullish
    # ============================================================
    if new_pivot_low and pl_bar_1 is not None:
        price_lower_low = pl_price_2 < pl_price_1
        rsi_higher_low = pl_rsi_2 > pl_rsi_1
        macd_higher_low = pl_macdline_2 > pl_macdline_1
        hist_higher_low = pl_hist_2 > pl_hist_1
        both_troughs_red = pl_hist_1 < 0 and pl_hist_2 < 0
        macd_color_low = check_color_change(hist_line, pl_bar_1, pl_bar_2, False)
        
        trend_ok = is_trending_down(close_series, pl_bar_1)
        
        fib_ok = False
        if pl_bar_1 is not None:
            trend_start = find_trend_start_high(high_series, pl_bar_1)
            fib_ok = check_fib_level(trend_start, pl_price_1, pl_price_2, False)
        
        confirm_bar = min(pl_bar_2 + RIGHT_BARS, n - 1)
        pa_ok, _ = check_price_action(closed_df_reset, confirm_bar, "BUY", atr14.iloc[confirm_bar])
        
        classic_bullish_cond1_rsi = price_lower_low and rsi_higher_low
        classic_bullish_cond2_macdl = price_lower_low and macd_higher_low
        classic_bullish_cond3_macdh = price_lower_low and hist_higher_low and both_troughs_red and macd_color_low
        classic_bullish_base3 = price_lower_low and trend_ok and classic_bullish_cond3_macdh and classic_bullish_cond1_rsi and classic_bullish_cond2_macdl
        
        log(f"   🟢 CD+ check | PL1={pl_price_1:.4f} (RSI={pl_rsi_1:.2f}) → PL2={pl_price_2:.4f} (RSI={pl_rsi_2:.2f})")
        
        if classic_bullish_base3:
            if passes_min_requirement(classic_bullish_base3, fib_ok, pa_ok):
                entry_price = float(close_series.iloc[-1])
                
                if ph_price_2 is not None and ph_price_1 is not None:
                    stop_price = max(ph_price_1, ph_price_2) + STOP_BUFFER_PCT * atr14.iloc[-1]
                    mid_trough = low_series.iloc[ph_bar_1+1:ph_bar_2].min() if ph_bar_1 is not None and ph_bar_2 is not None else None
                    if mid_trough is not None and not pd.isna(mid_trough):
                        target_price = mid_trough
                        
                        details = [
                            f"✅ priceLowerLow and rsiHigherLowOnTroughs",
                            f"✅ priceLowerLow and macdLineHigherLowOnTroughs",
                            f"✅ priceLowerLow and histHigherLowOnTroughs and bothTroughsRed and macdColorChangedForLows",
                            f"✅ trendOkForBullish",
                            f"✅ fibScoreBullish" if fib_ok else "❌ fibScoreBullish",
                            f"✅ priceActionBullishAtPivot" if pa_ok else "❌ priceActionBullishAtPivot"
                        ]
                        score = 3 + (1 if trend_ok else 0) + (1 if fib_ok else 0) + (1 if pa_ok else 0)
                        
                        if score > best_score:
                            best_signal = "BUY"
                            best_entry = entry_price
                            best_stop = stop_price
                            best_target = target_price
                            best_emoji = "🟢"
                            best_label = "Classic Bullish"
                            best_score = score
                            best_details = details
                            best_pivot1 = {'price': pl_price_1, 'rsi': pl_rsi_1, 'macdline': pl_macdline_1, 'hist': pl_hist_1, 'bar': pl_bar_1}
                            best_pivot2 = {'price': pl_price_2, 'rsi': pl_rsi_2, 'macdline': pl_macdline_2, 'hist': pl_hist_2, 'bar': pl_bar_2}
        else:
            log(f"   🟢 CD+ score=0/6")
            log(f"      {'✅' if rsi_higher_low else '❌'} RSI (rsiHigherLowOnTroughs)")
            log(f"      {'✅' if macd_higher_low else '❌'} MACD Line (macdLineHigherLowOnTroughs)")
            log(f"      {'✅' if hist_higher_low and both_troughs_red and macd_color_low else '❌'} MACD Histogram")
            log(f"      {'✅' if trend_ok else '❌'} Trend (trendOkForBullish)")
            log(f"      ❌ Base3 برقرار نیست")
    
    # ============================================================
    # 3. Hidden Bullish
    # ============================================================
    if new_pivot_low and pl_bar_1 is not None and ENABLE_HIDDEN:
        price_higher_low = pl_price_2 > pl_price_1
        rsi_lower_low = pl_rsi_2 < pl_rsi_1
        macd_lower_low = pl_macdline_2 < pl_macdline_1
        hist_lower_low = pl_hist_2 < pl_hist_1
        both_troughs_red = pl_hist_1 < 0 and pl_hist_2 < 0
        macd_color_low = check_color_change(hist_line, pl_bar_1, pl_bar_2, False)
        
        trend_ok = is_trending_up(close_series, pl_bar_1)
        
        fib_ok = False
        if pl_bar_1 is not None:
            trend_start = find_trend_start_high(high_series, pl_bar_1)
            fib_ok = check_fib_level(trend_start, pl_price_1, pl_price_2, False)
        
        confirm_bar = min(pl_bar_2 + RIGHT_BARS, n - 1)
        pa_ok, _ = check_price_action(closed_df_reset, confirm_bar, "BUY", atr14.iloc[confirm_bar])
        
        hidden_bullish_cond1_rsi = price_higher_low and rsi_lower_low
        hidden_bullish_cond2_macdl = price_higher_low and macd_lower_low
        hidden_bullish_cond3_macdh = price_higher_low and hist_lower_low and both_troughs_red and macd_color_low
        hidden_bullish_base3 = ENABLE_HIDDEN and price_higher_low and hidden_bullish_cond3_macdh and hidden_bullish_cond1_rsi and hidden_bullish_cond2_macdl
        
        log(f"   🔵 HD+ check | PL1={pl_price_1:.4f} (RSI={pl_rsi_1:.2f}) → PL2={pl_price_2:.4f} (RSI={pl_rsi_2:.2f})")
        
        if hidden_bullish_base3:
            if passes_min_requirement(hidden_bullish_base3, fib_ok, pa_ok):
                entry_price = float(close_series.iloc[-1])
                
                if ph_price_2 is not None and ph_price_1 is not None:
                    stop_price = max(ph_price_1, ph_price_2) + STOP_BUFFER_PCT * atr14.iloc[-1]
                    mid_trough = low_series.iloc[ph_bar_1+1:ph_bar_2].min() if ph_bar_1 is not None and ph_bar_2 is not None else None
                    if mid_trough is not None and not pd.isna(mid_trough):
                        target_price = mid_trough
                        
                        details = [
                            f"✅ priceHigherLow and rsiLowerLowOnTroughs",
                            f"✅ priceHigherLow and macdLineLowerLowOnTroughs",
                            f"✅ priceHigherLow and histLowerLowOnTroughs and bothTroughsRed and macdColorChangedForLows",
                            f"✅ trendOkForBullish" if trend_ok else "❌ trendOkForBullish",
                            f"✅ fibScoreBullish" if fib_ok else "❌ fibScoreBullish",
                            f"✅ priceActionBullishAtPivot" if pa_ok else "❌ priceActionBullishAtPivot"
                        ]
                        score = 3 + (1 if trend_ok else 0) + (1 if fib_ok else 0) + (1 if pa_ok else 0)
                        
                        if score > best_score:
                            best_signal = "BUY"
                            best_entry = entry_price
                            best_stop = stop_price
                            best_target = target_price
                            best_emoji = "🔵"
                            best_label = "Hidden Bullish"
                            best_score = score
                            best_details = details
                            best_pivot1 = {'price': pl_price_1, 'rsi': pl_rsi_1, 'macdline': pl_macdline_1, 'hist': pl_hist_1, 'bar': pl_bar_1}
                            best_pivot2 = {'price': pl_price_2, 'rsi': pl_rsi_2, 'macdline': pl_macdline_2, 'hist': pl_hist_2, 'bar': pl_bar_2}
        else:
            log(f"   🔵 HD+ score=0/6")
            log(f"      {'✅' if rsi_lower_low else '❌'} RSI (rsiLowerLowOnTroughs)")
            log(f"      {'✅' if macd_lower_low else '❌'} MACD Line (macdLineLowerLowOnTroughs)")
            log(f"      {'✅' if hist_lower_low and both_troughs_red and macd_color_low else '❌'} MACD Histogram")
            log(f"      ❌ Base3 برقرار نیست")
    
    # ============================================================
    # 4. Hidden Bearish
    # ============================================================
    if new_pivot_high and ph_bar_1 is not None and ENABLE_HIDDEN:
        price_lower_high = ph_price_2 < ph_price_1
        rsi_higher_high = ph_rsi_2 > ph_rsi_1
        macd_higher_high = ph_macdline_2 > ph_macdline_1
        hist_higher_high = ph_hist_2 > ph_hist_1
        both_peaks_green = ph_hist_1 > 0 and ph_hist_2 > 0
        macd_color_high = check_color_change(hist_line, ph_bar_1, ph_bar_2, True)
        
        trend_ok = is_trending_down(close_series, ph_bar_1)
        
        fib_ok = False
        if ph_bar_1 is not None:
            trend_start = find_trend_start_low(low_series, ph_bar_1)
            fib_ok = check_fib_level(trend_start, ph_price_1, ph_price_2, True)
        
        confirm_bar = min(ph_bar_2 + RIGHT_BARS, n - 1)
        pa_ok, _ = check_price_action(closed_df_reset, confirm_bar, "SELL", atr14.iloc[confirm_bar])
        
        hidden_bearish_cond1_rsi = price_lower_high and rsi_higher_high
        hidden_bearish_cond2_macdl = price_lower_high and macd_higher_high
        hidden_bearish_cond3_macdh = price_lower_high and hist_higher_high and both_peaks_green and macd_color_high
        hidden_bearish_base3 = ENABLE_HIDDEN and price_lower_high and hidden_bearish_cond3_macdh and hidden_bearish_cond1_rsi and hidden_bearish_cond2_macdl
        
        log(f"   🟠 HD- check | PH1={ph_price_1:.4f} (RSI={ph_rsi_1:.2f}) → PH2={ph_price_2:.4f} (RSI={ph_rsi_2:.2f})")
        
        if hidden_bearish_base3:
            if passes_min_requirement(hidden_bearish_base3, fib_ok, pa_ok):
                entry_price = float(close_series.iloc[-1])
                
                if pl_price_2 is not None and pl_price_1 is not None:
                    stop_price = min(pl_price_1, pl_price_2) - STOP_BUFFER_PCT * atr14.iloc[-1]
                    mid_peak = high_series.iloc[pl_bar_1+1:pl_bar_2].max() if pl_bar_1 is not None and pl_bar_2 is not None else None
                    if mid_peak is not None and not pd.isna(mid_peak):
                        target_price = mid_peak
                        
                        details = [
                            f"✅ priceLowerHigh and rsiHigherHighOnPeaks",
                            f"✅ priceLowerHigh and macdLineHigherHighOnPeaks",
                            f"✅ priceLowerHigh and histHigherHighOnPeaks and bothPeaksGreen and macdColorChangedForHighs",
                            f"✅ trendOkForBearish" if trend_ok else "❌ trendOkForBearish",
                            f"✅ fibScoreBearish" if fib_ok else "❌ fibScoreBearish",
                            f"✅ priceActionBearishAtPivot" if pa_ok else "❌ priceActionBearishAtPivot"
                        ]
                        score = 3 + (1 if trend_ok else 0) + (1 if fib_ok else 0) + (1 if pa_ok else 0)
                        
                        if score > best_score:
                            best_signal = "SELL"
                            best_entry = entry_price
                            best_stop = stop_price
                            best_target = target_price
                            best_emoji = "🟠"
                            best_label = "Hidden Bearish"
                            best_score = score
                            best_details = details
                            best_pivot1 = {'price': ph_price_1, 'rsi': ph_rsi_1, 'macdline': ph_macdline_1, 'hist': ph_hist_1, 'bar': ph_bar_1}
                            best_pivot2 = {'price': ph_price_2, 'rsi': ph_rsi_2, 'macdline': ph_macdline_2, 'hist': ph_hist_2, 'bar': ph_bar_2}
        else:
            log(f"   🟠 HD- score=0/6")
            log(f"      {'✅' if rsi_higher_high else '❌'} RSI (rsiHigherHighOnPeaks)")
            log(f"      {'✅' if macd_higher_high else '❌'} MACD Line (macdLineHigherHighOnPeaks)")
            log(f"      {'✅' if hist_higher_high and both_peaks_green and macd_color_high else '❌'} MACD Histogram")
            log(f"      ❌ Base3 برقرار نیست")
    
    save_states()
    
    if best_signal is None:
        log(f"   ⚪ No signal (none passed Base3)")
        
        log(f"   ======================================================================")
        log(f"   🔬 FULL DEBUG LOG (Pine-Exact Pivot + Base3 Gating v2.0)")
        log(f"   ======================================================================")
        log(f"   📊 GENERAL:")
        log(f"      Symbol: {symbol}")
        log(f"      Time: {format_iran_time()}")
        log(f"      Total Candles (n): {n}")
        log(f"      New Pivot Highs: {1 if new_pivot_high else 0}")
        log(f"      New Pivot Lows: {1 if new_pivot_low else 0}")
        log(f"      Total Pivot Highs (mem): {len([x for x in [ph_price_2, ph_price_1] if x is not None])}")
        log(f"      Total Pivot Lows (mem): {len([x for x in [pl_price_2, pl_price_1] if x is not None])}")
        log(f"")
        log(f"   📈 CURRENT INDICATORS:")
        log(f"      Entry Price: {close_series.iloc[-1]:.4f}")
        log(f"      RSI(14)[-1]: {rsi_val.iloc[-1]:.2f}")
        log(f"      MACD Line[-1]: {macd_line.iloc[-1]:.6f}")
        log(f"      MACD Hist[-1]: {hist_line.iloc[-1]:.6f}")
        log(f"      ATR(14)[-1]: {atr14.iloc[-1]:.4f}")
        log(f"")
        log(f"   🏁 FINAL RESULT:")
        log(f"      ❌ NO SIGNAL")
        log(f"   ======================================================================")
    
    save_debug_log_to_file(symbol, debug_file_lines)
    
    if not hasattr(detect_signal, "_debug_count"):
        detect_signal._debug_count = 0
    
    if detect_signal._debug_count < 5 and best_signal is None:
        summary_log = []
        summary_log.append(f"🟢 لاگ #{detect_signal._debug_count + 1} — {symbol} {HASHTAGS['log']}")
        summary_log.append("━━━━━━━━━━━━━━━━━━━━━━")
        for line in debug_log:
            if line.startswith("🔍") or line.startswith("   n=") or line.startswith("   new_high") or \
               line.startswith("   🔴") or line.startswith("   🟢") or line.startswith("   🔵") or line.startswith("   🟠") or \
               line.startswith("   ⚪") or line.startswith("   ==") or line.startswith("   📊") or \
               line.startswith("   📈") or line.startswith("   🏁") or line.startswith("      ❌") or \
               line.startswith("      ✅"):
                summary_log.append(line)
        summary_log.append("━━━━━━━━━━━━━━━━━━━━━━")
        summary_log.append(f"🕒 {format_iran_time()}")
        
        send_telegram_message("\n".join(summary_log))
        detect_signal._debug_count += 1
    
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
    except Exception as e:
        diagnostic_log.append(f"🔴 خطا: {str(e)[:50]}")
    
    diagnostic_log.append(f"🟢 پارامترهای استراتژی (مطابق کد اول):")
    diagnostic_log.append(f"   • PIVOT_MODE: {PIVOT_MODE}")
    diagnostic_log.append(f"   • RSI_LEN: {RSI_LEN}")
    diagnostic_log.append(f"   • MACD: {MACD_FAST}/{MACD_SLOW}/{MACD_SIG}")
    diagnostic_log.append(f"   • TREND_LOOKBACK: {TREND_LOOKBACK}")
    diagnostic_log.append(f"   • TREND_SLOPE_MIN_PCT: {TREND_SLOPE_MIN_PCT}%")
    diagnostic_log.append(f"   • MIN_CONFIRMATIONS: {MIN_CONFIRMATIONS}")
    diagnostic_log.append(f"   • ENABLE_HIDDEN: {ENABLE_HIDDEN}")
    diagnostic_log.append(f"   • FIB_USE_618: {FIB_USE_618}")
    diagnostic_log.append(f"   • FIB_USE_786: {FIB_USE_786}")
    diagnostic_log.append(f"   • FIB_TOLERANCE_PCT: {FIB_TOLERANCE_PCT}%")
    diagnostic_log.append(f"   • FIB_SEARCH_BARS: {FIB_TREND_SEARCH_BARS}")
    
    diagnostic_log.append("🟢 موتور امتیازدهی: Base3 + Trend/Fib/PA")
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
                entry = exchange._round_price(entry, symbol)
                stop = exchange._round_price(stop, symbol)
                target = exchange._round_price(target, symbol)

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
                
                pivot1_info = f"Pivot اول: قیمت {pivot1['price']:.4f} @ کندل {pivot1['bar']} (RSI={pivot1['rsi']:.2f})" if pivot1 else "Pivot اول: نامشخص"
                pivot2_info = f"Pivot دوم: قیمت {pivot2['price']:.4f} @ کندل {pivot2['bar']} (RSI={pivot2['rsi']:.2f})" if pivot2 else "Pivot دوم: نامشخص"
                
                signal_message = (
                    f"{emoji} {signal_type} — {symbol} #Signal_{signal_number}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 Score: {score}/6\n"
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
                            'pivot1_bar': pivot1['bar'] if pivot1 else None,
                            'pivot1_price': pivot1['price'] if pivot1 else None,
                            'pivot1_rsi': pivot1['rsi'] if pivot1 else None,
                            'pivot2_bar': pivot2['bar'] if pivot2 else None,
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
        f"🧠 منطق: عین dtm(1).py (بدون MTF)\n"
        f"📊 Pivot: {PIVOT_MODE} ({LEFT_BARS}/{RIGHT_BARS})\n"
        f"⚙️ Base3: RSI + MACD Line + MACD Histogram\n"
        f"⚙️ Trend + Fibonacci + PA (امتیازی)\n"
        f"⚙️ ۴ نوع واگرایی: Classic/Hidden + BUY/SELL\n"
        f"🔧 ETH=50x | LTC/DOGE=75x\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 پارامترها:\n"
        f"   RSI_LEN: {RSI_LEN}\n"
        f"   MACD: {MACD_FAST}/{MACD_SLOW}/{MACD_SIG}\n"
        f"   TREND_LOOKBACK: {TREND_LOOKBACK}\n"
        f"   TREND_SLOPE_MIN_PCT: {TREND_SLOPE_MIN_PCT}%\n"
        f"   ENABLE_HIDDEN: {ENABLE_HIDDEN}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕒 {format_iran_time()}"
    )
    
    run_startup_diagnostic()
    
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=10000), daemon=True).start()
    logger.info("[STARTUP] Flask روی پورت 10000")
    main_loop()
