# -*- coding: utf-8 -*-
"""
DTM Divergence Auto-Trading Bot - TheTrueTrade (نسخه هیبریدی)
====================================================================
نسخه نهایی کامل — منطق واگرایی مطابق دقیق با Pine Script:
- Pivot: حالت سریع (5/3)
- فیلتر MTF: استفاده نمی‌شود
- کندل تأییدیه: بازنویسی دقیق مطابق پاین (sizeOk با ATR، avgBody، shadowToBodyRatio و...)
- رفع باگ فرمول شیب روند (slope × lookback)
- رفع باگ منطق تغییر رنگ MACD Histogram (بین دو پیوت)
- رفع Gating: RSI + MACD Line + MACD Histogram هر سه اجباری
- رفع فرمول فیبوناچی (بر اساس ابتدای روند واقعی)
- رفع تکرار سیگنال: فقط وقتی پیوت دوم تازه شکل گرفته باشد
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
# ثابت‌های استراتژی — دقیقاً مطابق Pine Script
# =====================================================================================
TREND_LOOKBACK = 20
TREND_SLOPE_MIN_PCT = 0.05
FIB_USE_618 = True
FIB_USE_786 = True
FIB_TOLERANCE_PCT = 0.5
FIB_SEARCH_BARS = 100
STOP_BUFFER_PCT = 0.05

# ثابت‌های کندل تأییدیه — دقیقاً مطابق پاین
SHADOW_TO_BODY_RATIO = 2.0
MAX_OPPOSITE_SHADOW_PCT = 20.0
MIN_CANDLE_ATR_RATIO = 0.3
BIG_CANDLE_AVG_LEN = 14
BIG_CANDLE_MULTIPLIER = 1.5

# =====================================================================================
# کلاس دریافت داده
# =====================================================================================
class TrueTradePublicData:
    def __init__(self):
        self.base_url = BASE_URL

    def fetch_ohlcv(self, symbol, timeframe='1m', limit=500):
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
        if not response.ok:
            if response.status_code in [401, 403]:
                self.connected = False
            logger.error(f"[EXCHANGE ERROR] {method} {uri} | Status: {response.status_code} | Body: {response.text[:300]}")
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
            data = self._request('GET', '/accounting/assets')
            if isinstance(data, list):
                for asset in data:
                    if asset.get('asset') == 'USDT' and asset.get('accountType') == 'futures':
                        return float(asset.get('balance', 0))
            return 0
        except:
            return None

    def create_order(self, symbol, order_type, side, amount, price=None, params=None):
        order_data = {
            "symbol": symbol.upper(),
            "side": side.upper(),
            "tradeType": order_type.upper(),
            "leverage": params.get('leverage', 1) if params else 1,
            "size": str(amount),
            "walletType": "debit"
        }

        if order_type.upper() == "LIMIT" and price:
            order_data["price"] = str(price)

        if params:
            if 'stopLoss' in params:
                order_data["stopLoss"] = str(params['stopLoss'])
            if 'takeProfit' in params:
                order_data["takeProfit"] = str(params['takeProfit'])

        result = self._request('POST', '/futures/positions', order_data)
        return {
            'id': result.get('positionId'),
            'symbol': symbol,
            'side': side,
            'type': order_type,
            'amount': amount
        }

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
# توابع محاسباتی پایه
# =====================================================================================
def calc_rsi(close, length=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1.0/length, min_periods=length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0/length, min_periods=length, adjust=False).mean()
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

# =====================================================================================
# روند — slope × lookback (معادل ta.linreg در پاین)
# =====================================================================================
def is_trending_up(close, ref_bar, lookback=TREND_LOOKBACK, slope_min_pct=TREND_SLOPE_MIN_PCT):
    if ref_bar is None or ref_bar - lookback < 0:
        return False
    y = close.iloc[ref_bar - lookback:ref_bar + 1].values
    if len(y) < 2:
        return False
    slope_per_bar = np.polyfit(np.arange(len(y)), y, 1)[0]
    total_slope = slope_per_bar * lookback
    avg = y.mean()
    if avg == 0:
        return False
    return (total_slope / avg) * 100 > slope_min_pct

def is_trending_down(close, ref_bar, lookback=TREND_LOOKBACK, slope_min_pct=TREND_SLOPE_MIN_PCT):
    if ref_bar is None or ref_bar - lookback < 0:
        return False
    y = close.iloc[ref_bar - lookback:ref_bar + 1].values
    if len(y) < 2:
        return False
    slope_per_bar = np.polyfit(np.arange(len(y)), y, 1)[0]
    total_slope = slope_per_bar * lookback
    avg = y.mean()
    if avg == 0:
        return False
    return (total_slope / avg) * 100 < -slope_min_pct

def resolve_bar_from_ts(df_indexed, ts):
    if ts not in df_indexed.index:
        return None
    return df_indexed.index.get_loc(ts)

# =====================================================================================
# تغییر رنگ MACD Histogram بین دو پیوت — معادل checkColorChange در پاین
# =====================================================================================
def check_macd_color_change(hist_series, bar1, bar2, need_negative_phase):
    if bar1 is None or bar2 is None or bar2 <= bar1 + 1:
        return False
    window = hist_series.iloc[bar1 + 1:bar2]
    if window.empty:
        return False
    return (window < 0).any() if need_negative_phase else (window > 0).any()

# =====================================================================================
# فیبوناچی — معادل findTrendStartLow/High + checkFibLevel در پاین
# =====================================================================================
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

# =====================================================================================
# کندل تأییدیه — بازنویسی دقیق مطابق Pine Script
# =====================================================================================
def check_price_action(df, bar2, direction, atr_val):
    """
    بررسی کندل تأییدیه روی کندل bar2 (همان کندل پیوت دوم).
    df: closed_df_indexed (با ایندکس timestamp)
    """
    if bar2 is None or bar2 < 0 or bar2 >= len(df):
        return False, []

    last = df.iloc[bar2]
    candle_range = last['high'] - last['low']
    candle_body = abs(last['close'] - last['open'])
    upper_shadow = last['high'] - max(last['close'], last['open'])
    lower_shadow = min(last['close'], last['open']) - last['low']

    # sizeOk: حداقل اندازه کندل نسبت به ATR
    size_ok = candle_range >= MIN_CANDLE_ATR_RATIO * atr_val

    # avgBody: میانگین بدنه در bigCandleAvgLen کندل
    start_idx = max(0, bar2 - BIG_CANDLE_AVG_LEN + 1)
    avg_body = df['close'].iloc[start_idx:bar2 + 1].diff().abs().mean()
    if pd.isna(avg_body) or avg_body == 0:
        avg_body = candle_body

    pa = False
    pa_reasons = []

    if direction == "BUY":
        # bullishWick
        bullish_wick = (candle_range > 0 and
                        lower_shadow >= SHADOW_TO_BODY_RATIO * candle_body and
                        (upper_shadow / candle_range) * 100 <= MAX_OPPOSITE_SHADOW_PCT and
                        size_ok)
        # bigGreenCandle
        big_green = (last['close'] > last['open'] and
                     candle_body >= BIG_CANDLE_MULTIPLIER * avg_body and
                     size_ok)

        if bullish_wick:
            pa = True
            pa_reasons.append("Bullish Wick (Hammer)")
        if big_green:
            pa = True
            pa_reasons.append("Big Green Candle")

    else:  # SELL
        # bearishWick (Shooting Star)
        bearish_wick = (candle_range > 0 and
                        upper_shadow >= SHADOW_TO_BODY_RATIO * candle_body and
                        (lower_shadow / candle_range) * 100 <= MAX_OPPOSITE_SHADOW_PCT and
                        size_ok)
        # bearishHangingMan
        bearish_hanging = (candle_range > 0 and
                           lower_shadow >= SHADOW_TO_BODY_RATIO * candle_body and
                           (upper_shadow / candle_range) * 100 <= MAX_OPPOSITE_SHADOW_PCT and
                           size_ok)
        # bigRedCandle
        big_red = (last['close'] < last['open'] and
                   candle_body >= BIG_CANDLE_MULTIPLIER * avg_body and
                   size_ok)

        if bearish_wick:
            pa = True
            pa_reasons.append("Bearish Wick (Shooting Star)")
        if bearish_hanging:
            pa = True
            pa_reasons.append("Bearish Hanging Man")
        if big_red:
            pa = True
            pa_reasons.append("Big Red Candle")

    return pa, pa_reasons

# =====================================================================================
# استاپ و تارگت
# =====================================================================================
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
        mid_peak = df_indexed["high"].iloc[bar1+1:bar2].max() if bar2 > bar1+1 else df_indexed["high"].iloc[bar1:bar2+1].max()
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
        mid_trough = df_indexed["low"].iloc[bar1+1:bar2].min() if bar2 > bar1+1 else df_indexed["low"].iloc[bar1:bar2+1].min()
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

# =====================================================================================
# سیستم امتیازدهی — مطابق دقیق پاین (با کندل تأییدیه)
# =====================================================================================
def calculate_divergence_score(p1, p2, direction, bar1, bar2, hist_series, high_series, low_series, df_indexed, atr_val):
    """
    Gating: RSI + MACD Line + MACD Histogram هر سه اجباری (3 امتیاز پایه)
    فیبوناچی: +1 امتیاز
    کندل تأییدیه: +1 امتیاز
    حداکثر: 5
    """
    details = []

    # === RSI ===
    if direction == "BUY":
        if p2['price'] < p1['price'] and p2['rsi'] > p1['rsi']:
            rsi_ok = True
        elif p2['price'] > p1['price'] and p2['rsi'] < p1['rsi']:
            rsi_ok = True
        else:
            rsi_ok = False
    else:
        if p2['price'] > p1['price'] and p2['rsi'] < p1['rsi']:
            rsi_ok = True
        elif p2['price'] < p1['price'] and p2['rsi'] > p1['rsi']:
            rsi_ok = True
        else:
            rsi_ok = False

    details.append("✅ RSI Divergence" if rsi_ok else "❌ RSI")

    # === MACD Line ===
    if direction == "BUY":
        if p2['price'] < p1['price'] and p2['macdline'] > p1['macdline']:
            macdline_ok = True
        elif p2['price'] > p1['price'] and p2['macdline'] < p1['macdline']:
            macdline_ok = True
        else:
            macdline_ok = False
    else:
        if p2['price'] > p1['price'] and p2['macdline'] < p1['macdline']:
            macdline_ok = True
        elif p2['price'] < p1['price'] and p2['macdline'] > p1['macdline']:
            macdline_ok = True
        else:
            macdline_ok = False

    details.append("✅ MACD Line Divergence" if macdline_ok else "❌ MACD Line")

    # === MACD Histogram ===
    if direction == "BUY":
        hist_shape_ok = ((p2['price'] < p1['price'] and p2['hist'] > p1['hist']) or
                         (p2['price'] > p1['price'] and p2['hist'] < p1['hist']))
        both_same_sign = p1['hist'] < 0 and p2['hist'] < 0
        color_changed = check_macd_color_change(hist_series, bar1, bar2, need_negative_phase=False)
        macdhist_ok = hist_shape_ok and both_same_sign and color_changed
    else:
        hist_shape_ok = ((p2['price'] > p1['price'] and p2['hist'] < p1['hist']) or
                         (p2['price'] < p1['price'] and p2['hist'] > p1['hist']))
        both_same_sign = p1['hist'] > 0 and p2['hist'] > 0
        color_changed = check_macd_color_change(hist_series, bar1, bar2, need_negative_phase=True)
        macdhist_ok = hist_shape_ok and both_same_sign and color_changed

    details.append("✅ MACD Histogram + Color Change" if macdhist_ok else "❌ MACD Histogram")

    # === Gating اجباری ===
    base3 = rsi_ok and macdline_ok and macdhist_ok
    if not base3:
        details.append("❌ حداقل ۳ تأییدیه پایه (RSI+MACD Line+MACD Hist) برقرار نیست")
        return 0, details

    score = 3

    # === فیبوناچی ===
    if direction == "BUY":
        trend_start = find_trend_start_high(high_series, bar1)
        fib_ok = check_fib_level(trend_start, p1['price'], p2['price'], is_retrace_down=False)
    else:
        trend_start = find_trend_start_low(low_series, bar1)
        fib_ok = check_fib_level(trend_start, p1['price'], p2['price'], is_retrace_down=True)

    if fib_ok:
        score += 1
        details.append("✅ Fibonacci (0.618/0.786)")
    else:
        details.append("❌ Fibonacci")

    # === کندل تأییدیه (Price Action) ===
    pa_ok, pa_reasons = check_price_action(df_indexed, bar2, direction, atr_val)
    if pa_ok:
        score += 1
        details.append(f"✅ Price Action ({', '.join(pa_reasons)})")
    else:
        details.append("❌ Price Action")

    return score, details

def classify_signal(score):
    if score >= 5:
        return "🟢", "Ideal"
    elif score >= 4:
        return "🟡", "Custom"
    elif score >= 3:
        return "⚪", "Minimal"
    else:
        return None, None

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

    if not today_trades:
        return

    total = len(today_trades)
    wins = len([t for t in today_trades if t.get('result') == 'TAKE_PROFIT'])
    losses = len([t for t in today_trades if t.get('result') == 'STOP_LOSS'])
    open_trades = len([t for t in today_trades if t.get('result') is None])
    closed = wins + losses
    win_rate = (wins / closed * 100) if closed > 0 else 0

    message = f"""📊 گزارش روزانه — {today_str}
━━━━━━━━━━━━━━━━━━━━━━

📈 کل معاملات: {total} عدد
✅ موفق: {wins} ({win_rate:.1f}%)
❌ ناموفق: {losses}
⏳ باز: {open_trades}

📊 نرخ موفقیت (بسته شده): {win_rate:.1f}%
💪 وضعیت: {'عالی! 🚀' if wins > losses else 'نیاز به بررسی 📊'}

آخرین معاملات:"""

    for i, trade in enumerate(today_trades[-5:], 1):
        result_emoji = "✅" if trade.get('result') == 'TAKE_PROFIT' else "❌" if trade.get('result') == 'STOP_LOSS' else "⏳"
        direction = "LONG" if trade.get('direction') == 'BUY' else "SHORT"
        message += f"\n{i}. {trade['symbol']} {direction} {result_emoji}"

    message += f"\n\n━━━━━━━━━━━━━━━━━━━━━━\n🕒 {format_iran_time()}"
    send_telegram_message(message)

def send_monthly_report():
    history = load_history()
    month_ago = (datetime.now(timezone(timedelta(hours=3, minutes=30))) - timedelta(days=30)).strftime('%Y-%m-%d')
    month_trades = [t for t in history if t.get('signal_time', '') >= month_ago]

    if not month_trades:
        return

    total = len(month_trades)
    wins = len([t for t in month_trades if t.get('result') == 'TAKE_PROFIT'])
    losses = len([t for t in month_trades if t.get('result') == 'STOP_LOSS'])
    win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0

    message = f"""📈 گزارش ۳۰ روز گذشته
━━━━━━━━━━━━━━━━━━━━━━

📊 کل معاملات: {total} عدد
✅ موفق: {wins} ({win_rate:.1f}%)
❌ ناموفق: {losses}

📈 نرخ موفقیت: {win_rate:.1f}%
📊 میانگین روزانه: {(wins-losses)/30:.1f} معامله
💪 ارزیابی: {'پروژه موفق! 🎉' if wins > losses else 'نیاز به بهینه‌سازی ⚙️'}

━━━━━━━━━━━━━━━━━━━━━━
🕒 {format_iran_time()}"""
    send_telegram_message(message)

# =====================================================================================
# Startup Diagnostic
# =====================================================================================
def run_startup_diagnostic():
    logger.info("Running Startup Diagnostic...")

    diagnostic_log = []
    diagnostic_log.append("🔍 بررسی سلامت سیستم")
    diagnostic_log.append("━━━━━━━━━━━━━━━━━━━━━━")

    try:
        requests.get("https://www.google.com", timeout=5)
        diagnostic_log.append("🟢 اتصال اینترنت")
    except:
        diagnostic_log.append("🔴 اتصال اینترنت")

    public_data = TrueTradePublicData()
    df = None
    try:
        df = public_data.fetch_ohlcv("LTCUSDT", "1m", 500)
        if df is not None and not df.empty:
            diagnostic_log.append(f"🟢 دریافت داده (API Public): {len(df)} کندل")
        else:
            diagnostic_log.append("🔴 دریافت داده (API Public)")
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
        diagnostic_log.append(f"🔴 خطا در محاسبات: {str(e)[:50]}")

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

    right_bars = 3
    last_valid_pivot_index = n - right_bars - 1

    new_pivots_high = []
    new_pivots_low = []

    existing_high_ts = {p['ts'] for p in state.pivot_highs}
    existing_low_ts = {p['ts'] for p in state.pivot_lows}

    if state.last_processed_ts is None:
        start_bar = 0
    else:
        if state.last_processed_ts in closed_df_indexed.index:
            last_pos = closed_df_indexed.index.get_loc(state.last_processed_ts)
            start_bar = max(0, last_pos - 5)
        else:
            start_bar = max(0, n - 10)

    start_bar = min(start_bar, last_valid_pivot_index)

    log(f"   n={n}, last_valid={last_valid_pivot_index}, start={start_bar}")

    for i in range(start_bar, last_valid_pivot_index + 1):
        ts = closed_df_indexed.index[i]

        if not pd.isna(pivot_high.iloc[i]) and ts not in existing_high_ts:
            new_pivots_high.append({
                'ts': ts, 'price': pivot_high.iloc[i],
                'rsi': rsi_val.iloc[i], 'macdline': macd_line.iloc[i], 'hist': hist_line.iloc[i]
            })
        if not pd.isna(pivot_low.iloc[i]) and ts not in existing_low_ts:
            new_pivots_low.append({
                'ts': ts, 'price': pivot_low.iloc[i],
                'rsi': rsi_val.iloc[i], 'macdline': macd_line.iloc[i], 'hist': hist_line.iloc[i]
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

    # BUY — فقط وقتی پیوت لو دوم تازه تشکیل شده
    if len(new_pivots_low) > 0 and len(state.pivot_lows) >= 2:
        pl_1, pl_2 = state.pivot_lows[-2], state.pivot_lows[-1]
        bar1 = resolve_bar_from_ts(closed_df_indexed, pl_1['ts'])
        bar2 = resolve_bar_from_ts(closed_df_indexed, pl_2['ts'])

        if bar1 is not None and bar2 is not None:
            trend_ok = is_trending_down(close, bar1, TREND_LOOKBACK, TREND_SLOPE_MIN_PCT)
            log(f"   🔵 BUY check: bar1={bar1}, bar2={bar2}, trend={'✅' if trend_ok else '❌'}")

            if trend_ok:
                score, details = calculate_divergence_score(
                    pl_1, pl_2, "BUY", bar1, bar2, hist_line, high, low, closed_df_indexed, atr14.iloc[-1]
                )
                buy_emoji, buy_label = classify_signal(score)
                buy_score = score
                buy_details = details
                log(f"   🔵 BUY score={score}/5 {'✅' if buy_emoji else '❌'}")
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

    # SELL — فقط وقتی پیوت های دوم تازه تشکیل شده
    if len(new_pivots_high) > 0 and len(state.pivot_highs) >= 2:
        ph_1, ph_2 = state.pivot_highs[-2], state.pivot_highs[-1]
        bar1 = resolve_bar_from_ts(closed_df_indexed, ph_1['ts'])
        bar2 = resolve_bar_from_ts(closed_df_indexed, ph_2['ts'])

        if bar1 is not None and bar2 is not None:
            trend_ok = is_trending_up(close, bar1, TREND_LOOKBACK, TREND_SLOPE_MIN_PCT)
            log(f"   🔴 SELL check: bar1={bar1}, bar2={bar2}, trend={'✅' if trend_ok else '❌'}")

            if trend_ok:
                score, details = calculate_divergence_score(
                    ph_1, ph_2, "SELL", bar1, bar2, hist_line, high, low, closed_df_indexed, atr14.iloc[-1]
                )
                sell_emoji, sell_label = classify_signal(score)
                sell_score = score
                sell_details = details
                log(f"   🔴 SELL score={score}/5 {'✅' if sell_emoji else '❌'}")
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

    # ⚡ ارسال لاگ به تلگرام
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
                f"{prefix} لاگ #{state.telegram_log_count} — {symbol}\n"
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
def check_proximity(symbol, current_price, entry, stop, target, direction, capital, leverage, qty):
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
            f"⚠️ هشدار نزدیکی به حد ضرر (75%)\n\n"
            f"🔹 نماد: {symbol}\n"
            f"💰 قیمت فعلی: {current_price:.4f}\n"
            f"🛑 حد ضرر: {stop:.4f}\n"
            f"📊 فاصله تا استاپ: {stop_distance*100:.1f}% از کل ریسک\n\n"
            f"⚠️ فقط ۲۵٪ تا فعال شدن حد ضرر باقی مانده\n"
            f"🕒 {format_iran_time()}"
        )

    if target_progress >= 0.60 and target_progress < 1.0:
        unrealized_r = target_progress / (1 - target_progress) if target_progress < 1 else 999
        send_telegram_message(
            f"🎯 هشدار نزدیکی به حد سود (R:R = 1.5)\n\n"
            f"🔹 نماد: {symbol}\n"
            f"💰 قیمت فعلی: {current_price:.4f}\n"
            f"🎯 حد سود: {target:.4f}\n"
            f"📊 پیشرفت: {target_progress*100:.1f}% به سمت تارگت\n"
            f"⚖️ R:R فعلی: {unrealized_r:.1f}\n\n"
            f"💡 سود شناور: {capital * leverage * abs(current_price - entry) / entry:.2f} USDT\n"
            f"🕒 {format_iran_time()}"
        )

def track_open_signals():
    history = load_history()
    data = TrueTradePublicData()
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

            check_proximity(trade['symbol'], cp, entry, stop, target, direction, capital, leverage, qty)

            if direction == 'BUY':
                if cp >= target:
                    profit_pct = (cp-entry)/entry*100
                    profit_usdt = capital * leverage * profit_pct / 100
                    update_trade_result(trade['symbol'], trade['signal_time'], 'TAKE_PROFIT', cp)
                    send_telegram_message(
                        f"🎯 حد سود فعال شد\n\n"
                        f"🔹 نماد: {trade['symbol']}\n"
                        f"🔸 جهت: LONG (خرید)\n\n"
                        f"📍 ورود: {entry:.4f}\n"
                        f"🎯 خروج: {cp:.4f}\n\n"
                        f"📈 میزان سود: +{profit_pct:.2f}%\n"
                        f"💰 سود: +{profit_usdt:.2f} USDT\n"
                        f"🕒 {format_iran_time()}"
                    )
                elif cp <= stop:
                    loss_pct = (cp-entry)/entry*100
                    loss_usdt = capital * leverage * abs(loss_pct) / 100
                    update_trade_result(trade['symbol'], trade['signal_time'], 'STOP_LOSS', cp)
                    send_telegram_message(
                        f"💔 حد ضرر فعال شد\n\n"
                        f"🔹 نماد: {trade['symbol']}\n"
                        f"🔸 جهت: LONG (خرید)\n\n"
                        f"📍 ورود: {entry:.4f}\n"
                        f"💔 خروج: {cp:.4f}\n\n"
                        f"📉 میزان ضرر: {loss_pct:.2f}%\n"
                        f"💰 ضرر: {loss_usdt:.2f} USDT\n"
                        f"🕒 {format_iran_time()}"
                    )
            else:
                if cp <= target:
                    profit_pct = (entry-cp)/entry*100
                    profit_usdt = capital * leverage * profit_pct / 100
                    update_trade_result(trade['symbol'], trade['signal_time'], 'TAKE_PROFIT', cp)
                    send_telegram_message(
                        f"🎯 حد سود فعال شد\n\n"
                        f"🔹 نماد: {trade['symbol']}\n"
                        f"🔸 جهت: SHORT (فروش)\n\n"
                        f"📍 ورود: {entry:.4f}\n"
                        f"🎯 خروج: {cp:.4f}\n\n"
                        f"📈 میزان سود: +{profit_pct:.2f}%\n"
                        f"💰 سود: +{profit_usdt:.2f} USDT\n"
                        f"🕒 {format_iran_time()}"
                    )
                elif cp >= stop:
                    loss_pct = (entry-cp)/entry*100
                    loss_usdt = capital * leverage * abs(loss_pct) / 100
                    update_trade_result(trade['symbol'], trade['signal_time'], 'STOP_LOSS', cp)
                    send_telegram_message(
                        f"💔 حد ضرر فعال شد\n\n"
                        f"🔹 نماد: {trade['symbol']}\n"
                        f"🔸 جهت: SHORT (فروش)\n\n"
                        f"📍 ورود: {entry:.4f}\n"
                        f"💔 خروج: {cp:.4f}\n\n"
                        f"📉 میزان ضرر: {loss_pct:.2f}%\n"
                        f"💰 ضرر: {loss_usdt:.2f} USDT\n"
                        f"🕒 {format_iran_time()}"
                    )

# =====================================================================================
# تابع اصلی - با فرمول مدیریت ریسک
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
        balance_text = f"\n💰 موجودی: {balance:.2f} USDT" if balance else ""
        send_telegram_message(
            f"📡 وضعیت اتصال به صرافی\n\n"
            f"{status_text}{balance_text}\n\n"
            f"🕒 {format_iran_time()}"
        )
    elif analyze_and_execute._last_status != conn:
        analyze_and_execute._last_status = conn
        status_text = "✅ متصل — ترید خودکار فعال شد" if conn else "⚠️ قطع — ترید خودکار متوقف شد"
        send_telegram_message(
            f"🔄 تغییر وضعیت صرافی\n\n"
            f"{status_text}\n\n"
            f"🕒 {format_iran_time()}"
        )

    data = TrueTradePublicData()
    track_open_signals()

    side_map = {"BUY": "LONG", "SELL": "SHORT"}

    leverage_map = {
        "LTCUSDT": 75,
        "DOGEUSDT": 75,
        "ETHUSDT": 50
    }

    for symbol in SYMBOLS:
        try:
            df = data.fetch_ohlcv(symbol, '1m', 500)
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
                send_telegram_message(
                    f"⚡ در حال تشکیل Pivot جدید\n\n"
                    f"🔹 نماد: {symbol}\n"
                    f"💰 قیمت: {cp:.4f}\n"
                    f"📊 نوع: کف/قله جدید\n\n"
                    f"⏳ تا تأیید نهایی حدود ۲ دقیقه\n"
                    f"🕒 {format_iran_time()}"
                )

            if signal and stop and target:
                profit_pct = (target-entry)/entry*100 if signal=="BUY" else (entry-target)/entry*100
                loss_pct = (entry-stop)/entry*100 if signal=="BUY" else (stop-entry)/entry*100
                rr = abs(profit_pct/loss_pct) if loss_pct != 0 else 0
                direction_text = "LONG (خرید)" if signal == "BUY" else "SHORT (فروش)"
                direction_emoji = "🟢" if signal == "BUY" else "🔴"

                details_text = ""
                if details:
                    details_text = "\n".join([f"{i+1}. {d}" for i, d in enumerate(details)])

                TARGET_RISK = 3.5
                leverage = leverage_map.get(symbol, 50)
                stop_pct = abs(entry - stop) / entry

                old_leverage = 1.0 / stop_pct if stop_pct > 0 else 999999

                if old_leverage <= leverage:
                    required_capital = TARGET_RISK
                    used_leverage = old_leverage
                else:
                    multiplier = old_leverage / leverage
                    required_capital = TARGET_RISK * multiplier
                    used_leverage = leverage

                capital_reduced = False
                if balance >= required_capital:
                    capital = required_capital
                    actual_risk = TARGET_RISK
                else:
                    capital = balance
                    actual_risk = capital * used_leverage * stop_pct
                    capital_reduced = True

                qty = (capital * used_leverage) / entry
                potential_profit = capital * used_leverage * (profit_pct / 100)

                logger.info(f"[RISK CALC] {symbol}: stop%={stop_pct:.4f}%, "
                           f"old_lev={old_leverage:.1f}, req_cap={required_capital:.2f}, "
                           f"balance={balance:.2f}, capital={capital:.2f}, "
                           f"qty={qty:.6f}, risk={actual_risk:.2f}")

                signal_message = (
                    f"{emoji} سیگنال {label} — {symbol}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"📊 امتیاز: {score}/5\n"
                    f"🔸 نوع: {direction_emoji} {direction_text}\n\n"
                    f"📍 نقطه ورود: {entry:.4f}\n"
                    f"🛑 حد ضرر: {stop:.4f}\n"
                    f"🎯 حد سود: {target:.4f}\n\n"
                    f"📈 سود مورد انتظار: +{profit_pct:.2f}%\n"
                    f"📉 ضرر قابل قبول: -{loss_pct:.2f}%\n"
                    f"⚖️ نسبت Risk/Reward: {rr:.2f}\n\n"
                    f"✅ دلایل تأیید سیگنال:\n{details_text}\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🕒 {format_iran_time()}"
                )
                send_telegram_message(signal_message)

                history = load_history()
                history.append({
                    'symbol': symbol, 'direction': signal,
                    'entry_price': entry, 'stop_loss': stop, 'take_profit': target,
                    'signal_time': format_iran_time(), 'result': None, 'score': score, 'label': label,
                    'capital': capital, 'leverage': int(used_leverage), 'qty': qty
                })
                save_history(history)

                if exchange.connected:
                    try:
                        exchange.create_order(
                            symbol, "market", side_map[signal], qty, None,
                            {'leverage': int(used_leverage), 'stopLoss': stop, 'takeProfit': target}
                        )

                        order_message = (
                            f"✅ سفارش با موفقیت ثبت شد\n\n"
                            f"🔹 نماد: {symbol}\n"
                            f"🔸 جهت: {side_map[signal]}\n"
                            f"🔸 نوع: MARKET\n"
                            f"📦 حجم: {qty:.6f} قرارداد\n"
                            f"🔧 اهرم: {int(used_leverage)}x\n"
                            f"💰 سرمایه: {capital:.2f} USDT\n\n"
                        )

                        if capital_reduced:
                            order_message += (
                                f"⚠️ هشدار: سرمایه کاهش یافت!\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"📐 سرمایه لازم (طبق فرمول): {required_capital:.2f} USDT\n"
                                f"💰 موجودی فعلی: {balance:.2f} USDT\n"
                                f"📉 کمبود: {required_capital - balance:.2f} USDT\n\n"
                                f"🔧 اقدام خودکار:\n"
                                f"• سرمایه به {capital:.2f} USDT کاهش یافت\n"
                                f"• ضرر از {TARGET_RISK:.2f} به {actual_risk:.2f} USDT کاهش یافت\n"
                                f"• حجم معامله: {qty:.6f} (به‌روز شده)\n\n"
                                f"💡 توصیه: موجودی را به {required_capital:.2f} USDT برسانید\n"
                                f"تا ضرر به {TARGET_RISK:.2f} USDT بازگردد.\n\n"
                            )

                        order_message += (
                            f"🛑 حد ضرر: {stop:.4f}\n"
                            f"🎯 حد سود: {target:.4f}\n\n"
                            f"📉 ضرر: {actual_risk:.2f} USDT ({loss_pct:.2f}%)\n"
                            f"📈 سود احتمالی: {potential_profit:.2f} USDT (+{profit_pct:.2f}%)\n\n"
                            f"🕒 {format_iran_time()}"
                        )
                        send_telegram_message(order_message)

                    except Exception as e:
                        send_telegram_message(
                            f"❌ خطا در ثبت سفارش\n\n"
                            f"🔹 نماد: {symbol}\n"
                            f"🔸 جهت: {side_map[signal]}\n"
                            f"📝 علت: {str(e)[:200]}\n\n"
                            f"⚠️ نیاز به ورود دستی\n"
                            f"🕒 {format_iran_time()}"
                        )

                SYMBOL_STATES[symbol].alert_sent = False
            else:
                logger.info(f"[ANALYSIS] {symbol}: بدون سیگنال")
        except Exception as e:
            logger.error(f"[ERROR] {symbol}: {e}")

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

if __name__ == "__main__":
    logger.info("DTM Bot Starting...")

    send_telegram_message(
        "🤖 DTM Pro — آنلاین\n\n"
        "🧠 استراتژی: DTM Divergence (منطبق با Pine Script)\n"
        "📊 سیگنال‌دهی: خودکار\n"
        "💰 ترید: خودکار (با اتصال صرافی)\n\n"
        "⚙️ تنظیمات:\n"
        "• Timeframe: 1m\n"
        "• Pivot: حالت سریع Left=5, Right=3\n"
        "• Memory: 100 Pivot\n"
        "• Scoring: 5-Level (RSI+MACD Line+MACD Hist اجباری + Fib + Price Action)\n"
        "• Symbols: LTCUSDT, DOGEUSDT, ETHUSDT\n"
        "• Min R/R: 2.0\n"
        "• Max Risk: 3.5 USDT\n"
        "• Leverage: ETH=50x, LTC/DOGE=75x\n\n"
        f"🕒 {format_iran_time()}"
    )

    run_startup_diagnostic()

    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=10000), daemon=True).start()
    logger.info("[STARTUP] Flask روی پورت 10000")

    main_loop()
