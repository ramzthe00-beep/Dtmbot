# -*- coding: utf-8 -*-
"""
DTM Divergence Auto-Trading Bot - TheTrueTrade (نسخه هیبریدی)
====================================================================
نسخه اصلاح شده نهایی:
- رفع باگ ایندکس ناپایدار pivot با timestamp
- رفع باگ side (BUY/SELL -> LONG/SHORT مطابق مستندات API)
- رفع باگ cost/size (تعداد قرارداد باید در فیلد size ارسال شود، نه cost)
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

    def create_order(self, symbol, order_type, side, amount, price=None, params=None):
        """
        side باید دقیقا "LONG" یا "SHORT" باشد (طبق مستندات API)
        amount = تعداد قرارداد (contracts) و همیشه در فیلد size فرستاده می‌شود
        """
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

# =====================================================================================
# توابع محاسباتی
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

def is_trending_up(close, ref_bar, lookback=20, slope_min_pct=0.05):
    if ref_bar is None or ref_bar - lookback < 0:
        return False
    y = close.iloc[ref_bar - lookback:ref_bar + 1].values
    if len(y) < 2:
        return False
    slope = np.polyfit(np.arange(len(y)), y, 1)[0]
    avg = y.mean()
    return (slope / avg) * 100 > slope_min_pct if avg != 0 else False

def is_trending_down(close, ref_bar, lookback=20, slope_min_pct=0.05):
    if ref_bar is None or ref_bar - lookback < 0:
        return False
    y = close.iloc[ref_bar - lookback:ref_bar + 1].values
    if len(y) < 2:
        return False
    slope = np.polyfit(np.arange(len(y)), y, 1)[0]
    avg = y.mean()
    return (slope / avg) * 100 < -slope_min_pct if avg != 0 else False

def resolve_bar_from_ts(df_indexed, ts):
    """پیدا کردن موقعیت عددی یک کندل بر اساس timestamp در DataFrame فعلی"""
    if ts not in df_indexed.index:
        return None
    return df_indexed.index.get_loc(ts)

def compute_stop_and_targets(pivot_highs, pivot_lows, direction, df_indexed, atr_val, stop_buffer_pct=0.05):
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
# سیستم امتیازدهی
# =====================================================================================
def calculate_divergence_score(p1, p2, direction, df, current_price):
    score = 0
    details = []

    # RSI
    if direction == "BUY":
        if p2['price'] < p1['price'] and p2['rsi'] > p1['rsi']:
            score += 1; details.append("✅ RSI")
        elif p2['price'] > p1['price'] and p2['rsi'] < p1['rsi']:
            score += 1; details.append("✅ RSI Hidden")
        else:
            details.append("❌ RSI")
    else:
        if p2['price'] > p1['price'] and p2['rsi'] < p1['rsi']:
            score += 1; details.append("✅ RSI")
        elif p2['price'] < p1['price'] and p2['rsi'] > p1['rsi']:
            score += 1; details.append("✅ RSI Hidden")
        else:
            details.append("❌ RSI")

    # MACD Line
    if direction == "BUY":
        if p2['price'] < p1['price'] and p2['macdline'] > p1['macdline']:
            score += 1; details.append("✅ MACD Line")
        elif p2['price'] > p1['price'] and p2['macdline'] < p1['macdline']:
            score += 1; details.append("✅ MACD Line Hidden")
        else:
            details.append("❌ MACD Line")
    else:
        if p2['price'] > p1['price'] and p2['macdline'] < p1['macdline']:
            score += 1; details.append("✅ MACD Line")
        elif p2['price'] < p1['price'] and p2['macdline'] > p1['macdline']:
            score += 1; details.append("✅ MACD Line Hidden")
        else:
            details.append("❌ MACD Line")

    # Histogram
    hist_div = False
    if direction == "BUY":
        if p2['price'] < p1['price'] and p2['hist'] > p1['hist']:
            hist_div = True
        elif p2['price'] > p1['price'] and p2['hist'] < p1['hist']:
            hist_div = True
    else:
        if p2['price'] > p1['price'] and p2['hist'] < p1['hist']:
            hist_div = True
        elif p2['price'] < p1['price'] and p2['hist'] > p1['hist']:
            hist_div = True

    color_changed = (p1['hist'] < 0 and p2['hist'] > 0) or (p1['hist'] > 0 and p2['hist'] < 0)
    if hist_div and color_changed:
        score += 1; details.append("✅ Histogram+Color")
    elif hist_div:
        details.append("⚠️ Histogram no color")
    else:
        details.append("❌ Histogram")

    # Fibonacci
    if len(df) > 20:
        h20, l20 = df['high'].iloc[-20:].max(), df['low'].iloc[-20:].min()
        if h20 != l20:
            f618 = l20 + 0.618*(h20-l20)
            f786 = l20 + 0.786*(h20-l20)
            if abs(current_price-f618)/f618 < 0.005 or abs(current_price-f786)/f786 < 0.005:
                score += 1; details.append("✅ Fibonacci")
            else:
                details.append("❌ Fibonacci")
        else:
            details.append("❌ Fibonacci")
    else:
        details.append("❌ Fibonacci")

    # Price Action
    if len(df) >= 3:
        last, prev = df.iloc[-1], df.iloc[-2]
        avg_range = (df['high']-df['low']).rolling(10).mean().iloc[-1]
        body = abs(last['close']-last['open'])
        upper_wick = last['high'] - max(last['open'], last['close'])
        lower_wick = min(last['open'], last['close']) - last['low']

        pa = False
        if (last['high']-last['low']) > avg_range*2:
            pa = True
        if direction == "BUY" and lower_wick > body*2 and upper_wick < body*0.5:
            pa = True
        if direction == "SELL" and upper_wick > body*2 and lower_wick < body*0.5:
            pa = True
        if direction == "BUY" and last['close']>last['open'] and prev['close']<prev['open'] and last['close']>prev['open'] and last['open']<prev['close']:
            pa = True
        if direction == "SELL" and last['close']<last['open'] and prev['close']>prev['open'] and last['close']<prev['open'] and last['open']>prev['close']:
            pa = True

        if pa:
            score += 1; details.append("✅ Price Action")
        else:
            details.append("❌ Price Action")
    else:
        details.append("❌ Price Action")

    return score, details

def classify_signal(score, details, direction):
    if score >= 5:
        return "🟢", "Ideal", score, details
    elif score >= 4:
        return "🟡", "Custom", score, details
    elif score >= 3:
        return "⚪", "Minimal", score, details
    else:
        return None, None, score, details

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
        return None, None, None, None, False, None, None, None

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
            send_telegram_message(f"{prefix} **Log #{state.telegram_log_count} - {symbol}**\n🕒 {format_iran_time()}\n```\n{telegram_debug[:1500]}\n```")
        except Exception as e:
            logger.error(f"[TELEGRAM] {e}")

    early_signal = len(new_pivots_high) > 0 or len(new_pivots_low) > 0
    entry_price = close.iloc[-1]
    current_price = df['close'].iloc[-1]

    buy_signal = sell_signal = None
    buy_emoji = sell_emoji = None
    buy_label = sell_label = None
    buy_score = sell_score = 0
    buy_stop = buy_target = sell_stop = sell_target = None

    # BUY
    if len(state.pivot_lows) >= 2:
        pl_1, pl_2 = state.pivot_lows[-2], state.pivot_lows[-1]
        bar1 = resolve_bar_from_ts(closed_df_indexed, pl_1['ts'])

        if bar1 is not None:
            if pl_2['price'] < pl_1['price'] or pl_2['price'] > pl_1['price']:
                trend_ok = is_trending_down(close, bar1, 20, 0.05)
                log(f"   🔵 BUY check: bar1={bar1}, trend={'✅' if trend_ok else '❌'}")

                if trend_ok:
                    score, details = calculate_divergence_score(pl_1, pl_2, "BUY", df, current_price)
                    buy_emoji, buy_label, buy_score, _ = classify_signal(score, details, "BUY")
                    log(f"   🔵 BUY score={score}/5 {'✅' if buy_emoji else '❌'}")

                    if buy_emoji and score >= 3:
                        stop, tp_raw, _ = compute_stop_and_targets(
                            state.pivot_highs, state.pivot_lows, "long", closed_df_indexed, atr14.iloc[-1]
                        )
                        if stop and tp_raw:
                            buy_stop, buy_target = stop, resolve_final_target(entry_price, stop, tp_raw, "long")
                            buy_signal = "BUY"
                            log(f"   Entry={entry_price:.4f}, SL={stop:.4f}, TP={buy_target:.4f}")
        else:
            log(f"   🔵 BUY: pl_1 ts not in current window")

    # SELL
    if len(state.pivot_highs) >= 2:
        ph_1, ph_2 = state.pivot_highs[-2], state.pivot_highs[-1]
        bar1 = resolve_bar_from_ts(closed_df_indexed, ph_1['ts'])

        if bar1 is not None:
            if ph_2['price'] > ph_1['price'] or ph_2['price'] < ph_1['price']:
                trend_ok = is_trending_up(close, bar1, 20, 0.05)
                log(f"   🔴 SELL check: bar1={bar1}, trend={'✅' if trend_ok else '❌'}")

                if trend_ok:
                    score, details = calculate_divergence_score(ph_1, ph_2, "SELL", df, current_price)
                    sell_emoji, sell_label, sell_score, _ = classify_signal(score, details, "SELL")
                    log(f"   🔴 SELL score={score}/5 {'✅' if sell_emoji else '❌'}")

                    if sell_emoji and score >= 3:
                        stop, tp_raw, _ = compute_stop_and_targets(
                            state.pivot_highs, state.pivot_lows, "short", closed_df_indexed, atr14.iloc[-1]
                        )
                        if stop and tp_raw:
                            sell_stop, sell_target = stop, resolve_final_target(entry_price, stop, tp_raw, "short")
                            sell_signal = "SELL"
                            log(f"   Entry={entry_price:.4f}, SL={stop:.4f}, TP={sell_target:.4f}")
        else:
            log(f"   🔴 SELL: ph_1 ts not in current window")

    if not buy_signal and not sell_signal:
        log(f"   ⚪ No signal")

    if buy_signal:
        return "BUY", entry_price, buy_stop, buy_target, early_signal, buy_emoji, buy_label, buy_score
    elif sell_signal:
        return "SELL", entry_price, sell_stop, sell_target, early_signal, sell_emoji, sell_label, sell_score
    return None, None, None, None, early_signal, None, None, None

# =====================================================================================
# پیگیری سیگنال‌های باز
# =====================================================================================
def track_open_signals():
    history = load_history()
    data = TrueTradePublicData()
    for trade in history:
        if trade.get('result') is None:
            df = data.fetch_ohlcv(trade['symbol'], '1m', 10)
            if df is None or df.empty:
                continue
            cp = df['close'].iloc[-1]
            entry, stop, target = trade['entry_price'], trade['stop_loss'], trade['take_profit']
            if trade['direction'] == 'BUY':
                if cp >= target:
                    update_trade_result(trade['symbol'], trade['signal_time'], 'TAKE_PROFIT', cp)
                    send_telegram_message(f"🎉 **تارگت!** {trade['symbol']} | سود: {(cp-entry)/entry*100:.2f}%")
                elif cp <= stop:
                    update_trade_result(trade['symbol'], trade['signal_time'], 'STOP_LOSS', cp)
                    send_telegram_message(f"💔 **استاپ!** {trade['symbol']}")
            else:
                if cp <= target:
                    update_trade_result(trade['symbol'], trade['signal_time'], 'TAKE_PROFIT', cp)
                    send_telegram_message(f"🎉 **تارگت!** {trade['symbol']}")
                elif cp >= stop:
                    update_trade_result(trade['symbol'], trade['signal_time'], 'STOP_LOSS', cp)
                    send_telegram_message(f"💔 **استاپ!** {trade['symbol']}")

# =====================================================================================
# تابع اصلی
# =====================================================================================
def analyze_and_execute():
    logger.info("[ANALYZE] شروع...")
    exchange = TrueTradePrivateExchange(API_KEY, API_SECRET, BASE_URL)
    conn = exchange.test_connection()

    if not hasattr(analyze_and_execute, "_last_status"):
        analyze_and_execute._last_status = conn
        send_telegram_message(f"📡 **صرافی:** {'✅ متصل' if conn else '⚠️ قطع'}\n🕒 {format_iran_time()}")
    elif analyze_and_execute._last_status != conn:
        analyze_and_execute._last_status = conn
        send_telegram_message(f"🔄 **تغییر وضعیت:** {'✅ متصل' if conn else '⚠️ قطع'}")

    data = TrueTradePublicData()
    track_open_signals()

    side_map = {"BUY": "LONG", "SELL": "SHORT"}

    for symbol in SYMBOLS:
        try:
            df = data.fetch_ohlcv(symbol, '1m', 500)
            if df is None or df.empty:
                logger.warning(f"[SKIP] {symbol}")
                continue

            logger.info(f"[DATA] {symbol}: {len(df)} کندل")

            signal, entry, stop, target, early, emoji, label, score = detect_signal(
                df, SYMBOL_STATES[symbol], symbol, debug=True
            )
            cp = df['close'].iloc[-1]

            if early and not SYMBOL_STATES[symbol].alert_sent:
                SYMBOL_STATES[symbol].alert_sent = True
                send_telegram_message(f"⚡ **آماده باش - {symbol}**\n💰 {cp:.4f}")

            if signal and stop and target:
                profit_pct = (target-entry)/entry*100 if signal=="BUY" else (entry-target)/entry*100
                loss_pct = (entry-stop)/entry*100 if signal=="BUY" else (stop-entry)/entry*100

                send_telegram_message(
                    f"{emoji} **سیگنال {label} - {symbol}**\n"
                    f"🔸 {'🟢 خرید' if signal=='BUY' else '🔴 فروش'} | امتیاز: {score}/5\n"
                    f"📍 ورود: {entry:.4f} | 🛑 ضرر: {stop:.4f} | 🎯 سود: {target:.4f}\n"
                    f"📈 سود: {profit_pct:.2f}% | 📉 ضرر: {loss_pct:.2f}%\n"
                    f"🕒 {format_iran_time()}"
                )

                history = load_history()
                history.append({
                    'symbol': symbol, 'direction': signal,
                    'entry_price': entry, 'stop_loss': stop, 'take_profit': target,
                    'signal_time': format_iran_time(), 'result': None, 'score': score, 'label': label
                })
                save_history(history)

                if exchange.connected:
                    try:
                        risk = abs(entry-stop)
                        if risk > 0:
                            qty = 3.5 / risk
                            exchange.create_order(
                                symbol, "market", side_map[signal], qty, None,
                                {'leverage': 10, 'stopLoss': stop, 'takeProfit': target}
                            )
                            send_telegram_message(f"✅ **معامله ثبت شد** {symbol} | حجم: {qty:.6f}")
                    except Exception as e:
                        send_telegram_message(f"❌ **خطا** {symbol}: {str(e)[:200]}")

                SYMBOL_STATES[symbol].alert_sent = False
            else:
                logger.info(f"[ANALYSIS] {symbol}: بدون سیگنال")
        except Exception as e:
            logger.error(f"[ERROR] {symbol}: {e}")

# =====================================================================================
# حلقه اصلی
# =====================================================================================
def main_loop():
    while True:
        try:
            logger.info(f"[LOOP] {format_iran_time()}")
            analyze_and_execute()
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
    send_telegram_message("🤖 **ربات DTM راه‌اندازی شد**\n✅ رفع باگ ایندکس ناپایدار\n✅ رفع باگ side (LONG/SHORT)\n✅ رفع باگ cost→size")
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=10000), daemon=True).start()
    main_loop()
