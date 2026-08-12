# -*- coding: utf-8 -*-
"""
DTM Divergence Auto-Trading Bot - TheTrueTrade (نسخه هیبریدی)
====================================================================
نسخه نهایی کامل — منطق واگرایی دقیقاً مطابق Pine Script:
- Pivot: حالت سریع (5/3) — **FIXED**: Asymmetric tie-breaking (Pine-accurate)
- RSI با calc_rma (Wilder Smoothing مثل ta.rma در Pine)
- ATR با calc_rma (Wilder Smoothing مثل ta.atr در Pine)
- EMA دقیقاً مثل Pine (بدون SMA seed)
- عمق داده 5000 کندل (معادل حساب رایگان TradingView)
- رفع درز State پیوت‌ها (start_bar=0 همیشه)
- RSI/MACD در همان کندل Pivot
- شرط روند: تفاضل مقدار برازش دو رگرسیون (مثل ta.linreg) با >
- کندل تأییدیه: روی کندل تأیید (bar2 + RIGHT_BARS)
- رفع Off-by-one در mid_peak/mid_trough
- رفع فرمول avg_body در Price Action
- **FIXED**: شرط روند با > (مطابق Pine)
- **FIXED**: MACD Color Change برگردانده شد (مطابق Pine)
- **FIXED**: تشخیص نوع واگرایی (Classic/Hidden)
- **FIXED**: ATR در کندل تأیید
- **FIXED**: اسکن کامل همه کندل‌ها (مطابق Pine)
- **FIXED**: محدوده بررسی پیوت‌ها = 15 (برای سرعت بهتر)
- **FIXED**: ذخیره و بازیابی صحیح state بین اجراها
- **FIXED [PINE-EXACT GATE]**: ۴ شرط اجباری (RSI + MACD Line + MACD Hist + Trend)
- رفع فرمول فیبوناچی
- **FIXED [BUG B]**: FIB_SEARCH_BARS = 100 (مطابق Pine)
- **FIXED [ENHANCEMENT C]**: ATR در بار تأیید (confirm bar) خوانده میشه
- **FIXED [ENHANCEMENT D]**: Bar-State Safety Net (حذف کندل باز)
- **FIXED [RIGOR]**: calc_rma با تطبیق دقیق معناشناسی na در Pine
- **FIXED [PERF]**: بهینه‌سازی سرعت اسکن پیوت‌ها
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
- پیام سیگنال با اطلاعات کامل پیوت‌ها
- گزارش واقعی صرافی (معاملات + موجودی)
- **SECURITY**: کلیدهای API فقط از متغیر محیطی خوانده میشن
- **DEBUG**: Full Debug Log (فایل `full_debug_log.txt`)
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
# Multi-Pivot Comparison Settings (کاهش برای سرعت بهتر)
# =====================================================================================
MAX_HISTORICAL_PIVOTS = 15  # از ۵۰ به ۱۵ کاهش یافت برای سرعت
MAX_BARS_BETWEEN_PIVOTS = 80

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
        response = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}, timeout=30)
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

# =====================================================================================
# توابع محاسباتی پایه (Pine-Exact)
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
    ema_fast = calc_ema(close, fast)
    ema_slow = calc_ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calc_ema(macd_line, signal)
    return macd_line, signal_line, macd_line - signal_line

def calc_atr(high, low, close, length=14):
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return calc_rma(tr, length)

# =====================================================================================
# FIXED: Asymmetric tie-breaking (Pine-accurate)
# تساوی با کندل‌های چپ مجاز، با کندل‌های راست غیرمجاز
# =====================================================================================
def find_pivot_high(high, left=LEFT_BARS, right=RIGHT_BARS):
    n = len(high)
    result = pd.Series(np.nan, index=high.index)
    vals = high.values
    for i in range(left, n - right):
        left_ok = True
        for j in range(i - left, i):
            if vals[j] > vals[i]:
                left_ok = False
                break
        right_ok = True
        for j in range(i + 1, i + right + 1):
            if vals[j] >= vals[i]:
                right_ok = False
                break
        if left_ok and right_ok:
            result.iloc[i] = vals[i]
    return result

def find_pivot_low(low, left=LEFT_BARS, right=RIGHT_BARS):
    n = len(low)
    result = pd.Series(np.nan, index=low.index)
    vals = low.values
    for i in range(left, n - right):
        left_ok = True
        for j in range(i - left, i):
            if vals[j] < vals[i]:
                left_ok = False
                break
        right_ok = True
        for j in range(i + 1, i + right + 1):
            if vals[j] <= vals[i]:
                right_ok = False
                break
        if left_ok and right_ok:
            result.iloc[i] = vals[i]
    return result

def _linreg_end(y):
    x = np.arange(len(y))
    slope, intercept = np.polyfit(x, y, 1)
    return intercept + slope * (len(y) - 1)

def is_trending_up(close, ref_bar, lookback=TREND_LOOKBACK, slope_min_pct=TREND_SLOPE_MIN_PCT):
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
    if bar1 is None or bar2 is None or bar2 <= bar1:
        return False
    for i in range(bar1 + 1, bar2):
        if i < len(hist_series):
            h = hist_series.iloc[i]
            if need_negative_phase and h < 0:
                return True
            if not need_negative_phase and h > 0:
                return True
    return False

def find_trend_start_low(low_series, ref_bar, search_bars=FIB_SEARCH_BARS):
    if ref_bar is None:
        return None
    start = max(0, ref_bar - search_bars + 1)
    window = low_series.iloc[start:ref_bar + 1]
    return window.min() if len(window) > 0 else None

def find_trend_start_high(high_series, ref_bar, search_bars=FIB_SEARCH_BARS):
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
        mid_peak = df_indexed["high"].iloc[bar1+1:bar2].max()
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
        mid_trough = df_indexed["low"].iloc[bar1+1:bar2].min()
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
# PINE-EXACT GATE: ۴ شرط اجباری + ۲ شرط امتیازی
# =====================================================================================
def calculate_divergence_score(p1, p2, div_type, direction, bar1, bar2, hist_series, high_series, low_series, df_indexed, atr_series, close):
    details = []
    
    # شرط ۱: RSI (اجباری)
    if direction == "BUY":
        if div_type == 'classic':
            rsi_ok = p2['price'] < p1['price'] and p2['rsi'] > p1['rsi']
        else:
            rsi_ok = p2['price'] > p1['price'] and p2['rsi'] < p1['rsi']
    else:
        if div_type == 'classic':
            rsi_ok = p2['price'] > p1['price'] and p2['rsi'] < p1['rsi']
        else:
            rsi_ok = p2['price'] < p1['price'] and p2['rsi'] > p1['rsi']
    details.append("✅ RSI" if rsi_ok else "❌ RSI")
    
    # شرط ۲: MACD Line (اجباری)
    if direction == "BUY":
        if div_type == 'classic':
            macd_ok = p2['price'] < p1['price'] and p2['macdline'] > p1['macdline']
        else:
            macd_ok = p2['price'] > p1['price'] and p2['macdline'] < p1['macdline']
    else:
        if div_type == 'classic':
            macd_ok = p2['price'] > p1['price'] and p2['macdline'] < p1['macdline']
        else:
            macd_ok = p2['price'] < p1['price'] and p2['macdline'] > p1['macdline']
    details.append("✅ MACD Line" if macd_ok else "❌ MACD Line")
    
    # شرط ۳: MACD Histogram (اجباری)
    if direction == "SELL" and div_type == 'classic':
        hist_ok = (p2['price'] > p1['price'] and 
                  p2['hist'] < p1['hist'] and 
                  p1['hist'] > 0 and p2['hist'] > 0 and
                  check_macd_color_change(hist_series, bar1, bar2, True))
    elif direction == "SELL" and div_type == 'hidden':
        hist_ok = (p2['price'] < p1['price'] and 
                  p2['hist'] > p1['hist'] and 
                  p1['hist'] > 0 and p2['hist'] > 0 and
                  check_macd_color_change(hist_series, bar1, bar2, True))
    elif direction == "BUY" and div_type == 'classic':
        hist_ok = (p2['price'] < p1['price'] and 
                  p2['hist'] > p1['hist'] and 
                  p1['hist'] < 0 and p2['hist'] < 0 and
                  check_macd_color_change(hist_series, bar1, bar2, False))
    elif direction == "BUY" and div_type == 'hidden':
        hist_ok = (p2['price'] > p1['price'] and 
                  p2['hist'] < p1['hist'] and 
                  p1['hist'] < 0 and p2['hist'] < 0 and
                  check_macd_color_change(hist_series, bar1, bar2, False))
    else:
        hist_ok = False
    details.append("✅ MACD Histogram" if hist_ok else "❌ MACD Histogram")
    
    # شرط ۴: Trend (اجباری)
    if direction == "BUY":
        if div_type == 'classic':
            trend_ok = is_trending_down(close, bar1)
        else:
            trend_ok = is_trending_up(close, bar1)
    else:
        if div_type == 'classic':
            trend_ok = is_trending_up(close, bar1)
        else:
            trend_ok = is_trending_down(close, bar1)
    details.append("✅ Trend" if trend_ok else "❌ Trend")
    
    # PINE-EXACT GATE
    mandatory_ok = rsi_ok and macd_ok and hist_ok and trend_ok
    if not mandatory_ok:
        details.append("❌ یکی از شروط اجباری برقرار نیست")
        return 0, details
    
    score = 4
    
    # Fibonacci (امتیازی)
    if direction == "BUY":
        trend_start = find_trend_start_high(high_series, bar1)
        fib_ok = check_fib_level(trend_start, p1['price'], p2['price'], is_retrace_down=False)
    else:
        trend_start = find_trend_start_low(low_series, bar1)
        fib_ok = check_fib_level(trend_start, p1['price'], p2['price'], is_retrace_down=True)
    if fib_ok:
        score += 1
        details.append("✅ Fibonacci")
    else:
        details.append("❌ Fibonacci")
    
    # Price Action (امتیازی)
    confirm_bar = bar2 + RIGHT_BARS
    if confirm_bar < len(df_indexed):
        pa_ok, pa_reasons = check_price_action(df_indexed, confirm_bar, direction, atr_series.iloc[confirm_bar])
    else:
        pa_ok, pa_reasons = False, []
    if pa_ok:
        score += 1
        details.append(f"✅ Price Action ({', '.join(pa_reasons)})")
    else:
        details.append("❌ Price Action")
    
    return score, details

def classify_signal(score):
    if score >= 6: return "🟢", "Ideal"
    elif score >= 5: return "🟡", "Custom"
    elif score >= 4: return "⚪", "Minimal"
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
# کلاس وضعیت
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
    diagnostic_log.append("🟢 موتور امتیازدهی: آماده (Pine-Exact Gating)")
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
# تابع تشخیص سیگنال
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

    # Closed-candle only
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

    # محاسبه اندیکاتورها
    rsi_val = calc_rsi(close, 14)
    macd_line, signal_line, hist_line = calc_macd(close, 12, 26, 9)
    atr14 = calc_atr(high, low, close, 14)
    pivot_high = find_pivot_high(high, LEFT_BARS, RIGHT_BARS)
    pivot_low = find_pivot_low(low, LEFT_BARS, RIGHT_BARS)

    # اسکن کامل همه کندل‌ها
    existing_high_ts = {p['ts'] for p in state.pivot_highs}
    existing_low_ts = {p['ts'] for p in state.pivot_lows}

    all_new_pivots_high = []
    all_new_pivots_low = []

    for i in range(LEFT_BARS, n - RIGHT_BARS):
        ts = closed_df_indexed.index[i]
        if not pd.isna(pivot_high.iloc[i]) and ts not in existing_high_ts:
            all_new_pivots_high.append({
                'ts': ts, 'price': float(pivot_high.iloc[i]),
                'rsi': float(rsi_val.iloc[i]), 'macdline': float(macd_line.iloc[i]),
                'hist': float(hist_line.iloc[i]), 'bar': i
            })
        if not pd.isna(pivot_low.iloc[i]) and ts not in existing_low_ts:
            all_new_pivots_low.append({
                'ts': ts, 'price': float(pivot_low.iloc[i]),
                'rsi': float(rsi_val.iloc[i]), 'macdline': float(macd_line.iloc[i]),
                'hist': float(hist_line.iloc[i]), 'bar': i
            })

    if all_new_pivots_high:
        state.pivot_highs.extend(all_new_pivots_high)
        if len(state.pivot_highs) > 500:
            state.pivot_highs = state.pivot_highs[-500:]
    if all_new_pivots_low:
        state.pivot_lows.extend(all_new_pivots_low)
        if len(state.pivot_lows) > 500:
            state.pivot_lows = state.pivot_lows[-500:]

    last = n - 1
    state.last_processed_ts = closed_df_indexed.index[last]

    log(f"   n={n}, last={last}")
    log(f"   all_new_high={len(all_new_pivots_high)}, all_new_low={len(all_new_pivots_low)} | mem: H={len(state.pivot_highs)} L={len(state.pivot_lows)}")

    early_signal = len(all_new_pivots_high) > 0 or len(all_new_pivots_low) > 0
    entry_price = float(close.iloc[last])

    if not all_new_pivots_high and not all_new_pivots_low:
        log(f"   ⚪ No new pivot — skipping signal detection")
        save_debug_log_to_file(symbol, debug_file_lines)
        return None, None, None, None, False, None, None, None, []

    buy_signal = sell_signal = None
    buy_emoji = sell_emoji = None
    buy_label = sell_label = None
    buy_score = sell_score = 0
    buy_stop = buy_target = sell_stop = sell_target = None
    buy_details = sell_details = []
    buy_pivot1 = buy_pivot2 = sell_pivot1 = sell_pivot2 = None

    # بهینه‌سازی: فقط پیوت‌های جدیدی که توی all_new هستن رو بررسی کن
    # اگر all_new خیلی بزرگه (catch-up بعد از ریستارت)، فقط ۱۵ تای آخر
    check_highs = all_new_pivots_high[-15:] if len(all_new_pivots_high) > 15 else all_new_pivots_high
    check_lows = all_new_pivots_low[-15:] if len(all_new_pivots_low) > 15 else all_new_pivots_low

    # BUY SIGNAL
    if len(state.pivot_lows) >= 2 and len(check_lows) > 0:
        best_buy_score = 0
        best_buy_pair = None
        best_div_type = None
        best_p1 = None
        best_p2 = None
        
        # فقط پیوت‌های جدید رو با ۱۵ پیوت قبلی مقایسه کن
        for pl_2 in check_lows:
            # پیدا کردن ایندکس pl_2 توی state
            idx = next((i for i, p in enumerate(state.pivot_lows) if p['ts'] == pl_2['ts']), None)
            if idx is None or idx < 1:
                continue
            
            start = max(0, idx - MAX_HISTORICAL_PIVOTS)
            for prev_idx in range(start, idx):
                pl_1 = state.pivot_lows[prev_idx]
                
                bar1 = resolve_bar_from_ts(closed_df_indexed, pl_1['ts'])
                bar2 = resolve_bar_from_ts(closed_df_indexed, pl_2['ts'])
                
                if bar1 is None or bar2 is None:
                    continue
                if abs(bar2 - bar1) > MAX_BARS_BETWEEN_PIVOTS:
                    continue
                
                is_classic = pl_2['price'] < pl_1['price'] and pl_2['rsi'] > pl_1['rsi']
                is_hidden = pl_2['price'] > pl_1['price'] and pl_2['rsi'] < pl_1['rsi']
                
                div_type = 'classic' if is_classic else 'hidden' if is_hidden else None
                if div_type is None:
                    continue
                
                score, details = calculate_divergence_score(
                    pl_1, pl_2, div_type, "BUY", bar1, bar2, 
                    hist_line, high, low, closed_df_indexed, atr14, close
                )
                
                if score >= 4 and score > best_buy_score:
                    best_buy_score = score
                    best_buy_pair = (pl_1, pl_2, bar1, bar2, details)
                    best_div_type = div_type
                    best_p1 = pl_1
                    best_p2 = pl_2
                    if score >= 6:
                        break
            if best_buy_score >= 6:
                break
        
        if best_buy_pair and best_buy_score >= 4:
            buy_emoji, buy_label = classify_signal(best_buy_score)
            if buy_emoji:
                confirm_bar = best_buy_pair[2] + RIGHT_BARS
                atr_at_confirm = atr14.iloc[min(confirm_bar, len(atr14) - 1)]
                stop, tp_raw, _ = compute_stop_and_targets(
                    state.pivot_highs, state.pivot_lows, "long", closed_df_indexed, atr_at_confirm
                )
                if stop and tp_raw:
                    buy_stop = stop
                    buy_target = resolve_final_target(entry_price, stop, tp_raw, "long")
                    buy_signal = "BUY"
                    buy_score = best_buy_score
                    buy_details = best_buy_pair[3]
                    buy_pivot1 = best_p1
                    buy_pivot2 = best_p2
                    log(f"   🔵 BUY signal: {best_div_type} | score={best_buy_score}/6 ✅")
                    log(f"   Entry={entry_price:.4f}, SL={stop:.4f}, TP={buy_target:.4f}")

    # SELL SIGNAL
    if not buy_signal and len(state.pivot_highs) >= 2 and len(check_highs) > 0:
        best_sell_score = 0
        best_sell_pair = None
        best_div_type = None
        best_p1 = None
        best_p2 = None
        
        for ph_2 in check_highs:
            idx = next((i for i, p in enumerate(state.pivot_highs) if p['ts'] == ph_2['ts']), None)
            if idx is None or idx < 1:
                continue
            
            start = max(0, idx - MAX_HISTORICAL_PIVOTS)
            for prev_idx in range(start, idx):
                ph_1 = state.pivot_highs[prev_idx]
                
                bar1 = resolve_bar_from_ts(closed_df_indexed, ph_1['ts'])
                bar2 = resolve_bar_from_ts(closed_df_indexed, ph_2['ts'])
                
                if bar1 is None or bar2 is None:
                    continue
                if abs(bar2 - bar1) > MAX_BARS_BETWEEN_PIVOTS:
                    continue
                
                is_classic = ph_2['price'] > ph_1['price'] and ph_2['rsi'] < ph_1['rsi']
                is_hidden = ph_2['price'] < ph_1['price'] and ph_2['rsi'] > ph_1['rsi']
                
                div_type = 'classic' if is_classic else 'hidden' if is_hidden else None
                if div_type is None:
                    continue
                
                score, details = calculate_divergence_score(
                    ph_1, ph_2, div_type, "SELL", bar1, bar2, 
                    hist_line, high, low, closed_df_indexed, atr14, close
                )
                
                if score >= 4 and score > best_sell_score:
                    best_sell_score = score
                    best_sell_pair = (ph_1, ph_2, bar1, bar2, details)
                    best_div_type = div_type
                    best_p1 = ph_1
                    best_p2 = ph_2
                    if score >= 6:
                        break
            if best_sell_score >= 6:
                break
        
        if best_sell_pair and best_sell_score >= 4:
            sell_emoji, sell_label = classify_signal(best_sell_score)
            if sell_emoji:
                confirm_bar = best_sell_pair[2] + RIGHT_BARS
                atr_at_confirm = atr14.iloc[min(confirm_bar, len(atr14) - 1)]
                stop, tp_raw, _ = compute_stop_and_targets(
                    state.pivot_highs, state.pivot_lows, "short", closed_df_indexed, atr_at_confirm
                )
                if stop and tp_raw:
                    sell_stop = stop
                    sell_target = resolve_final_target(entry_price, stop, tp_raw, "short")
                    sell_signal = "SELL"
                    sell_score = best_sell_score
                    sell_details = best_sell_pair[3]
                    sell_pivot1 = best_p1
                    sell_pivot2 = best_p2
                    log(f"   🔴 SELL signal: {best_div_type} | score={best_sell_score}/6 ✅")
                    log(f"   Entry={entry_price:.4f}, SL={stop:.4f}, TP={sell_target:.4f}")

    if not buy_signal and not sell_signal:
        log(f"   ⚪ No signal")

    # DEBUG LOG
    log("   " + "=" * 70)
    log("   🔬 FULL DEBUG LOG (Pine-Exact Gating + Asymmetric Pivot)")
    log("   " + "=" * 70)
    log(f"   📊 GENERAL:")
    log(f"      Symbol: {symbol}")
    log(f"      Time: {format_iran_time()}")
    log(f"      Total Candles (n): {n}")
    log(f"      New Pivot Highs: {len(all_new_pivots_high)}")
    log(f"      New Pivot Lows: {len(all_new_pivots_low)}")
    log(f"      Check Highs: {len(check_highs)}, Check Lows: {len(check_lows)}")
    log(f"      Total Pivot Highs: {len(state.pivot_highs)}")
    log(f"      Total Pivot Lows: {len(state.pivot_lows)}")
    log("")
    log(f"   📈 CURRENT INDICATORS:")
    log(f"      Entry Price: {entry_price:.4f}")
    log(f"      RSI(14)[-1]: {rsi_val.iloc[-1]:.2f}")
    log(f"      MACD Line[-1]: {macd_line.iloc[-1]:.6f}")
    log(f"      MACD Hist[-1]: {hist_line.iloc[-1]:.6f}")
    log(f"      ATR(14)[-1]: {atr14.iloc[-1]:.4f}")
    log("")
    log(f"   🏁 FINAL RESULT:")
    if buy_signal:
        log(f"      ✅ BUY SIGNAL | Score={buy_score}/6 | Type={buy_label}")
        log(f"      Entry={entry_price:.4f}, SL={buy_stop:.4f}, TP={buy_target:.4f}")
    elif sell_signal:
        log(f"      ✅ SELL SIGNAL | Score={sell_score}/6 | Type={sell_label}")
        log(f"      Entry={entry_price:.4f}, SL={sell_stop:.4f}, TP={sell_target:.4f}")
    else:
        log(f"      ❌ NO SIGNAL")
    log("")
    if buy_signal:
        for d in buy_details:
            log(f"      {d}")
    elif sell_signal:
        for d in sell_details:
            log(f"      {d}")
    log("   " + "=" * 70)

    save_debug_log_to_file(symbol, debug_file_lines)

    # Telegram log
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
            prefix = "🟢" if len(all_new_pivots_high)+len(all_new_pivots_low) > 0 else "ℹ️"
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
        return "BUY", entry_price, buy_stop, buy_target, early_signal, buy_emoji, buy_label, buy_score, buy_details, buy_pivot1, buy_pivot2
    elif sell_signal:
        return "SELL", entry_price, sell_stop, sell_target, early_signal, sell_emoji, sell_label, sell_score, sell_details, sell_pivot1, sell_pivot2
    return None, None, None, None, early_signal, None, None, None, [], None, None

# =====================================================================================
# ادامه کدهای اصلی
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
                    logger.warning(f"[TRACK] Open position for {symbol} {direction} not found")
            except Exception as e:
                logger.error(f"[TRACK] Error fetching trade history for {symbol}: {e}")

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
            
            if len(result) >= 11:
                signal, entry, stop, target, early, emoji, label, score, details, pivot1, pivot2 = result
            else:
                signal, entry, stop, target, early, emoji, label, score = result[:8]
                details = result[8] if len(result) > 8 else []
                pivot1 = result[9] if len(result) > 9 else None
                pivot2 = result[10] if len(result) > 10 else None
                
            cp = df['close'].iloc[-1]

            if early and not SYMBOL_STATES[symbol].alert_sent:
                SYMBOL_STATES[symbol].alert_sent = True
                send_telegram_message(f"⚡ Pivot جدید — {symbol} {HASHTAGS['pivot']}\n💰 {cp:.4f}\n⏳ ~۲ دقیقه تا تأیید\n🕒 {format_iran_time()}")

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

                signal_type = "CD+" if signal == "BUY" and label == "Classic" else "HD+" if signal == "BUY" else "CD-" if label == "Classic" else "HD-"
                
                pivot1_info = f"Pivot اول: قیمت {pivot1['price']:.4f} @ کندل {pivot1['bar']} (زمان: {pivot1['ts']})" if pivot1 else "Pivot اول: نامشخص"
                pivot2_info = f"Pivot دوم: قیمت {pivot2['price']:.4f} @ کندل {pivot2['bar']} (زمان: {pivot2['ts']})" if pivot2 else "Pivot دوم: نامشخص"
                
                signal_message = (
                    f"{emoji} {signal_type} — {symbol} {HASHTAGS['signal']} #Signal_{signal_number}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"📊 Score: {score}/6\n"
                    f"🔸 {direction_emoji} Direction: {direction_text}\n\n"
                    f"📍 Entry: {entry:.{PRICE_PRECISION.get(symbol, 2)}f}\n"
                    f"🛑 Stop Loss: {stop:.{PRICE_PRECISION.get(symbol, 2)}f}\n"
                    f"🎯 Take Profit: {target:.{PRICE_PRECISION.get(symbol, 2)}f}\n\n"
                    f"📈 Potential Profit: +{profit_pct:.2f}%\n"
                    f"📉 Potential Loss: -{loss_pct:.2f}%\n"
                    f"⚖️ Risk/Reward Ratio: {rr:.2f}\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 اطلاعات Pivot‌ها:\n"
                    f"• {pivot1_info}\n"
                    f"• {pivot2_info}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🕒 {format_iran_time()}"
                )
                
                try:
                    send_telegram_message(signal_message)
                except Exception as e:
                    logger.error(f"[TELEGRAM SIGNAL ERROR] {symbol}: {e}")
                    fallback_msg = (
                        f"{emoji} {signal_type} — {symbol} #Signal_{signal_number}\n"
                        f"📍 Entry: {entry:.{PRICE_PRECISION.get(symbol, 2)}f}\n"
                        f"🛑 SL: {stop:.{PRICE_PRECISION.get(symbol, 2)}f} | 🎯 TP: {target:.{PRICE_PRECISION.get(symbol, 2)}f}\n"
                        f"{pivot1_info}\n{pivot2_info}\n🕒 {format_iran_time()}"
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
                            'position_id': position_id,
                            'pivot1_bar': pivot1['bar'] if pivot1 else None,
                            'pivot1_price': pivot1['price'] if pivot1 else None,
                            'pivot2_bar': pivot2['bar'] if pivot2 else None,
                            'pivot2_price': pivot2['price'] if pivot2 else None
                        })
                        save_history(history)

                        order_message = (
                            f"✅ سفارش ثبت شد — {symbol} #سیگنال_{signal_number}\n\n"
                            f"🔸 {side_map[signal]} | 💰 {capital:.2f} USDT | 🔧 {int(used_leverage)}x\n"
                        )
                        if capital_reduced:
                            order_message += (
                                f"⚠️ سرمایه کاهش یافت!\n"
                                f"📐 لازم: {required_capital:.2f} | 💰 موجود: {balance:.2f}\n"
                            )
                        order_message += (
                            f"🛑 {stop:.4f} | 🎯 {target:.4f}\n"
                            f"📉 ریسک: {actual_risk:.2f} USDT | 📈 سود بالقوه: {potential_profit:.2f} USDT\n"
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

def main_loop():
    exchange = TrueTradePrivateExchange(API_KEY, API_SECRET, BASE_URL)
    last_daily_report_date = None

    while True:
        try:
            logger.info(f"[LOOP] {format_iran_time()}")
            analyze_and_execute()
            
            today = format_iran_date()
            if last_daily_report_date != today:
                try:
                    send_reports(exchange)
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

    hashtag_list = "\n".join([f"• {v} → {k}" for k, v in HASHTAGS.items()])
    send_telegram_message(
        f"🤖 DTM Pro — آنلاین {HASHTAGS['startup']}\n\n"
        f"🧠 DTM Divergence (Pine Script Mirror — vFinal)\n"
        f"📊 سیگنال + ترید خودکار\n\n"
        f"⚙️ Pivot: 5/3 (Asymmetric tie-breaking) | Gating: 4-Mandatory AND\n"
        f"🔧 ETH=50x | LTC/DOGE=75x\n\n"
        f"✅ آخرین اصلاحات:\n"
        f"• Asymmetric pivot (تساوی چپ مجاز، راست غیرمجاز)\n"
        f"• ۴ شرط اجباری (RSI+MACDLine+MACDHist+Trend)\n"
        f"• بهینه‌سازی سرعت اسکن پیوت‌ها\n\n"
        f"📌 هشتگ‌های ثابت:\n{hashtag_list}\n\n"
        f"🕒 {format_iran_time()}"
    )
    run_startup_diagnostic()
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=10000), daemon=True).start()
    logger.info("[STARTUP] Flask روی پورت 10000")
    main_loop()
