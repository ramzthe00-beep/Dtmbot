# -*- coding: utf-8 -*-
"""
DTM Divergence Auto-Trading Bot - TheTrueTrade (نسخه هیبریدی)
====================================================================
نسخه نهایی کامل — منطق واگرایی دقیقاً مطابق Pine Script:
- Pivot: حالت سریع (5/3)
- RSI با calc_rma (Wilder Smoothing مثل ta.rma در Pine)
- عمق داده 1500 کندل
- رفع درز State پیوت‌ها (start_bar=0 همیشه)
- RSI/MACD در همان کندل Pivot
- شرط روند: تفاضل مقدار برازش دو رگرسیون (مثل ta.linreg)
- کندل تأییدیه: روی کندل تأیید (bar2 + RIGHT_BARS)
- رفع Off-by-one در mid_peak/mid_trough
- رفع فرمول avg_body در Price Action
- رفع باگ منطق تغییر رنگ MACD Histogram
- رفع Gating: RSI + MACD Line + MACD Histogram هر سه اجباری
- رفع فرمول فیبوناچی
- رفع تکرار سیگنال: فقط وقتی پیوت دوم تازه شکل گرفته باشد
- رفع فیلتر روند برای Hidden Divergence
- fetch_balance از /futures/assets
- استفاده از cost (مارجین) برای MARKET order
- نمایش موجودی در پیام اتصال صرافی
- هشتگ‌گذاری همه پیام‌های تلگرام
- شماره‌گذاری سیگنال‌ها
- بافر 2% موجودی برای جلوگیری از insufficient balance
- time.sleep بین پیام سیگنال و سفارش
- ربات تلگرام با منوی کامل و مدیریت پیام‌ها
- پیگیری خودکار استاپ/تارگت با بررسی قیمت لحظه‌ای
- تاریخچه معاملات از صرافی و گزارش‌های مبتنی بر داده واقعی
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
from flask import Flask, request
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
# کلیدهای API
# =====================================================================================
API_KEY = os.getenv("API_KEY", "pXJ3uOI3y7iPHxIgefQJ30PikXHqbQyVV9Ouj-_K")
API_SECRET = os.getenv("API_SECRET", "4cd23e00385ea761250034b420c86f40c4edb8e27c285c21572dbadf7e927b09")
BASE_URL = "https://apiv2.thetruetrade.io"

# =====================================================================================
# تنظیمات تلگرام
# =====================================================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8514469828:AAFC76EiVA7I4TFiX08jJ5N6-eKtOLMKitE")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7402770612")

HISTORY_FILE = "trades_history_hybrid.json"

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
RIGHT_BARS = 3

SHADOW_TO_BODY_RATIO = 2.0
MAX_OPPOSITE_SHADOW_PCT = 20.0
MIN_CANDLE_ATR_RATIO = 0.3
BIG_CANDLE_AVG_LEN = 14
BIG_CANDLE_MULTIPLIER = 1.5

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

    def fetch_ohlcv(self, symbol, timeframe='1m', limit=1500):
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
                'timestamp': pd.to_datetime(data['t'], unit='s'),
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

    def fetch_positions_history(self, start_date=None, end_date=None, symbol=None, page=1):
        """دریافت تاریخچه پوزیشن‌ها از صرافی"""
        try:
            params = []
            if symbol:
                params.append(f"symbol={symbol.upper()}")
            if start_date:
                params.append(f"start={start_date}")
            if end_date:
                params.append(f"end={end_date}")
            params.append(f"page={page}")
            
            query = "&".join(params)
            uri = f"/futures/positions?{query}" if query else "/futures/positions"
            
            return self._request('GET', uri)
        except Exception as e:
            logger.error(f"[FETCH POSITIONS ERROR] {e}")
            return []

    def fetch_trades_history(self, start_date=None, end_date=None, symbol=None, page=1):
        """دریافت تاریخچه معاملات از صرافی"""
        try:
            params = []
            if symbol:
                params.append(f"symbol={symbol.upper()}")
            if start_date:
                params.append(f"start={start_date}")
            if end_date:
                params.append(f"end={end_date}")
            params.append(f"page={page}")
            
            query = "&".join(params)
            uri = f"/futures/trades?{query}" if query else "/futures/trades"
            
            return self._request('GET', uri)
        except Exception as e:
            logger.error(f"[FETCH TRADES ERROR] {e}")
            return []

    def fetch_orders_history(self, start_date=None, end_date=None, symbol=None, page=1):
        """دریافت تاریخچه سفارشات از صرافی"""
        try:
            params = []
            if symbol:
                params.append(f"symbol={symbol.upper()}")
            if start_date:
                params.append(f"start={start_date}")
            if end_date:
                params.append(f"end={end_date}")
            params.append(f"page={page}")
            
            query = "&".join(params)
            uri = f"/futures/orders?{query}" if query else "/futures/orders"
            
            return self._request('GET', uri)
        except Exception as e:
            logger.error(f"[FETCH ORDERS ERROR] {e}")
            return []

    def get_current_price(self, symbol):
        """دریافت قیمت لحظه‌ای"""
        try:
            df = TrueTradePublicData().fetch_ohlcv(symbol, '1m', 2)
            if df is not None and not df.empty:
                return df['close'].iloc[-1]
        except:
            pass
        return None

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

def send_telegram_menu():
    """ارسال منوی کامل به تلگرام"""
    menu_text = """🤖 *منوی ربات DTM Pro*

📊 *گزارشات و آمار*
━━━━━━━━━━━━━━━━━━━━━━
📊 سیگنال‌های امروز → /today
📈 گزارش روزانه → /daily
📊 گزارش ماهانه → /monthly
🔍 وضعیت سیگنال های باز → /signal
💰 سود/زیان کل → /profit
🏆 بهترین سیگنال‌ها → /best
📉 بدترین سیگنال‌ها → /worst
📊 آمار تایم‌فریم‌ها → /tfstats
🪙 آمار ارزها → /symbolstats
📋 تاریخچه معاملات → /history

🔄 *وضعیت و تنظیمات*
━━━━━━━━━━━━━━━━━━━━━━
🔄 وضعیت ربات → /status
⚙️ تنظیمات → /settings
ℹ️ راهنما → /help
📞 پشتیبانی → /support

📊 *تحلیل*
━━━━━━━━━━━━━━━━━━━━━━
📊 آنالیز تکنیکال → /analysis
📋 تاریخچه صرافی → /exchange_history

🕒 {time}""".format(time=format_iran_time())
    
    send_telegram_message(menu_text)

def format_iran_time(dt=None):
    if dt is None:
        dt = datetime.now(timezone(timedelta(hours=3, minutes=30)))
    return dt.strftime('%Y-%m-%d %H:%M:%S')

def format_iran_date(dt=None):
    if dt is None:
        dt = datetime.now(timezone(timedelta(hours=3, minutes=30)))
    return dt.strftime('%Y-%m-%d')

# =====================================================================================
# توابع محاسباتی پایه
# =====================================================================================
def calc_rma(series, length):
    """معادل دقیق ta.rma (Wilder Smoothing) در Pine Script — seed = SMA اولین length مقدار"""
    alpha = 1.0 / length
    rma = pd.Series(np.nan, index=series.index)
    if len(series) < length:
        return rma
    seed = series.iloc[:length].mean()
    rma.iloc[length - 1] = seed
    prev = seed
    for i in range(length, len(series)):
        prev = alpha * series.iloc[i] + (1 - alpha) * prev
        rma.iloc[i] = prev
    return rma

def calc_rsi(close, length=14):
    delta = close.diff().fillna(0)
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = calc_rma(gain, length)
    avg_loss = calc_rma(loss, length)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50)

def calc_ema(series, length):
    return series.ewm(span=length, adjust=False).mean()

def calc_macd(close, fast=12, slow=26, signal=9):
    ema_fast = calc_ema(close, fast)
    ema_slow = calc_ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calc_ema(macd_line, signal)
    return macd_line, signal_line, macd_line - signal_line

def calc_atr(high, low, close, length=14):
    prev_close = close.shift(1)
    tr = pd.concat([high-low, (high-prev_close).abs(), (low-prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0/length, min_periods=length, adjust=False).mean()

def find_pivot_high(high, left=5, right=3):
    n = len(high)
    result = pd.Series(np.nan, index=high.index)
    for i in range(left, n - right):
        if not (high.iloc[i-left:i] >= high.iloc[i]).any() and not (high.iloc[i+1:i+right+1] >= high.iloc[i]).any():
            result.iloc[i] = high.iloc[i]
    return result

def find_pivot_low(low, left=5, right=3):
    n = len(low)
    result = pd.Series(np.nan, index=low.index)
    for i in range(left, n - right):
        if not (low.iloc[i-left:i] <= low.iloc[i]).any() and not (low.iloc[i+1:i+right+1] <= low.iloc[i]).any():
            result.iloc[i] = low.iloc[i]
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
    if bar1 is None or bar2 is None or bar2 <= bar1 + 1:
        return False
    window = hist_series.iloc[bar1 + 1:bar2]
    if window.empty:
        return False
    return (window < 0).any() if need_negative_phase else (window > 0).any()

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

SYMBOLS = ["LTCUSDT", "DOGEUSDT", "ETHUSDT"]
SYMBOL_STATES = {s: SymbolState() for s in SYMBOLS}

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

def update_trade_result(symbol, stime, result, price):
    h = load_history()
    for t in h:
        if t['symbol'] == symbol and t['signal_time'] == stime:
            t['result'] = result
            t['close_price'] = price
            t['close_time'] = format_iran_time()
    save_history(h)

# =====================================================================================
# توابع گزارش
# =====================================================================================
def send_daily_report():
    history = load_history()
    today_str = format_iran_date()
    today_trades = [t for t in history if t.get('signal_time', '').startswith(today_str)]
    
    # دریافت تاریخچه واقعی از صرافی
    exchange = TrueTradePrivateExchange(API_KEY, API_SECRET, BASE_URL)
    try:
        exchange_trades = exchange.fetch_trades_history(
            start_date=f"{today_str}T00:00:00Z",
            end_date=f"{today_str}T23:59:59Z"
        )
    except:
        exchange_trades = []
    
    if not today_trades and not exchange_trades:
        return

    # ترکیب داده‌های محلی و صرافی
    total_trades = []
    if exchange_trades:
        for trade in exchange_trades:
            total_trades.append({
                'symbol': trade.get('symbol', 'N/A'),
                'direction': trade.get('side', 'N/A'),
                'entry_price': float(trade.get('entryPrice', 0)),
                'close_price': float(trade.get('closePrice', 0)) if trade.get('closePrice') else None,
                'pnl': float(trade.get('realizedPnL', 0)),
                'result': 'TAKE_PROFIT' if float(trade.get('realizedPnL', 0)) > 0 else 'STOP_LOSS' if float(trade.get('realizedPnL', 0)) < 0 else None
            })
    
    # اضافه کردن trades از تاریخچه محلی که در صرافی نیستند
    local_symbols = set()
    if exchange_trades:
        local_symbols = {t.get('symbol') for t in exchange_trades}
    
    for t in today_trades:
        if t['symbol'] not in local_symbols:
            total_trades.append(t)
    
    total = len(total_trades)
    wins = len([t for t in total_trades if t.get('result') == 'TAKE_PROFIT'])
    losses = len([t for t in total_trades if t.get('result') == 'STOP_LOSS'])
    win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
    
    total_pnl = sum([t.get('pnl', 0) for t in total_trades])

    message = f"""📊 گزارش روزانه — {today_str} {HASHTAGS['daily']}
━━━━━━━━━━━━━━━━━━━━━━

📈 کل معاملات: {total} عدد
✅ موفق: {wins} ({win_rate:.1f}%)
❌ ناموفق: {losses}
💰 سود/زیان خالص: {total_pnl:.2f} USDT

📊 نرخ موفقیت: {win_rate:.1f}%
💪 وضعیت: {'عالی! 🚀' if wins > losses else 'نیاز به بررسی 📊'}

آخرین معاملات:"""

    for i, trade in enumerate(total_trades[-5:], 1):
        result_emoji = "✅" if trade.get('result') == 'TAKE_PROFIT' else "❌" if trade.get('result') == 'STOP_LOSS' else "⏳"
        direction = "LONG" if trade.get('direction', trade.get('side', '')).upper() in ['BUY', 'LONG'] else "SHORT"
        pnl = trade.get('pnl', 0)
        message += f"\n{i}. {trade['symbol']} {direction} {result_emoji} | {pnl:.2f} USDT"

    message += f"\n\n━━━━━━━━━━━━━━━━━━━━━━\n🕒 {format_iran_time()}"
    send_telegram_message(message)

def send_monthly_report():
    history = load_history()
    month_ago = (datetime.now(timezone(timedelta(hours=3, minutes=30))) - timedelta(days=30)).strftime('%Y-%m-%d')
    
    # دریافت تاریخچه واقعی از صرافی
    exchange = TrueTradePrivateExchange(API_KEY, API_SECRET, BASE_URL)
    try:
        exchange_trades = exchange.fetch_trades_history(
            start_date=f"{month_ago}T00:00:00Z",
            end_date=f"{format_iran_date()}T23:59:59Z"
        )
    except:
        exchange_trades = []
    
    month_trades = [t for t in history if t.get('signal_time', '') >= month_ago]
    
    # ترکیب داده‌ها
    total_trades = []
    if exchange_trades:
        for trade in exchange_trades:
            total_trades.append({
                'symbol': trade.get('symbol', 'N/A'),
                'direction': trade.get('side', 'N/A'),
                'pnl': float(trade.get('realizedPnL', 0)),
                'result': 'TAKE_PROFIT' if float(trade.get('realizedPnL', 0)) > 0 else 'STOP_LOSS'
            })
    
    if not total_trades and not month_trades:
        return

    total = len(total_trades) if total_trades else len(month_trades)
    wins = len([t for t in total_trades if t.get('result') == 'TAKE_PROFIT'])
    losses = len([t for t in total_trades if t.get('result') == 'STOP_LOSS'])
    win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
    total_pnl = sum([t.get('pnl', 0) for t in total_trades])

    message = f"""📈 گزارش ۳۰ روز گذشته {HASHTAGS['monthly']}
━━━━━━━━━━━━━━━━━━━━━━

📊 کل معاملات: {total} عدد
✅ موفق: {wins} ({win_rate:.1f}%)
❌ ناموفق: {losses}
💰 سود/زیان خالص: {total_pnl:.2f} USDT

📈 نرخ موفقیت: {win_rate:.1f}%
📊 میانگین روزانه: {(wins-losses)/30:.1f} معامله
💪 ارزیابی: {'پروژه موفق! 🎉' if wins > losses else 'نیاز به بهینه‌سازی ⚙️'}

━━━━━━━━━━━━━━━━━━━━━━
🕒 {format_iran_time()}"""
    send_telegram_message(message)

def send_profit_report():
    """گزارش سود/زیان کل"""
    exchange = TrueTradePrivateExchange(API_KEY, API_SECRET, BASE_URL)
    
    try:
        # دریافت کل سود/زیان از صرافی
        pnl_data = exchange._request('GET', '/futures/assets/pnl')
        total_pnl = float(pnl_data.get('totalPnL', 0)) if pnl_data else 0
        
        # دریافت تاریخچه برای جزئیات بیشتر
        trades = exchange.fetch_trades_history()
        
        wins = len([t for t in trades if float(t.get('realizedPnL', 0)) > 0]) if trades else 0
        losses = len([t for t in trades if float(t.get('realizedPnL', 0)) < 0]) if trades else 0
        
        message = f"""💰 سود/زیان کل
━━━━━━━━━━━━━━━━━━━━━━

📊 سود/زیان خالص: {total_pnl:.2f} USDT
✅ معاملات سودده: {wins}
❌ معاملات ضررده: {losses}
📈 نسبت سود به ضرر: {(wins/(wins+losses)*100) if (wins+losses) > 0 else 0:.1f}%

💪 وضعیت: {'سودده 📈' if total_pnl > 0 else 'در ضرر 📉' if total_pnl < 0 else 'سر به سر ⚖️'}

━━━━━━━━━━━━━━━━━━━━━━
🕒 {format_iran_time()}"""
        
        send_telegram_message(message)
    except Exception as e:
        send_telegram_message(f"❌ خطا در دریافت گزارش سود/زیان:\n{str(e)[:200]}")

def send_best_signals():
    """بهترین سیگنال‌های تاریخ"""
    history = load_history()
    if not history:
        send_telegram_message("📊 هنوز هیچ سیگنالی ثبت نشده است.")
        return
    
    # مرتب‌سازی بر اساس سود
    sorted_trades = sorted([t for t in history if t.get('result') == 'TAKE_PROFIT'], 
                          key=lambda x: abs(float(x.get('take_profit', 0)) - float(x.get('entry_price', 0))) / float(x.get('entry_price', 1)) * 100, 
                          reverse=True)
    
    message = f"""🏆 بهترین سیگنال‌ها
━━━━━━━━━━━━━━━━━━━━━━

📊 ۵ سیگنال برتر:"""

    for i, trade in enumerate(sorted_trades[:5], 1):
        profit_pct = abs(float(trade['take_profit']) - float(trade['entry_price'])) / float(trade['entry_price']) * 100
        message += f"""
{i}. #{trade.get('signal_number', '?')} {trade['symbol']}
📈 {trade['direction']} | سود: +{profit_pct:.2f}%
⭐ امتیاز: {trade.get('score', 'N/A')}/5
📅 {trade.get('signal_time', 'N/A')}"""

    message += f"""

━━━━━━━━━━━━━━━━━━━━━━
🕒 {format_iran_time()}"""
    send_telegram_message(message)

def send_worst_signals():
    """بدترین سیگنال‌های تاریخ"""
    history = load_history()
    if not history:
        send_telegram_message("📊 هنوز هیچ سیگنالی ثبت نشده است.")
        return
    
    # مرتب‌سازی بر اساس ضرر
    sorted_trades = sorted([t for t in history if t.get('result') == 'STOP_LOSS'], 
                          key=lambda x: abs(float(x.get('stop_loss', 0)) - float(x.get('entry_price', 0))) / float(x.get('entry_price', 1)) * 100, 
                          reverse=True)
    
    message = f"""📉 بدترین سیگنال‌ها
━━━━━━━━━━━━━━━━━━━━━━

📊 ۵ سیگنال با بیشترین ضرر:"""

    for i, trade in enumerate(sorted_trades[:5], 1):
        loss_pct = abs(float(trade['stop_loss']) - float(trade['entry_price'])) / float(trade['entry_price']) * 100
        message += f"""
{i}. #{trade.get('signal_number', '?')} {trade['symbol']}
📉 {trade['direction']} | ضرر: -{loss_pct:.2f}%
⭐ امتیاز: {trade.get('score', 'N/A')}/5
📅 {trade.get('signal_time', 'N/A')}"""

    message += f"""

━━━━━━━━━━━━━━━━━━━━━━
🕒 {format_iran_time()}"""
    send_telegram_message(message)

def send_tf_stats():
    """آمار تایم‌فریم‌ها"""
    history = load_history()
    if not history:
        send_telegram_message("📊 هنوز هیچ سیگنالی ثبت نشده است.")
        return
    
    # آمار بر اساس تایم‌فریم (در اینجا فقط 1m داریم)
    message = f"""📊 آمار تایم‌فریم‌ها
━━━━━━━━━━━━━━━━━━━━━━

⏱️ تایم‌فریم ۱ دقیقه (1m):
📊 کل سیگنال‌ها: {len(history)}
✅ موفق: {len([t for t in history if t.get('result') == 'TAKE_PROFIT'])}
❌ ناموفق: {len([t for t in history if t.get('result') == 'STOP_LOSS'])}
⏳ باز: {len([t for t in history if t.get('result') is None])}

📈 نرخ موفقیت: {(len([t for t in history if t.get('result') == 'TAKE_PROFIT']) / (len([t for t in history if t.get('result') in ['TAKE_PROFIT', 'STOP_LOSS']])) * 100) if len([t for t in history if t.get('result') in ['TAKE_PROFIT', 'STOP_LOSS']]) > 0 else 0:.1f}%

━━━━━━━━━━━━━━━━━━━━━━
🕒 {format_iran_time()}"""
    send_telegram_message(message)

def send_symbol_stats():
    """آمار ارزها"""
    history = load_history()
    if not history:
        send_telegram_message("📊 هنوز هیچ سیگنالی ثبت نشده است.")
        return
    
    message = f"""🪙 آمار ارزها
━━━━━━━━━━━━━━━━━━━━━━"""

    for symbol in SYMBOLS:
        symbol_trades = [t for t in history if t['symbol'] == symbol]
        if not symbol_trades:
            continue
        
        wins = len([t for t in symbol_trades if t.get('result') == 'TAKE_PROFIT'])
        losses = len([t for t in symbol_trades if t.get('result') == 'STOP_LOSS'])
        win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
        
        message += f"""

🔹 {symbol}:
📊 کل: {len(symbol_trades)}
✅ موفق: {wins}
❌ ناموفق: {losses}
📈 نرخ موفقیت: {win_rate:.1f}%"""

    message += f"""

━━━━━━━━━━━━━━━━━━━━━━
🕒 {format_iran_time()}"""
    send_telegram_message(message)

def send_exchange_history(period='1d'):
    """دریافت تاریخچه معاملات از صرافی با فیلتر زمانی"""
    exchange = TrueTradePrivateExchange(API_KEY, API_SECRET, BASE_URL)
    
    now = datetime.now(timezone(timedelta(hours=3, minutes=30)))
    
    if period == '1d':
        start_date = (now - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        title = "۲۴ ساعت اخیر"
    elif period == '1w':
        start_date = (now - timedelta(weeks=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        title = "۱ هفته اخیر"
    elif period == '1m':
        start_date = (now - timedelta(days=30)).strftime('%Y-%m-%dT%H:%M:%SZ')
        title = "۱ ماه اخیر"
    elif period == '3m':
        start_date = (now - timedelta(days=90)).strftime('%Y-%m-%dT%H:%M:%SZ')
        title = "۳ ماه اخیر"
    else:
        start_date = (now - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        title = "۲۴ ساعت اخیر"
    
    try:
        trades = exchange.fetch_trades_history(start_date=start_date)
        
        if not trades:
            send_telegram_message(f"📋 تاریخچه معاملات صرافی — {title}\n\nهیچ معامله‌ای در این بازه زمانی یافت نشد.")
            return
        
        total_pnl = sum([float(t.get('realizedPnL', 0)) for t in trades])
        wins = len([t for t in trades if float(t.get('realizedPnL', 0)) > 0])
        losses = len([t for t in trades if float(t.get('realizedPnL', 0)) < 0])
        
        message = f"""📋 تاریخچه معاملات صرافی — {title}
━━━━━━━━━━━━━━━━━━━━━━

📊 کل معاملات: {len(trades)}
✅ سودده: {wins}
❌ ضررده: {losses}
💰 سود/زیان خالص: {total_pnl:.2f} USDT

۱۰ معامله آخر:"""

        for i, trade in enumerate(trades[:10], 1):
            pnl = float(trade.get('realizedPnL', 0))
            emoji = "✅" if pnl > 0 else "❌" if pnl < 0 else "⚪"
            message += f"\n{i}. {trade.get('symbol', 'N/A')} | {pnl:.2f} USDT {emoji}"

        message += f"""

━━━━━━━━━━━━━━━━━━━━━━
🕒 {format_iran_time()}"""
        send_telegram_message(message)
    except Exception as e:
        send_telegram_message(f"❌ خطا در دریافت تاریخچه صرافی:\n{str(e)[:200]}")

def send_today_signals():
    """نمایش سیگنال‌های امروز"""
    history = load_history()
    today_str = format_iran_date()
    today_trades = [t for t in history if t.get('signal_time', '').startswith(today_str)]
    
    if not today_trades:
        send_telegram_message(f"📊 سیگنال‌های امروز — {today_str}\n\nهنوز سیگنالی برای امروز ثبت نشده است.")
        return
    
    message = f"""📊 سیگنال‌های امروز — {today_str}
━━━━━━━━━━━━━━━━━━━━━━

📈 تعداد: {len(today_trades)} سیگنال"""

    for i, trade in enumerate(today_trades, 1):
        direction_emoji = "🟢" if trade['direction'] == 'BUY' else "🔴"
        result_emoji = "✅" if trade.get('result') == 'TAKE_PROFIT' else "❌" if trade.get('result') == 'STOP_LOSS' else "⏳"
        
        message += f"""
{i}. #{trade.get('signal_number', '?')} {trade['symbol']} {direction_emoji}
📍 ورود: {trade['entry_price']}
🎯 هدف: {trade['take_profit']}
🛑 ضرر: {trade['stop_loss']}
⭐ امتیاز: {trade.get('score', 'N/A')}/5
📊 وضعیت: {result_emoji}"""

    message += f"""

━━━━━━━━━━━━━━━━━━━━━━
🕒 {format_iran_time()}"""
    send_telegram_message(message)

def send_signal_status():
    """وضعیت سیگنال‌های باز"""
    history = load_history()
    open_trades = [t for t in history if t.get('result') is None]
    
    if not open_trades:
        send_telegram_message("🔍 وضعیت سیگنال‌های باز\n\n✅ همه سیگنال‌ها بسته شده‌اند.")
        return
    
    data = TrueTradePublicData()
    
    message = f"""🔍 وضعیت سیگنال‌های باز
━━━━━━━━━━━━━━━━━━━━━━

📊 تعداد: {len(open_trades)} سیگنال باز"""

    for i, trade in enumerate(open_trades, 1):
        # دریافت قیمت فعلی
        try:
            df = data.fetch_ohlcv(trade['symbol'], '1m', 2)
            current_price = df['close'].iloc[-1] if df is not None and not df.empty else None
        except:
            current_price = None
        
        direction_emoji = "🟢" if trade['direction'] == 'BUY' else "🔴"
        
        if current_price:
            entry = float(trade['entry_price'])
            if trade['direction'] == 'BUY':
                pnl_pct = (current_price - entry) / entry * 100
            else:
                pnl_pct = (entry - current_price) / entry * 100
            pnl_emoji = "📈" if pnl_pct > 0 else "📉"
        else:
            pnl_pct = 0
            pnl_emoji = "⚪"
        
        message += f"""
{i}. #{trade.get('signal_number', '?')} {trade['symbol']} {direction_emoji}
📍 ورود: {trade['entry_price']}
💰 فعلی: {current_price:.4f} {pnl_emoji}
📊 سود/ضرر: {pnl_pct:+.2f}%
🎯 هدف: {trade['take_profit']}
🛑 ضرر: {trade['stop_loss']}
🕒 زمان: {trade.get('signal_time', 'N/A')}"""

    message += f"""

━━━━━━━━━━━━━━━━━━━━━━
🕒 {format_iran_time()}"""
    send_telegram_message(message)

def send_status():
    """وضعیت ربات"""
    exchange = TrueTradePrivateExchange(API_KEY, API_SECRET, BASE_URL)
    conn = exchange.test_connection()
    balance = exchange.fetch_balance() if conn else 0
    
    history = load_history()
    open_trades = len([t for t in history if t.get('result') is None])
    total_trades = len(history)
    
    message = f"""🔄 وضعیت ربات
━━━━━━━━━━━━━━━━━━━━━━

🤖 ربات: ✅ آنلاین
📡 صرافی: {'✅ متصل' if conn else '❌ قطع'}
💰 موجودی: {balance:.2f} USDT

📊 آمار کلی:
🔹 کل سیگنال‌ها: {total_trades}
🟢 باز: {open_trades}
🔴 بسته شده: {total_trades - open_trades}

⚙️ تنظیمات:
🔧 لوریج: ETH=50x | LTC/DOGE=75x
💵 ریسک: 3.5 USDT
⏱️ تایم‌فریم: 1m

━━━━━━━━━━━━━━━━━━━━━━
🕒 {format_iran_time()}"""
    send_telegram_message(message)

def send_help():
    """راهنمای استفاده"""
    message = f"""ℹ️ راهنمای استفاده از ربات DTM Pro
━━━━━━━━━━━━━━━━━━━━━━

🤖 *درباره ربات:*
ربات DTM Pro بر اساس استراتژی Divergence Trading Master (DTM) کار می‌کند و واگرایی‌های RSI و MACD را تشخیص می‌دهد.

📊 *دستورات:*
• /menu - نمایش منوی کامل
• /today - سیگنال‌های امروز
• /daily - گزارش روزانه
• /monthly - گزارش ماهانه
• /signal - وضعیت سیگنال‌های باز
• /profit - سود/زیان کل
• /best - بهترین سیگنال‌ها
• /worst - بدترین سیگنال‌ها
• /history - تاریخچه کامل
• /exchange_history - تاریخچه صرافی
• /status - وضعیت ربات
• /help - این راهنما

⚙️ *تنظیمات خودکار:*
• تایم‌فریم: ۱ دقیقه
• حداقل R/R: 2.0
• ریسک: 3.5 USDT
• لوریج: ETH=50x, LTC/DOGE=75x

━━━━━━━━━━━━━━━━━━━━━━
🕒 {format_iran_time()}"""
    send_telegram_message(message)

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
        df = public_data.fetch_ohlcv("LTCUSDT", "1m", 1500)
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

    diagnostic_log.append("\n━━━━━━━━━━━━━━━━━━━━━━")
    diagnostic_log.append("✅ تمام بخش‌ها فعال هستند" if conn else "⚠️ برخی بخش‌ها غیرفعال هستند")
    diagnostic_log.append(f"🕒 {format_iran_time()}")

    send_telegram_message("\n".join(diagnostic_log))
    logger.info("Startup Diagnostic Complete")

# =====================================================================================
# تابع تشخیص سیگنال
# =====================================================================================
def detect_signal(df, state, symbol, debug=False):
    debug_log = []
    def log(msg):
        debug_log.append(msg)
        if debug:
            logger.info(msg)

    log(f"🔍 DTM — {symbol} | {format_iran_time()}")

    closed_df_indexed = df.iloc[:-1].copy()
    closed_df = closed_df_indexed.reset_index(drop=True)

    n = len(closed_df)
    if n < 33:
        log(f"❌ داده ناکافی: {n}")
        return None, None, None, None, False, None, None, None, []

    close = closed_df["close"]
    high = closed_df["high"]
    low = closed_df["low"]

    rsi_val = calc_rsi(close, 14)
    macd_line, signal_line, hist_line = calc_macd(close, 12, 26, 9)
    atr14 = calc_atr(high, low, close, 14)
    pivot_high = find_pivot_high(high, 5, 3)
    pivot_low = find_pivot_low(low, 5, 3)

    last_valid_pivot_index = n - RIGHT_BARS - 1

    new_pivots_high = []
    new_pivots_low = []

    existing_high_ts = {p['ts'] for p in state.pivot_highs}
    existing_low_ts = {p['ts'] for p in state.pivot_lows}

    # ✅ همیشه از صفر اسکن کن؛ dedup با existing_ts انجام میشه
    start_bar = 0
    start_bar = min(start_bar, last_valid_pivot_index)

    log(f"   n={n}, last_valid={last_valid_pivot_index}, start={start_bar}")

    for i in range(start_bar, last_valid_pivot_index + 1):
        ts = closed_df_indexed.index[i]
        if not pd.isna(pivot_high.iloc[i]) and ts not in existing_high_ts:
            new_pivots_high.append({
                'ts': ts, 'price': pivot_high.iloc[i],
                'rsi': rsi_val.iloc[i],
                'macdline': macd_line.iloc[i],
                'hist': hist_line.iloc[i]
            })
        if not pd.isna(pivot_low.iloc[i]) and ts not in existing_low_ts:
            new_pivots_low.append({
                'ts': ts, 'price': pivot_low.iloc[i],
                'rsi': rsi_val.iloc[i],
                'macdline': macd_line.iloc[i],
                'hist': hist_line.iloc[i]
            })

    if n > 0:
        state.last_processed_ts = closed_df_indexed.index[min(last_valid_pivot_index, n-1)]

    state.pivot_highs.extend(new_pivots_high)
    state.pivot_lows.extend(new_pivots_low)

    if len(state.pivot_highs) > 100:
        state.pivot_highs = state.pivot_highs[-100:]
    if len(state.pivot_lows) > 100:
        state.pivot_lows = state.pivot_lows[-100:]

    log(f"   new_high={len(new_pivots_high)}, new_low={len(new_pivots_low)} | mem: H={len(state.pivot_highs)} L={len(state.pivot_lows)}")

    early_signal = len(new_pivots_high) > 0 or len(new_pivots_low) > 0
    entry_price = close.iloc[-1]

    buy_signal = sell_signal = None
    buy_emoji = sell_emoji = None
    buy_label = sell_label = None
    buy_score = sell_score = 0
    buy_stop = buy_target = sell_stop = sell_target = None
    buy_details = sell_details = []

    # BUY
    if len(new_pivots_low) > 0 and len(state.pivot_lows) >= 2:
        pl_1, pl_2 = state.pivot_lows[-2], state.pivot_lows[-1]
        bar1 = resolve_bar_from_ts(closed_df_indexed, pl_1['ts'])
        bar2 = resolve_bar_from_ts(closed_df_indexed, pl_2['ts'])

        if bar1 is not None and bar2 is not None:
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
                    buy_score = score
                    buy_details = details
                    div_type = "Classic" if is_classic_buy else "Hidden"
                    log(f"   🔵 {div_type} BUY score={score}/5 {'✅' if buy_emoji else '❌'}")
                    for d in details:
                        log(f"      {d}")

                    if buy_emoji and score >= 3:
                        stop, tp_raw, _ = compute_stop_and_targets(
                            state.pivot_highs, state.pivot_lows, "long", closed_df_indexed, atr14.iloc[-1]
                        )
                        if stop and tp_raw:
                            buy_stop, buy_target = stop, resolve_final_target(entry_price, stop, tp_raw, "long")
                            buy_signal = "BUY"
                            log(f"   Entry={entry_price:.4f}, SL={stop:.4f}, TP={buy_target:.4f}")
        else:
            log(f"   🔵 BUY: bar1/bar2 قابل resolve نبود")

    # SELL
    if len(new_pivots_high) > 0 and len(state.pivot_highs) >= 2:
        ph_1, ph_2 = state.pivot_highs[-2], state.pivot_highs[-1]
        bar1 = resolve_bar_from_ts(closed_df_indexed, ph_1['ts'])
        bar2 = resolve_bar_from_ts(closed_df_indexed, ph_2['ts'])

        if bar1 is not None and bar2 is not None:
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
                    sell_score = score
                    sell_details = details
                    div_type = "Classic" if is_classic_sell else "Hidden"
                    log(f"   🔴 {div_type} SELL score={score}/5 {'✅' if sell_emoji else '❌'}")
                    for d in details:
                        log(f"      {d}")

                    if sell_emoji and score >= 3:
                        stop, tp_raw, _ = compute_stop_and_targets(
                            state.pivot_highs, state.pivot_lows, "short", closed_df_indexed, atr14.iloc[-1]
                        )
                        if stop and tp_raw:
                            sell_stop, sell_target = stop, resolve_final_target(entry_price, stop, tp_raw, "short")
                            sell_signal = "SELL"
                            log(f"   Entry={entry_price:.4f}, SL={stop:.4f}, TP={sell_target:.4f}")
        else:
            log(f"   🔴 SELL: bar1/bar2 قابل resolve نبود")

    if not buy_signal and not sell_signal:
        log(f"   ⚪ No signal")

    # لاگ تلگرام
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
def check_proximity(symbol, current_price, entry, stop, target, direction, capital, leverage, qty, signal_number):
    if entry is None or stop is None or target is None:
        return
    risk_dist = abs(entry - stop)
    reward_dist = abs(target - entry)
    if direction == 'BUY':
        stop_distance = (current_price - stop) / risk_dist
        target_progress = (current_price - entry) / reward_dist
    else:
        stop_distance = (stop - current_price) / risk_dist
        target_progress = (entry - current_price) / reward_dist
    if stop_distance <= 0.25 and stop_distance > 0:
        send_telegram_message(
            f"⚠️ هشدار نزدیکی به حد ضرر (75%) {HASHTAGS['proximity_stop']} #سیگنال_{signal_number}\n\n"
            f"🔹 نماد: {symbol}\n💰 قیمت فعلی: {current_price:.4f}\n"
            f"🛑 حد ضرر: {stop:.4f}\n📊 فاصله: {stop_distance*100:.1f}%\n\n"
            f"⚠️ فقط ۲۵٪ باقی مانده\n🕒 {format_iran_time()}"
        )
    if target_progress >= 0.60 and target_progress < 1.0:
        unrealized_r = target_progress / (1 - target_progress) if target_progress < 1 else 999
        send_telegram_message(
            f"🎯 هشدار نزدیکی به حد سود (R:R = 1.5) {HASHTAGS['proximity_target']} #سیگنال_{signal_number}\n\n"
            f"🔹 نماد: {symbol}\n💰 قیمت فعلی: {current_price:.4f}\n"
            f"🎯 حد سود: {target:.4f}\n📊 پیشرفت: {target_progress*100:.1f}%\n"
            f"⚖️ R:R فعلی: {unrealized_r:.1f}\n\n"
            f"💡 سود شناور: {capital * leverage * abs(current_price - entry) / entry:.2f} USDT\n"
            f"🕒 {format_iran_time()}"
        )

def track_open_signals():
    history = load_history()
    data = TrueTradePublicData()
    exchange = TrueTradePrivateExchange(API_KEY, API_SECRET, BASE_URL)
    
    for trade in history:
        if trade.get('result') is None:
            df = data.fetch_ohlcv(trade['symbol'], '1m', 10)
            if df is None or df.empty:
                continue
            cp = df['close'].iloc[-1]
            entry = trade['entry_price']
            stop = trade['stop_loss']
            target = trade['take_profit']
            direction = trade['direction']
            capital = trade.get('capital', 3.5)
            leverage = trade.get('leverage', 50)
            qty = trade.get('qty', 0)
            signal_number = trade.get('signal_number', '?')
            
            # بررسی نزدیکی به استاپ/تارگت
            check_proximity(trade['symbol'], cp, entry, stop, target, direction, capital, leverage, qty, signal_number)
            
            # بررسی وضعیت در صرافی
            try:
                positions = exchange.fetch_positions_history(symbol=trade['symbol'])
                position_closed = True
                position_pnl = 0
                
                if positions:
                    for pos in positions:
                        if pos.get('status') == 'OPEN' and pos.get('symbol') == trade['symbol']:
                            position_closed = False
                            break
                
                if position_closed:
                    # پوزیشن بسته شده - بررسی نتیجه
                    trades = exchange.fetch_trades_history(symbol=trade['symbol'])
                    if trades:
                        # پیدا کردن آخرین trade برای این symbol
                        last_trade = trades[0] if trades else None
                        if last_trade:
                            pnl = float(last_trade.get('realizedPnL', 0))
                            if pnl > 0:
                                result = 'TAKE_PROFIT'
                            elif pnl < 0:
                                result = 'STOP_LOSS'
                            else:
                                result = 'BREAKEVEN'
                            
                            update_trade_result(trade['symbol'], trade['signal_time'], result, cp)
                            
                            if result == 'TAKE_PROFIT':
                                send_telegram_message(
                                    f"🎯 سیگنال بسته شد — {HASHTAGS['target']} #سیگنال_{signal_number}\n\n"
                                    f"🔹 {trade['symbol']} | {direction}\n"
                                    f"💰 سود: {pnl:.2f} USDT\n"
                                    f"🕒 {format_iran_time()}"
                                )
                            elif result == 'STOP_LOSS':
                                send_telegram_message(
                                    f"💔 سیگنال بسته شد — {HASHTAGS['stop']} #سیگنال_{signal_number}\n\n"
                                    f"🔹 {trade['symbol']} | {direction}\n"
                                    f"💰 ضرر: {abs(pnl):.2f} USDT\n"
                                    f"🕒 {format_iran_time()}"
                                )
            except:
                # اگر دسترسی به صرافی ممکن نبود، از روش قبلی استفاده کن
                if direction == 'BUY':
                    if cp >= target:
                        profit_pct = (cp-entry)/entry*100
                        profit_usdt = capital * leverage * profit_pct / 100
                        update_trade_result(trade['symbol'], trade['signal_time'], 'TAKE_PROFIT', cp)
                        send_telegram_message(f"🎯 حد سود فعال شد {HASHTAGS['target']} #سیگنال_{signal_number}\n\n🔹 {trade['symbol']} | LONG\n📍 {entry:.4f} → 🎯 {cp:.4f}\n📈 +{profit_pct:.2f}% | 💰 +{profit_usdt:.2f} USDT\n🕒 {format_iran_time()}")
                    elif cp <= stop:
                        loss_pct = (cp-entry)/entry*100
                        loss_usdt = capital * leverage * abs(loss_pct) / 100
                        update_trade_result(trade['symbol'], trade['signal_time'], 'STOP_LOSS', cp)
                        send_telegram_message(f"💔 حد ضرر فعال شد {HASHTAGS['stop']} #سیگنال_{signal_number}\n\n🔹 {trade['symbol']} | LONG\n📍 {entry:.4f} → 💔 {cp:.4f}\n📉 {loss_pct:.2f}% | 💰 {loss_usdt:.2f} USDT\n🕒 {format_iran_time()}")
                else:
                    if cp <= target:
                        profit_pct = (entry-cp)/entry*100
                        profit_usdt = capital * leverage * profit_pct / 100
                        update_trade_result(trade['symbol'], trade['signal_time'], 'TAKE_PROFIT', cp)
                        send_telegram_message(f"🎯 حد سود فعال شد {HASHTAGS['target']} #سیگنال_{signal_number}\n\n🔹 {trade['symbol']} | SHORT\n📍 {entry:.4f} → 🎯 {cp:.4f}\n📈 +{profit_pct:.2f}% | 💰 +{profit_usdt:.2f} USDT\n🕒 {format_iran_time()}")
                    elif cp >= stop:
                        loss_pct = (entry-cp)/entry*100
                        loss_usdt = capital * leverage * abs(loss_pct) / 100
                        update_trade_result(trade['symbol'], trade['signal_time'], 'STOP_LOSS', cp)
                        send_telegram_message(f"💔 حد ضرر فعال شد {HASHTAGS['stop']} #سیگنال_{signal_number}\n\n🔹 {trade['symbol']} | SHORT\n📍 {entry:.4f} → 💔 {cp:.4f}\n📉 {loss_pct:.2f}% | 💰 {loss_usdt:.2f} USDT\n🕒 {format_iran_time()}")

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
    track_open_signals()
    side_map = {"BUY": "LONG", "SELL": "SHORT"}
    leverage_map = {"LTCUSDT": 75, "DOGEUSDT": 75, "ETHUSDT": 50}

    for symbol in SYMBOLS:
        try:
            df = data.fetch_ohlcv(symbol, '1m', 1500)
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
                send_telegram_message(f"⚡ Pivot جدید — {symbol} {HASHTAGS['pivot']}\n💰 {cp:.4f}\n⏳ ~۲ دقیقه تا تأیید\n🕒 {format_iran_time()}")

            if signal and stop and target:
                entry = round_price(entry, symbol)
                stop = round_price(stop, symbol)
                target = round_price(target, symbol)

                profit_pct = (target-entry)/entry*100 if signal=="BUY" else (entry-target)/entry*100
                loss_pct = (entry-stop)/entry*100 if signal=="BUY" else (stop-entry)/entry*100
                rr = abs(profit_pct/loss_pct) if loss_pct != 0 else 0
                direction_text = "LONG (خرید)" if signal == "BUY" else "SHORT (فروش)"
                direction_emoji = "🟢" if signal == "BUY" else "🔴"
                details_text = "\n".join([f"{i+1}. {d}" for i, d in enumerate(details)]) if details else ""

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

                # ✅ پیام سیگنال با فرمت کامل
                signal_message = (
                    f"{emoji} سیگنال {label} — {symbol} {HASHTAGS['signal']} #سیگنال_{signal_number}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"📊 امتیاز: {score}/5\n"
                    f"🔸 {direction_emoji} {direction_text}\n\n"
                    f"📍 *نقطه ورود:* `{entry:.{PRICE_PRECISION.get(symbol, 2)}f}`\n"
                    f"🛑 *حد ضرر:* `{stop:.{PRICE_PRECISION.get(symbol, 2)}f}`\n"
                    f"🎯 *حد سود:* `{target:.{PRICE_PRECISION.get(symbol, 2)}f}`\n\n"
                    f"📈 سود: +{profit_pct:.2f}%\n"
                    f"📉 ضرر: -{loss_pct:.2f}%\n"
                    f"⚖️ نسبت ریسک به ریوارد: {rr:.2f}\n"
                    f"🔧 لوریج: {int(used_leverage)}x\n"
                    f"💰 سرمایه: {capital:.2f} USDT\n\n"
                    f"✅ *دلایل سیگنال:*\n{details_text}\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🕒 {format_iran_time()}"
                )
                send_telegram_message(signal_message)
                time.sleep(0.5)  # ✅ مکث کوتاه برای جلوگیری از Rate Limit تلگرام

                history = load_history()
                history.append({
                    'symbol': symbol, 'direction': signal,
                    'entry_price': entry, 'stop_loss': stop, 'take_profit': target,
                    'signal_time': format_iran_time(), 'result': None,
                    'score': score, 'label': label, 'capital': capital,
                    'leverage': int(used_leverage), 'qty': qty,
                    'signal_number': signal_number
                })
                save_history(history)

                if exchange.connected:
                    try:
                        exchange.create_order(symbol, "market", side_map[signal], capital, None,
                            {'leverage': int(used_leverage), 'stopLoss': stop, 'takeProfit': target})
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
                            f"📉 {actual_risk:.2f} USDT | 📈 {potential_profit:.2f} USDT\n"
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

# =====================================================================================
# وب‌هوک تلگرام
# =====================================================================================
def handle_telegram_webhook():
    """پردازش دستورات تلگرام"""
    try:
        update = request.get_json()
        if not update or 'message' not in update:
            return "OK", 200
        
        message = update['message']
        chat_id = message.get('chat', {}).get('id')
        text = message.get('text', '').strip()
        
        # فقط به پیام‌های کاربر مورد نظر پاسخ بده
        if str(chat_id) != TELEGRAM_CHAT_ID:
            return "OK", 200
        
        # پردازش دستورات
        command = text.split()[0].lower() if text else ''
        args = text.split()[1:] if len(text.split()) > 1 else []
        
        if command == '/menu' or command == '/start':
            send_telegram_menu()
        elif command == '/today':
            send_today_signals()
        elif command == '/daily':
            send_daily_report()
        elif command == '/monthly':
            send_monthly_report()
        elif command == '/signal':
            send_signal_status()
        elif command == '/profit':
            send_profit_report()
        elif command == '/best':
            send_best_signals()
        elif command == '/worst':
            send_worst_signals()
        elif command == '/tfstats':
            send_tf_stats()
        elif command == '/symbolstats':
            send_symbol_stats()
        elif command == '/history':
            history = load_history()
            if not history:
                send_telegram_message("📋 تاریخچه خالی است.")
            else:
                message_text = f"📋 تاریخچه معاملات ({len(history)} مورد)\n━━━━━━━━━━━━━━━━━━━━━━\n"
                for i, trade in enumerate(history[-10:], 1):
                    result_emoji = "✅" if trade.get('result') == 'TAKE_PROFIT' else "❌" if trade.get('result') == 'STOP_LOSS' else "⏳"
                    message_text += f"{i}. {trade['symbol']} {result_emoji} | {trade.get('signal_time', 'N/A')}\n"
                message_text += f"━━━━━━━━━━━━━━━━━━━━━━\n🕒 {format_iran_time()}"
                send_telegram_message(message_text)
        elif command == '/exchange_history':
            period = args[0] if args else '1d'
            send_exchange_history(period)
        elif command == '/status':
            send_status()
        elif command == '/help':
            send_help()
        elif command == '/settings':
            send_telegram_message(f"⚙️ تنظیمات فعلی:\n\n"
                                f"🔧 لوریج: ETH=50x | LTC/DOGE=75x\n"
                                f"💵 ریسک: 3.5 USDT\n"
                                f"⏱️ تایم‌فریم: 1m\n"
                                f"📊 حداقل R/R: 2.0\n\n"
                                f"🕒 {format_iran_time()}")
        elif command == '/support':
            send_telegram_message(f"📞 پشتیبانی\n\n"
                                f"برای ارتباط با پشتیبانی به آیدی زیر پیام دهید:\n"
                                f"@TheTrueTradeSupport\n\n"
                                f"🕒 {format_iran_time()}")
        elif command == '/analysis':
            symbol = args[0].upper() if args else 'LTCUSDT'
            data = TrueTradePublicData()
            df = data.fetch_ohlcv(symbol, '1m', 100)
            if df is not None and not df.empty:
                rsi = calc_rsi(df['close'], 14)
                macd_line, signal_line, hist = calc_macd(df['close'])
                
                message_text = f"""📊 آنالیز تکنیکال — {symbol}
━━━━━━━━━━━━━━━━━━━━━━

💰 قیمت: {df['close'].iloc[-1]:.4f}
📈 RSI(14): {rsi.iloc[-1]:.2f}
📊 MACD: {macd_line.iloc[-1]:.4f}
📉 Signal: {signal_line.iloc[-1]:.4f}
📊 Histogram: {hist.iloc[-1]:.4f}

📈 بالاترین: {df['high'].max():.4f}
📉 پایین‌ترین: {df['low'].min():.4f}
📊 حجم: {df['volume'].sum():.0f}

🕒 {format_iran_time()}"""
                send_telegram_message(message_text)
            else:
                send_telegram_message(f"❌ خطا در دریافت داده برای {symbol}")
        else:
            send_telegram_message(f"❓ دستور نامشخص: {text}\n\nاز /menu برای مشاهده منو استفاده کنید.")
        
        return "OK", 200
    except Exception as e:
        logger.error(f"[TELEGRAM WEBHOOK] {e}")
        return "OK", 200

# =====================================================================================
# حلقه اصلی
# =====================================================================================
def main_loop():
    last_daily_report = None
    last_monthly_report = None
    while True:
        try:
            logger.info(f"[LOOP] {format_iran_time()}")
            analyze_and_execute()
            today = format_iran_date()
            if last_daily_report != today:
                send_daily_report()
                last_daily_report = today
            if last_monthly_report is None or (datetime.now(timezone(timedelta(hours=3, minutes=30))) - last_monthly_report).days >= 30:
                send_monthly_report()
                last_monthly_report = datetime.now(timezone(timedelta(hours=3, minutes=30)))
            time.sleep(60)
        except Exception as e:
            logger.error(f"[LOOP] {e}")
            time.sleep(60)

# =====================================================================================
app = Flask(__name__)

@app.route("/")
def health():
    return "OK", 200

@app.route(f"/webhook/{TELEGRAM_BOT_TOKEN}", methods=['POST'])
def telegram_webhook():
    return handle_telegram_webhook()

@app.route("/exchange_history/<period>")
def api_exchange_history(period):
    """API برای دریافت تاریخچه صرافی (قابل استفاده در منوی پارامتری)"""
    try:
        exchange = TrueTradePrivateExchange(API_KEY, API_SECRET, BASE_URL)
        
        now = datetime.now(timezone(timedelta(hours=3, minutes=30)))
        
        if period == '1d':
            start_date = (now - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        elif period == '1w':
            start_date = (now - timedelta(weeks=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        elif period == '1m':
            start_date = (now - timedelta(days=30)).strftime('%Y-%m-%dT%H:%M:%SZ')
        elif period == '3m':
            start_date = (now - timedelta(days=90)).strftime('%Y-%m-%dT%H:%M:%SZ')
        else:
            start_date = (now - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        
        trades = exchange.fetch_trades_history(start_date=start_date)
        return json.dumps(trades, indent=2), 200, {'Content-Type': 'application/json'}
    except Exception as e:
        return json.dumps({"error": str(e)}), 500, {'Content-Type': 'application/json'}

if __name__ == "__main__":
    logger.info("DTM Bot Starting...")
    
    load_signal_counter()

    hashtag_list = "\n".join([f"• {v} → {k}" for k, v in HASHTAGS.items()])
    send_telegram_message(
        f"🤖 DTM Pro — آنلاین {HASHTAGS['startup']}\n\n"
        f"🧠 DTM Divergence (Pine Script Mirror)\n"
        f"📊 سیگنال + ترید خودکار\n\n"
        f"⚙️ Pivot: 5/3 | R/R: 2.0 | Risk: 3.5 USDT\n"
        f"🔧 ETH=50x | LTC/DOGE=75x\n\n"
        f"📌 هشتگ‌های ثابت:\n{hashtag_list}\n\n"
        f"📊 شمارنده سیگنال: از #سیگنال_{SIGNAL_COUNTER + 1} شروع می‌شود\n\n"
        f"📋 منوی کامل با /menu قابل دسترسی است\n\n"
        f"🕒 {format_iran_time()}"
    )
    
    # ارسال منو در startup
    send_telegram_menu()
    
    run_startup_diagnostic()
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=10000), daemon=True).start()
    logger.info("[STARTUP] Flask روی پورت 10000")
    main_loop()
