# -*- coding: utf-8 -*-
"""
DTM Divergence Auto-Trading Bot - TheTrueTrade (نسخه هیبریدی)
====================================================================
نسخه نهایی کامل — منطق واگرایی دقیقاً مطابق Pine Script:
- Pivot: حالت سریع (5/3)
- RSI با calc_rma (Wilder Smoothing مثل ta.rma در Pine) — **FIXED**: منطق 0/100 مثل Pine، بدون fillna
- ATR با calc_rma (Wilder Smoothing مثل ta.atr در Pine)
- EMA دقیقاً مثل Pine (بدون SMA seed) — **FIXED**: از کندل اول با مقدار خودش شروع میشه
- عمق داده 5000 کندل (معادل حساب رایگان TradingView) — **FIXED**: با برش tail()
- رفع درز State پیوت‌ها (start_bar=0 همیشه)
- RSI/MACD در همان کندل Pivot
- شرط روند: تفاضل مقدار برازش دو رگرسیون (مثل ta.linreg)
- کندل تأییدیه: روی کندل تأیید (bar2 + RIGHT_BARS)
- رفع Off-by-one در mid_peak/mid_trough
- رفع فرمول avg_body در Price Action
- **FIXED [BUG A]**: check_macd_color_change — معادل دقیق Pine (existence check ساده)
- رفع Gating: RSI + MACD Line + MACD Histogram هر سه اجباری
- رفع فرمول فیبوناچی
- **FIXED [CATCH-UP]**: بررسی همه پیوت‌های جدید با پیوت قبلی‌شان (حلقه روی new_pivots)
- رفع فیلتر روند برای Hidden Divergence
- **FIXED [BUG B]**: FIB_SEARCH_BARS = 100 (مطابق Pine)
- **FIXED [ENHANCEMENT C]**: ATR در بار تأیید (confirm bar) خوانده میشه
- **FIXED [ENHANCEMENT D]**: Bar-State Safety Net (حذف کندل باز)
- **FIXED [RIGOR]**: calc_rma با تطبیق دقیق معناشناسی na در Pine
- State persistence — پیوت‌ها در فایل ذخیره میشن
- اعتبارسنجی df.iloc[:-1]
- fetch_balance از /futures/assets
- استفاده از cost (مارجین) برای MARKET order
- نمایش موجودی در پیام اتصال صرافی
- هشتگ‌گذاری همه پیام‌های تلگرام
- شماره‌گذاری سیگنال‌ها
- بافر 2% موجودی برای جلوگیری از insufficient balance
- time.sleep بین پیام سیگنال و سفارش
- پیگیری پوزیشن‌های باز از طریق API صرافی
- دریافت تاریخچه معاملات از صرافی برای گزارش‌ها
- پیام سیگنال انگلیسی (بدون بخش دلایل)
- گزارش واقعی صرافی (معاملات + موجودی)
- **SECURITY**: کلیدهای API فقط از متغیر محیطی خوانده میشن
- **DEBUG**: Full Debug Log (فایل `full_debug_log.txt`)
- **FINAL FIX**: True bar-by-bar state machine matching Pine execution model
  (new pivots only processed on their exact confirmation bar)
"""

import os
import time
import threading
import traceback
import hashlib
import hmac
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from flask import Flask
import json
import logging

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
    raise RuntimeError(
        "API_KEY / API_SECRET باید به‌عنوان متغیر محیطی ست شوند."
    )
if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID باید به‌عنوان متغیر محیطی ست شوند."
    )

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
    "proximity_target": "#NearTP",
    "proximity_stop": "#NearSL",
    "order_request": "#OrderReq",
    "order_response": "#OrderOK",
    "order_error": "#OrderErr",
    "connection": "#Connected",
    "connection_change": "#Reconnected",
    "capital_reduced": "#LowCapital",
}

# =====================================================================================
# ثابت‌های استراتژی
# =====================================================================================
TREND_LOOKBACK = 20
TREND_SLOPE_MIN_PCT = 0.05
FIB_USE_618 = True
FIB_USE_786 = True
FIB_TOLERANCE_PCT = 0.5
FIB_SEARCH_BARS = 100
STOP_BUFFER_PCT = 0.05

LEFT_BARS = 5
RIGHT_BARS = 3

SHADOW_TO_BODY_RATIO = 2.0
MAX_OPPOSITE_SHADOW_PCT = 20.0
MIN_CANDLE_ATR_RATIO = 0.3
BIG_CANDLE_AVG_LEN = 14
BIG_CANDLE_MULTIPLIER = 1.5

API_RETURNS_OPEN_CANDLE = False

HISTORY_BARS = 5000

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

            logger.info(f"[BALANCE] Response: {json.dumps(data)[:500]}")

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
                params['stopLoss'] = round_price(params['stopLoss'], symbol)
            if 'takeProfit' in params:
                params['takeProfit'] = round_price(params['takeProfit'], symbol)

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
            f"📤 ثبت سفارش - درخواست {HASHTAGS['order_request']}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔹 Symbol: {symbol}\n"
            f"🔸 Side: {side.upper()}\n"
            f"🔸 Type: {order_type.upper()}\n"
            f"💰 Cost: {capital:.{prec}f}\n"
            f"🔧 Leverage: {order_data['leverage']}\n"
            f"📦 Body:\n```\n{json.dumps(order_data, indent=2)}\n```\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🕒 {format_iran_time()}"
        )

        try:
            result = self._request('POST', '/futures/positions', order_data)

            send_telegram_message(
                f"📥 ثبت سفارش - پاسخ {HASHTAGS['order_response']}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🔹 Symbol: {symbol}\n"
                f"✅ Success - Position ID: {result.get('positionId', 'N/A')}\n"
                f"📦 Response:\n```\n{json.dumps(result, indent=2)[:500]}\n```\n"
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
                f"❌ ثبت سفارش - خطا {HASHTAGS['order_error']}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🔹 Symbol: {symbol}\n"
                f"🔸 Side: {side.upper()}\n"
                f"📊 Status: {status_code}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📝 دلیل خطا:\n{error_detail if error_detail else str(e)[:500]}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📤 درخواست:\n```\n{json.dumps(order_data, indent=2)}\n```\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📥 پاسخ کامل:\n```\n{error_body[:1000]}\n```\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🕒 {format_iran_time()}"
            )
            raise

# =====================================================================================
# توابع تلگرام
# =====================================================================================
def send_telegram_message(message: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        logger.error(f"[TELEGRAM] {e}")

def format_iran_time(dt=None):
    if dt is None:
        dt = datetime.now(timezone(timedelta(hours=3, minutes=30)))
    return dt.strftime('%Y-%m-%d %H:%M:%S')

def format_iran_date(dt=None):
    if dt is None:
        dt = datetime.now(timezone(timedelta(hours=3, minutes=30)))
    return dt.strftime('%Y-%m-%d')

# =====================================================================================
# توابع محاسباتی پایه (Pine-Exact)
# =====================================================================================
def calc_rma(series, length):
    """
    معادل دقیق ta.rma (Wilder Smoothing) در Pine Script.
    """
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

def calc_ema(series, length):
    """
    EMA دقیقاً مانند Pine Script (ta.ema).
    """
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

def calc_rsi(close, length=14):
    """
    RSI دقیقاً مثل Pine.
    """
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
    ema_fast = calc_ema(close, fast)
    ema_slow = calc_ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calc_ema(macd_line, signal)
    return macd_line, signal_line, macd_line - signal_line

def calc_atr(high, low, close, length=14):
    """ATR با Wilder Smoothing (calc_rma) دقیقاً مثل ta.atr در Pine"""
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return calc_rma(tr, length)

def find_pivot_high(high, left=5, right=3):
    """تشخیص قله‌ها"""
    n = len(high)
    result = pd.Series(np.nan, index=high.index)
    for i in range(left, n - right):
        if not (high.iloc[i-left:i] >= high.iloc[i]).any() and not (high.iloc[i+1:i+right+1] >= high.iloc[i]).any():
            result.iloc[i] = high.iloc[i]
    return result

def find_pivot_low(low, left=5, right=3):
    """تشخیص دره‌ها"""
    n = len(low)
    result = pd.Series(np.nan, index=low.index)
    for i in range(left, n - right):
        if not (low.iloc[i-left:i] <= low.iloc[i]).any() and not (low.iloc[i+1:i+right+1] <= low.iloc[i]).any():
            result.iloc[i] = low.iloc[i]
    return result

def _linreg_end(y):
    """محاسبه مقدار پایانی رگرسیون خطی"""
    x = np.arange(len(y))
    slope, intercept = np.polyfit(x, y, 1)
    return intercept + slope * (len(y) - 1)

def is_trending_up(close, ref_bar, lookback=TREND_LOOKBACK, slope_min_pct=TREND_SLOPE_MIN_PCT):
    """تشخیص روند صعودی"""
    if ref_bar is None or ref_bar - 2 * lookback + 1 < 0:
        return False
    
    y_current = close.iloc[ref_bar - lookback + 1:ref_bar + 1].values
    y_past = close.iloc[ref_bar - 2 * lookback + 1:ref_bar - lookback + 1].values
    
    if len(y_current) < 2 or len(y_past) < 2:
        return False
    
    fitted_end_current = _linreg_end(y_current)
    fitted_end_past = _linreg_end(y_past)
    total_slope = fitted_end_current - fitted_end_past
    
    avg = y_current.mean()
    if avg == 0:
        return False
    
    return (total_slope / avg) * 100 > slope_min_pct

def is_trending_down(close, ref_bar, lookback=TREND_LOOKBACK, slope_min_pct=TREND_SLOPE_MIN_PCT):
    """تشخیص روند نزولی"""
    if ref_bar is None or ref_bar - 2 * lookback + 1 < 0:
        return False
    
    y_current = close.iloc[ref_bar - lookback + 1:ref_bar + 1].values
    y_past = close.iloc[ref_bar - 2 * lookback + 1:ref_bar - lookback + 1].values
    
    if len(y_current) < 2 or len(y_past) < 2:
        return False
    
    fitted_end_current = _linreg_end(y_current)
    fitted_end_past = _linreg_end(y_past)
    total_slope = fitted_end_current - fitted_end_past
    
    avg = y_current.mean()
    if avg == 0:
        return False
    
    return (total_slope / avg) * 100 < -slope_min_pct

def resolve_bar_from_ts(df_indexed, ts):
    if ts not in df_indexed.index:
        return None
    return df_indexed.index.get_loc(ts)

def check_macd_color_change(hist_series, bar1, bar2, need_negative_phase):
    """
    معادل دقیق تابع Pine: یه existence check ساده.
    """
    if bar1 is None or bar2 is None or bar2 <= bar1:
        return False

    lo = bar1 + 1
    hi = bar2 - 1
    if hi < lo:
        return False
    if lo < 0 or hi >= len(hist_series):
        return False

    window = hist_series.iloc[lo:hi + 1]
    if need_negative_phase:
        return bool((window < 0).any())
    return bool((window > 0).any())

def find_trend_start_low(low_series, ref_bar, search_bars=FIB_SEARCH_BARS):
    """پیدا کردن پایین‌ترین کف"""
    if ref_bar is None:
        return None
    start = max(0, ref_bar - search_bars + 1)
    window = low_series.iloc[start:ref_bar + 1]
    return window.min() if len(window) > 0 else None

def find_trend_start_high(high_series, ref_bar, search_bars=FIB_SEARCH_BARS):
    """پیدا کردن بالاترین سقف"""
    if ref_bar is None:
        return None
    start = max(0, ref_bar - search_bars + 1)
    window = high_series.iloc[start:ref_bar + 1]
    return window.max() if len(window) > 0 else None

def check_fib_level(fib_start, fib_end, target_price, is_retrace_down,
                     use_618=FIB_USE_618, use_786=FIB_USE_786, tolerance_pct=FIB_TOLERANCE_PCT):
    if fib_start is None or fib_end is None or fib_end == fib_start:
        return False
    range_ = fib_end - fib_start
    tol = abs(range_) * (tolerance_pct / 100.0)
    if is_retrace_down:
        level618 = fib_end - range_ * 0.618
        level786 = fib_end - range_ * 0.786
    else:
        level618 = fib_end + abs(range_) * 0.618
        level786 = fib_end + abs(range_) * 0.786
    ok = False
    if use_618 and abs(target_price - level618) <= tol:
        ok = True
    if use_786 and abs(target_price - level786) <= tol:
        ok = True
    return ok

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
        avg_body = candle_body
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
            pa = True; pa_reasons.append("Bullish Wick (Hammer)")
        if big_green:
            pa = True; pa_reasons.append("Big Green Candle")
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
            pa = True; pa_reasons.append("Bearish Wick (Shooting Star)")
        if bearish_hanging:
            pa = True; pa_reasons.append("Bearish Hanging Man")
        if big_red:
            pa = True; pa_reasons.append("Big Red Candle")
    return pa, pa_reasons

def compute_stop_and_targets(pivot_highs, pivot_lows, direction, df_indexed, atr_val, stop_buffer_pct=STOP_BUFFER_PCT):
    if direction == "long":
        if len(pivot_lows) < 2:
            return None, None, None
        pl_1, pl_2 = pivot_lows[-2], pivot_lows[-1]
        bar1 = resolve_bar_from_ts(df_indexed, pl_1['ts'])
        bar2 = resolve_bar_from_ts(df_indexed, pl_2['ts'])
        if bar1 is None or bar2 is None or bar2 <= bar1:
            return None, None, None
        stop_price = min(pl_1['price'], pl_2['price']) - stop_buffer_pct * atr_val
        mid_peak = df_indexed["high"].iloc[bar1+1:bar2+1].max()
        if pd.isna(mid_peak):
            return None, None, None
        return stop_price, mid_peak, None
    elif direction == "short":
        if len(pivot_highs) < 2:
            return None, None, None
        ph_1, ph_2 = pivot_highs[-2], pivot_highs[-1]
        bar1 = resolve_bar_from_ts(df_indexed, ph_1['ts'])
        bar2 = resolve_bar_from_ts(df_indexed, ph_2['ts'])
        if bar1 is None or bar2 is None or bar2 <= bar1:
            return None, None, None
        stop_price = max(ph_1['price'], ph_2['price']) + stop_buffer_pct * atr_val
        mid_trough = df_indexed["low"].iloc[bar1+1:bar2+1].min()
        if pd.isna(mid_trough):
            return None, None, None
        return stop_price, mid_trough, None
    return None, None, None

def resolve_final_target(entry, stop, tp_raw, direction, min_rr=2.0):
    risk = abs(entry - stop)
    if risk <= 0:
        return tp_raw
    rr = abs(tp_raw - entry) / risk
    if rr >= min_rr:
        return tp_raw
    return entry + risk * min_rr if direction == "long" else entry - risk * min_rr

def round_price(price, symbol):
    tick = TICK_SIZES.get(symbol.upper(), 0.01)
    precision = PRICE_PRECISION.get(symbol.upper(), 2)
    rounded = round(price / tick) * tick
    return round(rounded, precision)

# =====================================================================================
# سیستم امتیازدهی
# =====================================================================================
def calculate_divergence_score(p1, p2, direction, bar1, bar2, hist_series, high_series, low_series, df_indexed, atr_series):
    details = []

    if direction == "BUY":
        rsi_ok = (p2['price'] < p1['price'] and p2['rsi'] > p1['rsi']) or (p2['price'] > p1['price'] and p2['rsi'] < p1['rsi'])
    else:
        rsi_ok = (p2['price'] > p1['price'] and p2['rsi'] < p1['rsi']) or (p2['price'] < p1['price'] and p2['rsi'] > p1['rsi'])
    details.append("✅ RSI Divergence" if rsi_ok else "❌ RSI")

    if direction == "BUY":
        macdline_ok = (p2['price'] < p1['price'] and p2['macdline'] > p1['macdline']) or (p2['price'] > p1['price'] and p2['macdline'] < p1['macdline'])
    else:
        macdline_ok = (p2['price'] > p1['price'] and p2['macdline'] < p1['macdline']) or (p2['price'] < p1['price'] and p2['macdline'] > p1['macdline'])
    details.append("✅ MACD Line Divergence" if macdline_ok else "❌ MACD Line")

    if direction == "BUY":
        hist_shape_ok = ((p2['price'] < p1['price'] and p2['hist'] > p1['hist']) or (p2['price'] > p1['price'] and p2['hist'] < p1['hist']))
        both_same_sign = p1['hist'] < 0 and p2['hist'] < 0
        color_changed = check_macd_color_change(hist_series, bar1, bar2, need_negative_phase=False)
        macdhist_ok = hist_shape_ok and both_same_sign and color_changed
    else:
        hist_shape_ok = ((p2['price'] > p1['price'] and p2['hist'] < p1['hist']) or (p2['price'] < p1['price'] and p2['hist'] > p1['hist']))
        both_same_sign = p1['hist'] > 0 and p2['hist'] > 0
        color_changed = check_macd_color_change(hist_series, bar1, bar2, need_negative_phase=True)
        macdhist_ok = hist_shape_ok and both_same_sign and color_changed
    details.append("✅ MACD Histogram + Color Change" if macdhist_ok else "❌ MACD Histogram")

    base3 = rsi_ok and macdline_ok and macdhist_ok
    if not base3:
        details.append("❌ حداقل ۳ تأییدیه پایه برقرار نیست")
        return 0, details

    score = 3

    if direction == "BUY":
        trend_start = find_trend_start_high(high_series, bar1)
        fib_ok = check_fib_level(trend_start, p1['price'], p2['price'], is_retrace_down=False)
    else:
        trend_start = find_trend_start_low(low_series, bar1)
        fib_ok = check_fib_level(trend_start, p1['price'], p2['price'], is_retrace_down=True)
    if fib_ok:
        score += 1; details.append("✅ Fibonacci (0.618/0.786)")
    else:
        details.append("❌ Fibonacci")

    confirm_bar2 = bar2 + RIGHT_BARS
    if confirm_bar2 < len(df_indexed):
        pa_ok, pa_reasons = check_price_action(df_indexed, confirm_bar2, direction, atr_series.iloc[confirm_bar2])
    else:
        pa_ok, pa_reasons = False, []
    if pa_ok:
        score += 1; details.append(f"✅ Price Action ({', '.join(pa_reasons)})")
    else:
        details.append("❌ Price Action")

    return score, details

def classify_signal(score):
    if score >= 5: return "🟢", "Ideal"
    elif score >= 4: return "🟡", "Custom"
    elif score >= 3: return "⚪", "Minimal"
    else: return None, None

# =====================================================================================
# شمارنده سیگنال
# =====================================================================================
SIGNAL_COUNTER = 0

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

# =====================================================================================
# کلاس وضعیت (با state persistence)
# =====================================================================================
class SymbolState:
    def __init__(self):
        self.pivot_highs = []
        self.pivot_lows = []
        self.last_processed_ts = None
        self.alert_sent = False
        self.telegram_log_count = 0
        self.last_telegram_log_time = 0
    
    def to_dict(self):
        return {
            'pivot_highs': [{'ts': str(p['ts']), 'price': p['price'], 
                           'rsi': p['rsi'], 'macdline': p['macdline'], 
                           'hist': p['hist']} for p in self.pivot_highs[-200:]],
            'pivot_lows': [{'ts': str(p['ts']), 'price': p['price'],
                          'rsi': p['rsi'], 'macdline': p['macdline'],
                          'hist': p['hist']} for p in self.pivot_lows[-200:]],
            'last_processed_ts': str(self.last_processed_ts) if self.last_processed_ts else None,
            'telegram_log_count': self.telegram_log_count,
            'last_telegram_log_time': self.last_telegram_log_time
        }
    
    @classmethod
    def from_dict(cls, data):
        state = cls()
        if data:
            state.pivot_highs = [{'ts': pd.Timestamp(p['ts']), 'price': p['price'],
                                 'rsi': p['rsi'], 'macdline': p['macdline'],
                                 'hist': p['hist']} for p in data.get('pivot_highs', [])]
            state.pivot_lows = [{'ts': pd.Timestamp(p['ts']), 'price': p['price'],
                                'rsi': p['rsi'], 'macdline': p['macdline'],
                                'hist': p['hist']} for p in data.get('pivot_lows', [])]
            state.last_processed_ts = pd.Timestamp(data['last_processed_ts']) if data.get('last_processed_ts') else None
            state.telegram_log_count = data.get('telegram_log_count', 0)
            state.last_telegram_log_time = data.get('last_telegram_log_time', 0)
        return state

SYMBOLS = ["LTCUSDT", "DOGEUSDT", "ETHUSDT"]
SYMBOL_STATES = {s: SymbolState() for s in SYMBOLS}

def save_states():
    data = {s: SYMBOL_STATES[s].to_dict() for s in SYMBOLS}
    with open(STATE_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def load_states():
    global SYMBOL_STATES
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                data = json.load(f)
            for s in SYMBOLS:
                if s in data:
                    SYMBOL_STATES[s] = SymbolState.from_dict(data[s])
            logger.info(f"[STATE] Loaded pivot states from {STATE_FILE}")
        except Exception as e:
            logger.error(f"[STATE] Error loading states: {e}")

# =====================================================================================
# مدیریت تاریخچه
# =====================================================================================
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

# =====================================================================================
# توابع گزارش
# =====================================================================================
def fetch_exchange_trades_for_report(exchange, symbol=None, start_time=None, end_time=None):
    return exchange.fetch_trade_history(symbol=symbol, start_time=start_time, end_time=end_time)

def generate_daily_report_text(trades):
    today_str = format_iran_date()
    if not trades:
        return None
    total_trades = len(trades)
    total_realized_pnl = sum(float(t.get('realizedPnl', t.get('realized_pnl', 0))) for t in trades)
    wins = len([t for t in trades if float(t.get('realizedPnl', t.get('realized_pnl', 0))) > 0])
    losses = len([t for t in trades if float(t.get('realizedPnl', t.get('realized_pnl', 0))) < 0])
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
    total_realized_pnl = sum(float(t.get('realizedPnl', t.get('realized_pnl', 0))) for t in trades)
    wins = len([t for t in trades if float(t.get('realizedPnl', t.get('realized_pnl', 0))) > 0])
    losses = len([t for t in trades if float(t.get('realizedPnl', t.get('realized_pnl', 0))) < 0])
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

def send_reports(exchange):
    now = datetime.now(timezone(timedelta(hours=3, minutes=30)))
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
            local_daily_msg = f"""📊 گزارش روزانه (محلی) — {today_str} {HASHTAGS['daily']}
━━━━━━━━━━━━━━━━━━━━━━
📈 کل معاملات: {total} عدد
✅ موفق: {wins} ({win_rate:.1f}%)
❌ ناموفق: {losses}
📊 نرخ موفقیت: {win_rate:.1f}%
━━━━━━━━━━━━━━━━━━━━━━
🕒 {format_iran_time()}"""
            send_telegram_message(local_daily_msg)
            logger.info("[REPORT] Local daily report sent.")
    except Exception as e:
        logger.error(f"[REPORT ERROR] Local daily: {e}")
    try:
        now_utc = datetime.now(timezone.utc)
        today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0).strftime('%Y-%m-%dT%H:%M:%SZ')
        today_end = now_utc.strftime('%Y-%m-%dT%H:%M:%SZ')
        
        exchange_trades_today = exchange.fetch_trade_history(start_time=today_start, end_time=today_end)
        current_balance = exchange.fetch_balance()
        
        if isinstance(exchange_trades_today, list) and exchange_trades_today:
            total_realized_pnl = sum(float(t.get('realizedPnl', 0)) for t in exchange_trades_today)
            wins = len([t for t in exchange_trades_today if float(t.get('realizedPnl', 0)) > 0])
            losses = len([t for t in exchange_trades_today if float(t.get('realizedPnl', 0)) < 0])
            total_trades = len(exchange_trades_today)
            win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
            exchange_report_msg = f"""📈 گزارش واقعی صرافی — {format_iran_date()} {HASHTAGS['daily']}
━━━━━━━━━━━━━━━━━━━━━━
💰 موجودی حساب: {current_balance:.2f} USDT
📊 کل معاملات بسته شده: {total_trades} عدد
✅ سودآور: {wins} ({win_rate:.1f}%)
❌ ضررده: {losses}
💵 سود/زیان خالص: {total_realized_pnl:.2f} USDT
📈 نرخ موفقیت: {win_rate:.1f}%
💪 وضعیت: {'عالی! 🚀' if total_realized_pnl > 0 else 'نیاز به بررسی 📊'}
━━━━━━━━━━━━━━━━━━━━━━
🕒 {format_iran_time()}"""
        else:
            exchange_report_msg = f"""📈 گزارش واقعی صرافی — {format_iran_date()} {HASHTAGS['daily']}
━━━━━━━━━━━━━━━━━━━━━━
💰 موجودی حساب: {current_balance:.2f} USDT
📊 امروز معامله بسته شده‌ای نداشته‌اید.
━━━━━━━━━━━━━━━━━━━━━━
🕒 {format_iran_time()}"""
        send_telegram_message(exchange_report_msg)
        logger.info("[REPORT] Exchange-based daily report sent.")
    except Exception as e:
        logger.error(f"[REPORT ERROR] Exchange-based: {e}")

# =====================================================================================
# Startup Diagnostic
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
    diagnostic_log.append("🟢 موتور امتیازدهی: آماده")
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
    try:
        test_trades = exchange.fetch_trade_history()
        if isinstance(test_trades, list):
            diagnostic_log.append(f"🟢 هیستوری معاملات: متصل ({len(test_trades)} رکورد)")
        else:
            diagnostic_log.append("🔴 هیستوری معاملات: پاسخ نامعتبر از سرور")
    except Exception as e:
        diagnostic_log.append(f"🔴 هیستوری معاملات: قطع\n📝 خطا: {str(e)[:200]}")
    diagnostic_log.append("\n━━━━━━━━━━━━━━━━━━━━━━")
    diagnostic_log.append("✅ تمام بخش‌ها فعال هستند" if conn else "⚠️ برخی بخش‌ها غیرفعال هستند")
    diagnostic_log.append(f"🕒 {format_iran_time()}")
    send_telegram_message("\n".join(diagnostic_log))
    logger.info("Startup Diagnostic Complete")

# =====================================================================================
# تابع ذخیره لاگ در فایل
# =====================================================================================
def save_debug_log_to_file(symbol, debug_log_lines):
    """ذخیره Debug Log در یک فایل ثابت"""
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
# تابع تشخیص سیگنال — TRUE BAR-BY-BAR STATE MACHINE (Pine-exact)
# =====================================================================================
def detect_signal(df, state, symbol, debug=False):
    debug_log = []
    debug_file_lines = []
    def log(msg):
        debug_log.append(msg)
        debug_file_lines.append(msg)
        if debug:
            logger.info(msg)

    log(f"🔍 DTM — {symbol} | {format_iran_time()}")

    # ------------------------------------------------------------------
    # 1. Strict closed-candle only (Pine barstate.isconfirmed equivalent)
    # ------------------------------------------------------------------
    if API_RETURNS_OPEN_CANDLE:
        closed_df_indexed = df.iloc[:-1].copy()
        log(f"   API returns open candle — removed last row, using {len(closed_df_indexed)} closed candles")
    else:
        closed_df_indexed = df.copy()
        log(f"   API returns only closed candles — using all {len(closed_df_indexed)} candles")
    
    if len(closed_df_indexed) > 0:
        last_bar_start = closed_df_indexed.index[-1]
        if last_bar_start.tzinfo is None:
            last_bar_start = last_bar_start.tz_localize('UTC')
        last_bar_end = last_bar_start + pd.Timedelta(minutes=1)
        now_utc = pd.Timestamp.now(tz='UTC')
        if now_utc < last_bar_end:
            log(f"   ⏳ آخرین کندل ({last_bar_start}) هنوز کامل نشده — حذف شد")
            closed_df_indexed = closed_df_indexed.iloc[:-1].copy()
    
    if len(closed_df_indexed) > HISTORY_BARS:
        closed_df_indexed = closed_df_indexed.tail(HISTORY_BARS).copy()
        log(f"   ✂️ Sliced to last {HISTORY_BARS} bars (TV free-tier parity)")
    
    closed_df = closed_df_indexed.reset_index(drop=True)
    n = len(closed_df)
    if n < 33:
        log(f"❌ داده ناکافی: {n}")
        return None, None, None, None, False, None, None, None, []

    close = closed_df["close"]
    high = closed_df["high"]
    low = closed_df["low"]

    # ------------------------------------------------------------------
    # 2. Indicators (already Pine-exact)
    # ------------------------------------------------------------------
    rsi_val = calc_rsi(close, 14)
    macd_line, signal_line, hist_line = calc_macd(close, 12, 26, 9)
    atr14 = calc_atr(high, low, close, 14)
    pivot_high = find_pivot_high(high, LEFT_BARS, RIGHT_BARS)
    pivot_low = find_pivot_low(low, LEFT_BARS, RIGHT_BARS)

    # ------------------------------------------------------------------
    # 3. TRUE BAR-BY-BAR: only the newest confirmation candidate
    #    A pivot confirmed on bar i means real pivot is at i - RIGHT_BARS
    # ------------------------------------------------------------------
    last = n - 1
    real_pivot_candidate = last - RIGHT_BARS

    if real_pivot_candidate < LEFT_BARS:
        log(f"   ⏭️ Not enough bars for pivot confirmation")
        return None, None, None, None, False, None, None, None, []

    existing_high_ts = {p['ts'] for p in state.pivot_highs}
    existing_low_ts = {p['ts'] for p in state.pivot_lows}

    new_pivots_high = []
    new_pivots_low = []

    # Check whether a NEW pivot was confirmed exactly on this bar
    # (Pine: newPivotHigh = not na(pivotHighPrice))
    ts_candidate = closed_df_indexed.index[real_pivot_candidate]

    if not pd.isna(pivot_high.iloc[real_pivot_candidate]) and ts_candidate not in existing_high_ts:
        new_pivots_high.append({
            'ts': ts_candidate,
            'price': float(pivot_high.iloc[real_pivot_candidate]),
            'rsi': float(rsi_val.iloc[real_pivot_candidate]),
            'macdline': float(macd_line.iloc[real_pivot_candidate]),
            'hist': float(hist_line.iloc[real_pivot_candidate]),
            'bar': real_pivot_candidate
        })

    if not pd.isna(pivot_low.iloc[real_pivot_candidate]) and ts_candidate not in existing_low_ts:
        new_pivots_low.append({
            'ts': ts_candidate,
            'price': float(pivot_low.iloc[real_pivot_candidate]),
            'rsi': float(rsi_val.iloc[real_pivot_candidate]),
            'macdline': float(macd_line.iloc[real_pivot_candidate]),
            'hist': float(hist_line.iloc[real_pivot_candidate]),
            'bar': real_pivot_candidate
        })

    # Update state ONLY if a new pivot is confirmed on this bar
    # (exactly mirrors Pine's if newPivotHigh / if newPivotLow blocks)
    if new_pivots_high:
        state.pivot_highs.extend(new_pivots_high)
        if len(state.pivot_highs) > 500:
            state.pivot_highs = state.pivot_highs[-500:]
    if new_pivots_low:
        state.pivot_lows.extend(new_pivots_low)
        if len(state.pivot_lows) > 500:
            state.pivot_lows = state.pivot_lows[-500:]

    state.last_processed_ts = closed_df_indexed.index[last]

    log(f"   n={n}, last={last}, real_pivot_candidate={real_pivot_candidate}")
    log(f"   new_high={len(new_pivots_high)}, new_low={len(new_pivots_low)} | mem: H={len(state.pivot_highs)} L={len(state.pivot_lows)}")

    early_signal = len(new_pivots_high) > 0 or len(new_pivots_low) > 0
    entry_price = float(close.iloc[last])   # close of the confirmation bar (Pine process_orders_on_close)

    buy_signal = sell_signal = None
    buy_emoji = sell_emoji = None
    buy_label = sell_label = None
    buy_score = sell_score = 0
    buy_stop = buy_target = sell_stop = sell_target = None
    buy_details = sell_details = []

    # ------------------------------------------------------------------
    # 4. Score ONLY if we just confirmed a new pivot AND have a previous one
    #    All PA / ATR taken from the confirmation bar (last)
    # ------------------------------------------------------------------
    # BUY
    if len(new_pivots_low) > 0 and len(state.pivot_lows) >= 2:
        for new_pl in new_pivots_low:
            idx = next((i for i, p in enumerate(state.pivot_lows) if p['ts'] == new_pl['ts']), None)
            if idx is None or idx == 0:
                continue
            pl_1 = state.pivot_lows[idx - 1]
            pl_2 = state.pivot_lows[idx]
            bar1 = resolve_bar_from_ts(closed_df_indexed, pl_1['ts'])
            bar2 = resolve_bar_from_ts(closed_df_indexed, pl_2['ts'])

            if bar1 is None or bar2 is None:
                continue

            is_classic_buy = pl_2['price'] < pl_1['price']
            is_hidden_buy = pl_2['price'] > pl_1['price']

            if is_classic_buy or is_hidden_buy:
                should_score = False
                if is_classic_buy:
                    trend_ok = is_trending_down(close, bar1, TREND_LOOKBACK, TREND_SLOPE_MIN_PCT)
                    log(f"   🔵 Classic BUY: bar1={bar1}, bar2={bar2}, trend={'✅' if trend_ok else '❌'}")
                    should_score = trend_ok
                else:
                    log(f"   🔵 Hidden BUY: bar1={bar1}, bar2={bar2}, trend=⏭️ (skipped)")
                    should_score = True

                if should_score:
                    score, details = calculate_divergence_score(
                        pl_1, pl_2, "BUY", bar1, bar2, hist_line, high, low, closed_df_indexed, atr14
                    )
                    buy_emoji, buy_label = classify_signal(score)
                    if buy_emoji and score >= 3:
                        # ATR and PA already evaluated inside calculate_divergence_score
                        # on confirm_bar = bar2 + RIGHT_BARS which equals 'last'
                        atr_at_confirm = atr14.iloc[last]
                        stop, tp_raw, _ = compute_stop_and_targets(
                            state.pivot_highs, state.pivot_lows, "long", closed_df_indexed, atr_at_confirm
                        )
                        if stop and tp_raw:
                            buy_stop = stop
                            buy_target = resolve_final_target(entry_price, stop, tp_raw, "long")
                            buy_signal = "BUY"
                            buy_score = score
                            buy_details = details
                            log(f"   🔵 BUY score={score}/5 ✅ SIGNAL")
                            log(f"   Entry={entry_price:.4f}, SL={stop:.4f}, TP={buy_target:.4f}")
                            break

    # SELL
    if not buy_signal and len(new_pivots_high) > 0 and len(state.pivot_highs) >= 2:
        for new_ph in new_pivots_high:
            idx = next((i for i, p in enumerate(state.pivot_highs) if p['ts'] == new_ph['ts']), None)
            if idx is None or idx == 0:
                continue
            ph_1 = state.pivot_highs[idx - 1]
            ph_2 = state.pivot_highs[idx]
            bar1 = resolve_bar_from_ts(closed_df_indexed, ph_1['ts'])
            bar2 = resolve_bar_from_ts(closed_df_indexed, ph_2['ts'])

            if bar1 is None or bar2 is None:
                continue

            is_classic_sell = ph_2['price'] > ph_1['price']
            is_hidden_sell = ph_2['price'] < ph_1['price']

            if is_classic_sell or is_hidden_sell:
                should_score = False
                if is_classic_sell:
                    trend_ok = is_trending_up(close, bar1, TREND_LOOKBACK, TREND_SLOPE_MIN_PCT)
                    log(f"   🔴 Classic SELL: bar1={bar1}, bar2={bar2}, trend={'✅' if trend_ok else '❌'}")
                    should_score = trend_ok
                else:
                    log(f"   🔴 Hidden SELL: bar1={bar1}, bar2={bar2}, trend=⏭️ (skipped)")
                    should_score = True

                if should_score:
                    score, details = calculate_divergence_score(
                        ph_1, ph_2, "SELL", bar1, bar2, hist_line, high, low, closed_df_indexed, atr14
                    )
                    sell_emoji, sell_label = classify_signal(score)
                    if sell_emoji and score >= 3:
                        atr_at_confirm = atr14.iloc[last]
                        stop, tp_raw, _ = compute_stop_and_targets(
                            state.pivot_highs, state.pivot_lows, "short", closed_df_indexed, atr_at_confirm
                        )
                        if stop and tp_raw:
                            sell_stop = stop
                            sell_target = resolve_final_target(entry_price, stop, tp_raw, "short")
                            sell_signal = "SELL"
                            sell_score = score
                            sell_details = details
                            log(f"   🔴 SELL score={score}/5 ✅ SIGNAL")
                            log(f"   Entry={entry_price:.4f}, SL={stop:.4f}, TP={sell_target:.4f}")
                            break

    if not buy_signal and not sell_signal:
        log(f"   ⚪ No signal")

    # ------------------------------------------------------------------
    # DEBUG LOG (unchanged)
    # ------------------------------------------------------------------
    log("   " + "=" * 70)
    log("   🔬 FULL DEBUG LOG")
    log("   " + "=" * 70)
    
    log(f"   📊 GENERAL:")
    log(f"      Symbol: {symbol}")
    log(f"      Time: {format_iran_time()}")
    log(f"      Total Candles (n): {n}")
    log(f"      Last bar (confirmation candidate): {last}")
    log(f"      Real pivot candidate: {real_pivot_candidate}")
    log(f"      New Pivot Highs: {len(new_pivots_high)}")
    log(f"      New Pivot Lows: {len(new_pivots_low)}")
    log(f"      Total Pivot Highs in Memory: {len(state.pivot_highs)}")
    log(f"      Total Pivot Lows in Memory: {len(state.pivot_lows)}")
    log("")
    
    log(f"   🕯️ LAST 5 CANDLES (newest first):")
    for i in range(min(5, n)):
        idx = n - 1 - i
        if idx >= 0:
            c = closed_df.iloc[idx]
            body = abs(c['close'] - c['open'])
            upper_shadow = c['high'] - max(c['close'], c['open'])
            lower_shadow = min(c['close'], c['open']) - c['low']
            candle_type = "🟢" if c['close'] >= c['open'] else "🔴"
            ts = closed_df_indexed.index[idx]
            log(f"      [{idx}] {candle_type} {ts}")
            log(f"         O={c['open']:.4f} H={c['high']:.4f} L={c['low']:.4f} C={c['close']:.4f}")
            log(f"         Body={body:.4f} | UpperShadow={upper_shadow:.4f} | LowerShadow={lower_shadow:.4f}")
            log(f"         Volume={c['volume']:.2f}")
    log("")
    
    log(f"   📈 CURRENT INDICATORS:")
    log(f"      Entry Price (close of confirmation bar): {entry_price:.4f}")
    log(f"      RSI(14)[-1]: {rsi_val.iloc[-1]:.2f}")
    log(f"      MACD Line[-1]: {macd_line.iloc[-1]:.6f}")
    log(f"      MACD Hist[-1]: {hist_line.iloc[-1]:.6f}")
    log(f"      ATR(14)[-1]: {atr14.iloc[-1]:.4f}")
    log("")
    
    log(f"   🔺 LAST 3 PIVOT HIGHS (newest first):")
    for i, p in enumerate(reversed(state.pivot_highs[-3:])):
        bar = resolve_bar_from_ts(closed_df_indexed, p['ts'])
        log(f"      PH[{i}]: bar={bar}, ts={p['ts']}")
        log(f"         Price={p['price']:.4f}, RSI={p['rsi']:.2f}, MACDLine={p['macdline']:.6f}, Hist={p['hist']:.6f}")
    log("")
    
    log(f"   🔻 LAST 3 PIVOT LOWS (newest first):")
    for i, p in enumerate(reversed(state.pivot_lows[-3:])):
        bar = resolve_bar_from_ts(closed_df_indexed, p['ts'])
        log(f"      PL[{i}]: bar={bar}, ts={p['ts']}")
        log(f"         Price={p['price']:.4f}, RSI={p['rsi']:.2f}, MACDLine={p['macdline']:.6f}, Hist={p['hist']:.6f}")
    log("")
    
    if new_pivots_high or new_pivots_low:
        log(f"   🆕 NEW PIVOTS THIS CYCLE:")
        for p in new_pivots_high:
            log(f"      NEW PH: ts={p['ts']}, price={p['price']:.4f}")
        for p in new_pivots_low:
            log(f"      NEW PL: ts={p['ts']}, price={p['price']:.4f}")
        log("")
    
    log(f"   🏁 FINAL RESULT:")
    if buy_signal:
        log(f"      ✅ BUY SIGNAL GENERATED")
        log(f"      Entry={entry_price:.4f}, SL={buy_stop:.4f}, TP={buy_target:.4f}")
        log(f"      Score={buy_score}/5, Type={buy_label}")
    elif sell_signal:
        log(f"      ✅ SELL SIGNAL GENERATED")
        log(f"      Entry={entry_price:.4f}, SL={sell_stop:.4f}, TP={sell_target:.4f}")
        log(f"      Score={sell_score}/5, Type={sell_label}")
    else:
        log(f"      ❌ NO SIGNAL — conditions not met")
    
    log("   " + "=" * 70)

    save_debug_log_to_file(symbol, debug_file_lines)

    # Telegram log (unchanged)
    current_time = time.time()
    should_send = False
    if state.telegram_log_count < 5:
        if state.last_telegram_log_time == 0 or (current_time - state.last_telegram_log_time) >= 300:
            should_send = True
    else:
        if current_time - state.last_telegram_log_time >= 21600:
            should_send = True

    if should_send:
        state.last_telegram_log_time = current_time
        state.telegram_log_count += 1
        try:
            telegram_debug = "\n".join(debug_log)
            prefix = "🟢" if len(new_pivots_high)+len(new_pivots_low) > 0 else "ℹ️"
            send_telegram_message(
                f"{prefix} لاگ #{state.telegram_log_count} — {symbol} {HASHTAGS['log']}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"```\n{telegram_debug[:3000]}\n```\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🕒 {format_iran_time()}"
            )
        except Exception as e:
            logger.error(f"[TELEGRAM] {e}")

    if buy_signal:
        return "BUY", entry_price, buy_stop, buy_target, early_signal, buy_emoji, buy_label, buy_score, buy_details
    elif sell_signal:
        return "SELL", entry_price, sell_stop, sell_target, early_signal, sell_emoji, sell_label, sell_score, sell_details
    return None, None, None, None, early_signal, None, None, None, []

# =====================================================================================
# پیگیری سیگنال‌های باز
# =====================================================================================
def track_open_signals(exchange):
    history = load_history()
    open_trades_in_history = [t for t in history if t.get('result') is None]
    if not open_trades_in_history:
        return
    try:
        open_positions = exchange.fetch_open_positions()
    except Exception as e:
        logger.error(f"[TRACK] Could not fetch open positions: {e}")
        return
    for trade in open_trades_in_history:
        symbol = trade['symbol']
        direction = "LONG" if trade['direction'] == "BUY" else "SHORT"
        signal_time = trade['signal_time']
        matching_open_pos = None
        for pos in open_positions:
            if pos.get('symbol') == symbol and pos.get('side') == direction:
                matching_open_pos = pos
                break
        if matching_open_pos is None:
            try:
                now_utc = datetime.now(timezone.utc)
                today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0).strftime('%Y-%m-%dT%H:%M:%SZ')
                trades = exchange.fetch_trade_history(symbol=symbol, start_time=today_start)
                last_closed_trade = None
                for t in reversed(trades):
                    if t.get('symbol') == symbol:
                        last_closed_trade = t
                        break
                if last_closed_trade:
                    realized_pnl = float(last_closed_trade.get('realizedPnl', 0))
                    close_price = float(last_closed_trade.get('price', 0))
                    if realized_pnl > 0:
                        result = 'TAKE_PROFIT'
                        message = f"🎯 حد سود فعال شد {HASHTAGS['target']} #سیگنال_{trade.get('signal_number', '?')}\n\n🔹 {symbol} | {direction}\n💰 سود خالص: {realized_pnl:.2f} USDT\n🕒 {format_iran_time()}"
                    else:
                        result = 'STOP_LOSS'
                        message = f"💔 حد ضرر فعال شد {HASHTAGS['stop']} #سیگنال_{trade.get('signal_number', '?')}\n\n🔹 {symbol} | {direction}\n💸 ضرر خالص: {realized_pnl:.2f} USDT\n🕒 {format_iran_time()}"
                    update_trade_result(signal_time, result, close_price, format_iran_time(), pnl=realized_pnl)
                    send_telegram_message(message)
                    logger.info(f"[TRACK] Closed trade detected for {symbol} {direction}: {result}, PnL: {realized_pnl}")
                else:
                    logger.warning(f"[TRACK] Open position for {symbol} {direction} not found, but no matching history trade found either.")
            except Exception as e:
                logger.error(f"[TRACK] Error fetching trade history for {symbol}: {e}")

# =====================================================================================
# تابع اصلی
# =====================================================================================
def analyze_and_execute():
    logger.info("[ANALYZE] شروع...")
    exchange = TrueTradePrivateExchange(API_KEY, API_SECRET, BASE_URL)
    conn = exchange.test_connection()
    balance = exchange.fetch_balance() if conn else 0
    if balance is None:
        balance = 0

    if not hasattr(analyze_and_execute, "_last_status"):
        analyze_and_execute._last_status = conn
        status_text = "✅ متصل — ترید خودکار فعال است" if conn else "⚠️ قطع — ترید خودکار غیرفعال است"
        balance_text = f"\n💰 موجودی حساب فیوچرز: {balance:.2f} USDT" if balance else "\n💰 موجودی: نامشخص"
        send_telegram_message(f"📡 وضعیت اتصال به صرافی {HASHTAGS['connection']}\n\n{status_text}{balance_text}\n🕒 {format_iran_time()}")
    elif analyze_and_execute._last_status != conn:
        analyze_and_execute._last_status = conn
        status_text = "✅ متصل — ترید خودکار فعال شد" if conn else "⚠️ قطع — ترید خودکار متوقف شد"
        balance_text = f"\n💰 موجودی حساب فیوچرز: {balance:.2f} USDT" if balance else ""
        send_telegram_message(f"🔄 تغییر وضعیت صرافی {HASHTAGS['connection_change']}\n\n{status_text}{balance_text}\n🕒 {format_iran_time()}")

    data = TrueTradePublicData()
    track_open_signals(exchange)
    
    side_map = {"BUY": "LONG", "SELL": "SHORT"}
    leverage_map = {"LTCUSDT": 75, "DOGEUSDT": 75, "ETHUSDT": 50}

    for symbol in SYMBOLS:
        try:
            df = data.fetch_ohlcv(symbol, '1m', HISTORY_BARS)
            if df is None or df.empty:
                logger.warning(f"[SKIP] {symbol}")
                continue
            logger.info(f"[DATA] {symbol}: {len(df)} کندل")

            result = detect_signal(df, SYMBOL_STATES[symbol], symbol, debug=True)
            signal, entry, stop, target, early, emoji, label, score = result[:8]
            details = result[8] if len(result) > 8 else []
            cp = df['close'].iloc[-1]

            if early and not SYMBOL_STATES[symbol].alert_sent:
                SYMBOL_STATES[symbol].alert_sent = True
                send_telegram_message(f"⚡ Pivot جدید — {symbol} {HASHTAGS['pivot']}\n💰 {cp:.4f}\n⏳ \~۲ دقیقه تا تأیید\n🕒 {format_iran_time()}")

            if signal and stop and target:
                entry = round_price(entry, symbol)
                stop = round_price(stop, symbol)
                target = round_price(target, symbol)

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

                signal_message = (
                    f"{emoji} Signal {label} — {symbol} {HASHTAGS['signal']} #Signal_{signal_number}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"📊 Score: {score}/5\n"
                    f"🔸 {direction_emoji} Direction: {direction_text}\n\n"
                    f"📍 Entry: {entry:.{PRICE_PRECISION.get(symbol, 2)}f}\n"
                    f"🛑 Stop Loss: {stop:.{PRICE_PRECISION.get(symbol, 2)}f}\n"
                    f"🎯 Take Profit: {target:.{PRICE_PRECISION.get(symbol, 2)}f}\n\n"
                    f"📈 Potential Profit: +{profit_pct:.2f}%\n"
                    f"📉 Potential Loss: -{loss_pct:.2f}%\n"
                    f"⚖️ Risk/Reward Ratio: {rr:.2f}\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🕒 {format_iran_time()}"
                )
                try:
                    send_telegram_message(signal_message)
                except Exception as e:
                    logger.error(f"[TELEGRAM SIGNAL ERROR] {symbol}: {e}")
                    fallback_msg = (
                        f"{emoji} Signal {label} — {symbol} {HASHTAGS['signal']} #Signal_{signal_number}\n"
                        f"📍 Entry: {entry:.{PRICE_PRECISION.get(symbol, 2)}f}\n"
                        f"🛑 SL: {stop:.{PRICE_PRECISION.get(symbol, 2)}f} | 🎯 TP: {target:.{PRICE_PRECISION.get(symbol, 2)}f}\n"
                        f"🕒 {format_iran_time()}"
                    )
                    try:
                        send_telegram_message(fallback_msg)
                    except:
                        pass
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
                            'position_id': position_id
                        })
                        save_history(history)

                        order_message = (
                            f"✅ سفارش ثبت شد — {symbol} {HASHTAGS['signal']} #سیگنال_{signal_number}\n\n"
                            f"🔸 {side_map[signal]} | 💰 {capital:.2f} USDT | 🔧 {int(used_leverage)}x\n"
                        )
                        if capital_reduced:
                            order_message += (
                                f"⚠️ سرمایه کاهش یافت! {HASHTAGS['capital_reduced']}\n"
                                f"📐 لازم: {required_capital:.2f} | 💰 موجود: {balance:.2f}\n"
                                f"📉 ضرر: {TARGET_RISK:.2f} → {actual_risk:.2f} USDT\n"
                            )
                        order_message += (
                            f"🛑 {stop:.4f} | 🎯 {target:.4f}\n"
                            f"📉 ریسک: {actual_risk:.2f} USDT | 📈 سود بالقوه: {potential_profit:.2f} USDT\n"
                            f"🕒 {format_iran_time()}"
                        )
                        send_telegram_message(order_message)
                    except Exception as e:
                        send_telegram_message(f"❌ خطا — {symbol} {HASHTAGS['order_error']} #سیگنال_{signal_number}\n{side_map[signal]}\n📝 {str(e)[:200]}\n🕒 {format_iran_time()}")
                SYMBOL_STATES[symbol].alert_sent = False
            else:
                logger.info(f"[ANALYSIS] {symbol}: بدون سیگنال")
        except Exception as e:
            logger.error(f"[ERROR] {symbol}: {e}")
    
    save_states()

# =====================================================================================
# حلقه اصلی
# =====================================================================================
def main_loop():
    exchange = TrueTradePrivateExchange(API_KEY, API_SECRET, BASE_URL)
    last_daily_report_date = None
    last_monthly_report_date = None

    while True:
        try:
            logger.info(f"[LOOP] {format_iran_time()}")
            analyze_and_execute()
            
            today = format_iran_date()
            now = datetime.now(timezone(timedelta(hours=3, minutes=30)))
            
            if last_daily_report_date != today:
                try:
                    send_reports(exchange)
                    last_daily_report_date = today
                    last_monthly_report_date = now
                except Exception as e:
                    logger.error(f"[REPORT ERROR] {e}")
            
            if last_monthly_report_date is None or (now - last_monthly_report_date).days >= 30:
                 try:
                    send_reports(exchange)
                    last_monthly_report_date = now
                 except Exception as e:
                    logger.error(f"[REPORT ERROR] {e}")

            time.sleep(60)
        except Exception as e:
            logger.error(f"[LOOP] {e}")
            time.sleep(60)

# =====================================================================================
app = Flask(__name__)
@app.route("/")
def health():
    return "OK", 200

if __name__ == "__main__":
    logger.info("DTM Bot Starting...")
    
    load_signal_counter()
    load_states()

    hashtag_list = "\n".join([f"• {v} → {k}" for k, v in HASHTAGS.items()])
    send_telegram_message(
        f"🤖 DTM Pro — آنلاین {HASHTAGS['startup']}\n\n"
        f"🧠 DTM Divergence (Pine Script Mirror — Parity Final)\n"
        f"📊 سیگنال + ترید خودکار\n\n"
        f"⚙️ Pivot: 5/3 (Fast) | MTF: Off | R/R: 2.0 | Risk: 3.5 USDT\n"
        f"🔧 ETH=50x | LTC/DOGE=75x\n\n"
        f"📌 هشتگ‌های ثابت:\n{hashtag_list}\n\n"
        f"📊 شمارنده سیگنال: از #سیگنال_{SIGNAL_COUNTER + 1} شروع می‌شود\n\n"
        f"🕒 {format_iran_time()}"
    )
    run_startup_diagnostic()
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=10000), daemon=True).start()
    logger.info("[STARTUP] Flask روی پورت 10000")
    main_loop()
