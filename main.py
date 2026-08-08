# -*- coding: utf-8 -*-
"""
DTM Divergence Auto-Trading Bot - TheTrueTrade (نسخه هیبریدی)
====================================================================
ربات معاملاتی هیبریدی با حافظه ۱۰۰ Pivot، سیستم امتیازدهی ۳ سطحی و Diagnostic کامل
نسخه اصلاح شده نهایی - رفع باگ fatal + lookback + logging + لاگ تضمینی
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
    handlers=[
        logging.StreamHandler()
    ]
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

# =====================================================================================
# فایل ذخیره تاریخچه معاملات
# =====================================================================================
HISTORY_FILE = "trades_history_hybrid.json"

# =====================================================================================
# کلاس دریافت داده عمومی
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
            logger.error(f"[PUBLIC FETCH ERROR] {symbol}: {e}")
            return None

# =====================================================================================
# کلاس صرافی خصوصی
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
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            payload.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature

    def _request(self, method, uri, data=None, symbol="N/A"):
        timestamp = str(int(time.time() * 1000))
        signature = self._sign_request(method, uri, timestamp)

        headers = {
            "X-API-Key": self.api_key,
            "X-Timestamp": timestamp,
            "X-Signature": signature,
            "Content-Type": "application/json"
        }

        url = f"{self.base_url}{uri}"
        response = self.session.request(method, url, headers=headers, json=data, timeout=15)
        
        if not response.ok:
            logger.error(f"[EXCHANGE ERROR] {symbol} | Status: {response.status_code}")
            if response.status_code in [401, 403]:
                self.connected = False
            response.raise_for_status()
        else:
            self.connected = True
        return response.json()

    def test_connection(self):
        try:
            self._request('GET', '/futures/positions')
            self.connected = True
            logger.info("[EXCHANGE] ✅ اتصال برقرار است")
            return True
        except Exception as e:
            self.connected = False
            logger.error(f"[EXCHANGE] ❌ اتصال برقرار نیست: {e}")
            return False

    def fetch_positions(self):
        try:
            data = self._request('GET', '/futures/positions')
            positions = []
            if isinstance(data, list):
                for pos in data:
                    if pos.get('status') == 'OPEN':
                        positions.append({
                            'symbol': pos['symbol'],
                            'side': pos['side'].lower(),
                            'contracts': float(pos['size']),
                            'entryPrice': float(pos.get('entryPrice', 0)),
                            'markPrice': float(pos.get('markPrice', 0)),
                            'leverage': int(pos.get('leverage', 1)),
                            'unrealizedPnL': float(pos.get('unrealizedPnL', 0))
                        })
            return positions
        except:
            return []

    def create_order(self, symbol, order_type, side, amount, price=None, params=None):
        order_data = {
            "symbol": symbol.upper(),
            "side": side.upper(),
            "tradeType": order_type.upper(),
            "leverage": params.get('leverage', 1) if params else 1,
            "walletType": "debit"
        }
        
        if order_type.upper() == "MARKET":
            order_data["cost"] = str(amount)
        else:
            order_data["size"] = str(amount)
            if price:
                order_data["price"] = str(price)
        
        if params:
            if 'stopLoss' in params:
                order_data["stopLoss"] = str(params['stopLoss'])
            if 'takeProfit' in params:
                order_data["takeProfit"] = str(params['takeProfit'])
        
        result = self._request('POST', '/futures/positions', order_data, symbol=symbol)
        
        return {
            'id': result.get('positionId'),
            'symbol': symbol,
            'side': side,
            'type': order_type,
            'price': price,
            'amount': amount,
            'status': 'closed' if result.get('positionId') else 'open'
        }

    def fetch_balance(self):
        try:
            data = self._request('GET', '/accounting/assets')
            if isinstance(data, list):
                for asset in data:
                    if asset.get('asset') == 'USDT' and asset.get('accountType') == 'futures':
                        return {
                            'total': float(asset.get('balance', 0)),
                            'available': float(asset.get('balance', 0)) - float(asset.get('lockedBalance', 0))
                        }
            return {'total': 0, 'available': 0}
        except:
            return None

# =====================================================================================
# توابع تلگرام
# =====================================================================================
def send_telegram_message(message: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logger.error(f"[TELEGRAM ERROR] {e}")

def format_iran_time(dt=None):
    if dt is None:
        dt = datetime.now(timezone(timedelta(hours=3, minutes=30)))
    return dt.strftime('%Y-%m-%d %H:%M:%S')

# =====================================================================================
# توابع محاسباتی
# =====================================================================================
def calc_rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1.0 / length, min_periods=length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / length, min_periods=length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

def calc_ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()

def calc_macd(close: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = calc_ema(close, fast)
    ema_slow = calc_ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calc_ema(macd_line, signal)
    hist_line = macd_line - signal_line
    return macd_line, signal_line, hist_line

def calc_atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / length, min_periods=length, adjust=False).mean()

def find_pivot_high(high: pd.Series, left_bars: int = 5, right_bars: int = 3):
    n = len(high)
    result = pd.Series(np.nan, index=high.index)
    for i in range(left_bars, n - right_bars):
        window_left = high.iloc[i - left_bars:i]
        window_right = high.iloc[i + 1:i + right_bars + 1]
        center = high.iloc[i]
        if not (window_left >= center).any() and not (window_right >= center).any():
            result.iloc[i] = center
    return result

def find_pivot_low(low: pd.Series, left_bars: int = 5, right_bars: int = 3):
    n = len(low)
    result = pd.Series(np.nan, index=low.index)
    for i in range(left_bars, n - right_bars):
        window_left = low.iloc[i - left_bars:i]
        window_right = low.iloc[i + 1:i + right_bars + 1]
        center = low.iloc[i]
        if not (window_left <= center).any() and not (window_right <= center).any():
            result.iloc[i] = center
    return result

def is_trending_up(close: pd.Series, ref_bar: int, lookback: int = 20, slope_min_pct: float = 0.05) -> bool:
    if ref_bar is None or ref_bar - lookback < 0:
        return False
    y = close.iloc[ref_bar - lookback:ref_bar + 1].values
    if len(y) < 2:
        return False
    x = np.arange(len(y))
    slope = np.polyfit(x, y, 1)[0]
    avg = y.mean()
    if avg == 0:
        return False
    return (slope / avg) * 100 > slope_min_pct

def is_trending_down(close: pd.Series, ref_bar: int, lookback: int = 20, slope_min_pct: float = 0.05) -> bool:
    if ref_bar is None or ref_bar - lookback < 0:
        return False
    y = close.iloc[ref_bar - lookback:ref_bar + 1].values
    if len(y) < 2:
        return False
    x = np.arange(len(y))
    slope = np.polyfit(x, y, 1)[0]
    avg = y.mean()
    if avg == 0:
        return False
    return (slope / avg) * 100 < -slope_min_pct

def compute_stop_and_targets(pivot_highs, pivot_lows, direction, df, atr_val, stop_buffer_pct=0.05):
    if direction == "long":
        if len(pivot_lows) < 2:
            return None, None, None
        pl_1, pl_2 = pivot_lows[-2], pivot_lows[-1]
        stop_price = min(pl_1['price'], pl_2['price']) - stop_buffer_pct * atr_val

        bar1, bar2 = pl_1['bar'], pl_2['bar']
        if bar1 is None or bar2 is None or bar2 <= bar1:
            return None, None, None
        mid_peak = df["high"].iloc[bar1 + 1:bar2].max() if bar2 > bar1 + 1 else df["high"].iloc[bar1:bar2 + 1].max()
        if pd.isna(mid_peak):
            return None, None, None
        return stop_price, mid_peak, None

    elif direction == "short":
        if len(pivot_highs) < 2:
            return None, None, None
        ph_1, ph_2 = pivot_highs[-2], pivot_highs[-1]
        stop_price = max(ph_1['price'], ph_2['price']) + stop_buffer_pct * atr_val

        bar1, bar2 = ph_1['bar'], ph_2['bar']
        if bar1 is None or bar2 is None or bar2 <= bar1:
            return None, None, None
        mid_trough = df["low"].iloc[bar1 + 1:bar2].min() if bar2 > bar1 + 1 else df["low"].iloc[bar1:bar2 + 1].min()
        if pd.isna(mid_trough):
            return None, None, None
        return stop_price, mid_trough, None

    return None, None, None

def resolve_final_target(entry_price: float, stop_price: float, tp1_raw: float, direction: str, min_rr_ratio: float = 2.0) -> float:
    risk_dist = abs(entry_price - stop_price)
    if risk_dist <= 0:
        return tp1_raw
    reward_dist = abs(tp1_raw - entry_price)
    rr = reward_dist / risk_dist
    if rr >= min_rr_ratio:
        return tp1_raw
    if direction == "long":
        return entry_price + risk_dist * min_rr_ratio
    else:
        return entry_price - risk_dist * min_rr_ratio

# =====================================================================================
# سیستم امتیازدهی
# =====================================================================================
def calculate_divergence_score(pivot1, pivot2, direction, df, current_price):
    score = 0
    details = []
    
    # 1. RSI
    if direction == "BUY":
        if pivot2['price'] < pivot1['price'] and pivot2['rsi'] > pivot1['rsi']:
            score += 1; details.append("✅ RSI")
        elif pivot2['price'] > pivot1['price'] and pivot2['rsi'] < pivot1['rsi']:
            score += 1; details.append("✅ RSI Hidden")
        else:
            details.append("❌ RSI")
    else:
        if pivot2['price'] > pivot1['price'] and pivot2['rsi'] < pivot1['rsi']:
            score += 1; details.append("✅ RSI")
        elif pivot2['price'] < pivot1['price'] and pivot2['rsi'] > pivot1['rsi']:
            score += 1; details.append("✅ RSI Hidden")
        else:
            details.append("❌ RSI")
    
    # 2. MACD Line
    if direction == "BUY":
        if pivot2['price'] < pivot1['price'] and pivot2['macdline'] > pivot1['macdline']:
            score += 1; details.append("✅ MACD Line")
        elif pivot2['price'] > pivot1['price'] and pivot2['macdline'] < pivot1['macdline']:
            score += 1; details.append("✅ MACD Line Hidden")
        else:
            details.append("❌ MACD Line")
    else:
        if pivot2['price'] > pivot1['price'] and pivot2['macdline'] < pivot1['macdline']:
            score += 1; details.append("✅ MACD Line")
        elif pivot2['price'] < pivot1['price'] and pivot2['macdline'] > pivot1['macdline']:
            score += 1; details.append("✅ MACD Line Hidden")
        else:
            details.append("❌ MACD Line")
    
    # 3. MACD Histogram + Color
    hist_div = False
    if direction == "BUY":
        if pivot2['price'] < pivot1['price'] and pivot2['hist'] > pivot1['hist']:
            hist_div = True
        elif pivot2['price'] > pivot1['price'] and pivot2['hist'] < pivot1['hist']:
            hist_div = True
    else:
        if pivot2['price'] > pivot1['price'] and pivot2['hist'] < pivot1['hist']:
            hist_div = True
        elif pivot2['price'] < pivot1['price'] and pivot2['hist'] > pivot1['hist']:
            hist_div = True
    
    color_changed = (pivot1['hist'] < 0 and pivot2['hist'] > 0) or (pivot1['hist'] > 0 and pivot2['hist'] < 0)
    
    if hist_div and color_changed:
        score += 1; details.append("✅ Histogram+Color")
    elif hist_div:
        details.append("⚠️ Histogram no color")
    else:
        details.append("❌ Histogram")
    
    # 4. Fibonacci
    if len(df) > 20:
        high_20 = df['high'].iloc[-20:].max()
        low_20 = df['low'].iloc[-20:].min()
        if high_20 != low_20:
            fib_618 = low_20 + 0.618 * (high_20 - low_20)
            fib_786 = low_20 + 0.786 * (high_20 - low_20)
            if abs(current_price - fib_618) / fib_618 < 0.005 or abs(current_price - fib_786) / fib_786 < 0.005:
                score += 1; details.append("✅ Fibonacci")
            else:
                details.append("❌ Fibonacci")
        else:
            details.append("❌ Fibonacci")
    else:
        details.append("❌ Fibonacci")
    
    # 5. Price Action
    if len(df) >= 3:
        last = df.iloc[-1]; prev = df.iloc[-2]
        avg_range = (df['high'] - df['low']).rolling(10).mean().iloc[-1]
        candle_range = last['high'] - last['low']
        
        pa_signal = False
        if candle_range > avg_range * 2:
            pa_signal = True
        body = abs(last['close'] - last['open'])
        upper_wick = last['high'] - max(last['open'], last['close'])
        lower_wick = min(last['open'], last['close']) - last['low']
        if direction == "BUY" and lower_wick > body * 2 and upper_wick < body * 0.5:
            pa_signal = True
        if direction == "SELL" and upper_wick > body * 2 and lower_wick < body * 0.5:
            pa_signal = True
        if direction == "BUY" and last['close'] > last['open'] and prev['close'] < prev['open'] and last['close'] > prev['open'] and last['open'] < prev['close']:
            pa_signal = True
        if direction == "SELL" and last['close'] < last['open'] and prev['close'] > prev['open'] and last['close'] < prev['open'] and last['open'] > prev['close']:
            pa_signal = True
        
        if pa_signal:
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
        self.last_processed_bar = 0
        self.alert_sent = False
        self.telegram_log_count = 0
        self.last_telegram_log_time = 0

# =====================================================================================
# تعریف نمادها
# =====================================================================================
SYMBOLS = ["LTCUSDT", "DOGEUSDT", "ETHUSDT"]
SYMBOL_STATES = {symbol: SymbolState() for symbol in SYMBOLS}

# =====================================================================================
# مدیریت تاریخچه
# =====================================================================================
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f:
                return json.load(f)
        except:
            return []
    return []

def save_history(history):
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f, indent=2)

def update_trade_result(symbol, signal_time, result, price):
    history = load_history()
    for trade in history:
        if trade['symbol'] == symbol and trade['signal_time'] == signal_time:
            trade['result'] = result
            trade['close_price'] = price
            trade['close_time'] = format_iran_time()
            break
    save_history(history)

# =====================================================================================
# تابع تشخیص سیگنال - نسخه نهایی
# =====================================================================================
def detect_signal(df, state, symbol, debug=False):
    debug_log = []
    
    def log(msg):
        debug_log.append(msg)
        if debug:
            logger.info(msg)
    
    log(f"🔍 DTM — {symbol} | {format_iran_time()}")
    
    closed_df = df.iloc[:-1].reset_index(drop=True)
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
    
    if state.last_processed_bar == 0:
        start_bar = 0
    else:
        start_bar = max(0, state.last_processed_bar - 5)
    
    start_bar = min(start_bar, last_valid_pivot_index)
    
    log(f"   n={n}, last_valid={last_valid_pivot_index}, start={start_bar}")
    
    new_pivots_high = []
    new_pivots_low = []
    
    existing_high_bars = {p['bar'] for p in state.pivot_highs}
    existing_low_bars = {p['bar'] for p in state.pivot_lows}
    
    for i in range(start_bar, last_valid_pivot_index + 1):
        if not pd.isna(pivot_high.iloc[i]) and i not in existing_high_bars:
            new_pivots_high.append({
                'price': pivot_high.iloc[i], 'bar': i,
                'rsi': rsi_val.iloc[i], 'macdline': macd_line.iloc[i], 'hist': hist_line.iloc[i]
            })
        if not pd.isna(pivot_low.iloc[i]) and i not in existing_low_bars:
            new_pivots_low.append({
                'price': pivot_low.iloc[i], 'bar': i,
                'rsi': rsi_val.iloc[i], 'macdline': macd_line.iloc[i], 'hist': hist_line.iloc[i]
            })
    
    state.last_processed_bar = last_valid_pivot_index + 1
    
    state.pivot_highs.extend(new_pivots_high)
    state.pivot_lows.extend(new_pivots_low)
    
    if len(state.pivot_highs) > 100:
        state.pivot_highs = state.pivot_highs[-100:]
    if len(state.pivot_lows) > 100:
        state.pivot_lows = state.pivot_lows[-100:]
    
    log(f"   new_high={len(new_pivots_high)}, new_low={len(new_pivots_low)} | mem: H={len(state.pivot_highs)} L={len(state.pivot_lows)}")
    
    # ⚡ ارسال لاگ به تلگرام - تضمینی
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
            if state.telegram_log_count <= 5:
                prefix = "🟢" if len(new_pivots_high) + len(new_pivots_low) > 0 else "ℹ️"
                send_telegram_message(
                    f"{prefix} **Log #{state.telegram_log_count} - {symbol}**\n"
                    f"🕒 {format_iran_time()}\n"
                    f"```\n{telegram_debug[:1500]}\n```"
                )
            else:
                send_telegram_message(
                    f"🔵 **Log #{state.telegram_log_count} - {symbol}**\n"
                    f"🕒 {format_iran_time()}\n"
                    f"```\n{telegram_debug[:1500]}\n```"
                )
            logger.info(f"[TELEGRAM] لاگ #{state.telegram_log_count} برای {symbol} ارسال شد")
        except Exception as e:
            logger.error(f"[TELEGRAM ERROR] {e}")
    
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
        if pl_2['price'] < pl_1['price'] or pl_2['price'] > pl_1['price']:
            if is_trending_down(close, pl_1['bar'], 20, 0.05):
                score, details = calculate_divergence_score(pl_1, pl_2, "BUY", df, current_price)
                buy_emoji, buy_label, buy_score, _ = classify_signal(score, details, "BUY")
                log(f"   🔵 BUY score={score}/5 {'✅' if buy_emoji else '❌'}")
                
                if buy_emoji and score >= 3:
                    stop, tp_raw, _ = compute_stop_and_targets(state.pivot_highs, state.pivot_lows, "long", closed_df, atr14.iloc[-1])
                    if stop and tp_raw:
                        buy_stop, buy_target = stop, resolve_final_target(entry_price, stop, tp_raw, "long")
                        buy_signal = "BUY"
    
    # SELL
    if len(state.pivot_highs) >= 2:
        ph_1, ph_2 = state.pivot_highs[-2], state.pivot_highs[-1]
        if ph_2['price'] > ph_1['price'] or ph_2['price'] < ph_1['price']:
            if is_trending_up(close, ph_1['bar'], 20, 0.05):
                score, details = calculate_divergence_score(ph_1, ph_2, "SELL", df, current_price)
                sell_emoji, sell_label, sell_score, _ = classify_signal(score, details, "SELL")
                log(f"   🔴 SELL score={score}/5 {'✅' if sell_emoji else '❌'}")
                
                if sell_emoji and score >= 3:
                    stop, tp_raw, _ = compute_stop_and_targets(state.pivot_highs, state.pivot_lows, "short", closed_df, atr14.iloc[-1])
                    if stop and tp_raw:
                        sell_stop, sell_target = stop, resolve_final_target(entry_price, stop, tp_raw, "short")
                        sell_signal = "SELL"
    
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
            symbol = trade['symbol']
            df = data.fetch_ohlcv(symbol, '1m', 10)
            if df is None or df.empty:
                continue
            
            current_price = df['close'].iloc[-1]
            entry = trade['entry_price']
            stop = trade['stop_loss']
            target = trade['take_profit']
            
            if trade['direction'] == 'BUY':
                if current_price >= target:
                    update_trade_result(symbol, trade['signal_time'], 'TAKE_PROFIT', current_price)
                    send_telegram_message(f"🎉 **تارگت!** {symbol} | سود: {(current_price-entry)/entry*100:.2f}%")
                elif current_price <= stop:
                    update_trade_result(symbol, trade['signal_time'], 'STOP_LOSS', current_price)
                    send_telegram_message(f"💔 **استاپ!** {symbol} | ضرر: {(current_price-entry)/entry*100:.2f}%")
            else:
                if current_price <= target:
                    update_trade_result(symbol, trade['signal_time'], 'TAKE_PROFIT', current_price)
                    send_telegram_message(f"🎉 **تارگت!** {symbol} | سود: {(entry-current_price)/entry*100:.2f}%")
                elif current_price >= stop:
                    update_trade_result(symbol, trade['signal_time'], 'STOP_LOSS', current_price)
                    send_telegram_message(f"💔 **استاپ!** {symbol} | ضرر: {(entry-current_price)/entry*100:.2f}%")

# =====================================================================================
# تابع اصلی
# =====================================================================================
def analyze_and_execute():
    logger.info("[ANALYZE] شروع تحلیل...")
    private_exchange = TrueTradePrivateExchange(API_KEY, API_SECRET, BASE_URL)
    connection_ok = private_exchange.test_connection()
    
    current_time = format_iran_time()
    if not hasattr(analyze_and_execute, "_last_status"):
        status = "✅ متصل" if connection_ok else "⚠️ قطع"
        send_telegram_message(f"📡 **وضعیت صرافی:** {status}\n🕒 {current_time}")
        analyze_and_execute._last_status = connection_ok
    elif analyze_and_execute._last_status != connection_ok:
        status = "✅ متصل" if connection_ok else "⚠️ قطع"
        send_telegram_message(f"🔄 **تغییر وضعیت:** {status}\n🕒 {current_time}")
        analyze_and_execute._last_status = connection_ok
    
    public_data = TrueTradePublicData()
    track_open_signals()
    
    for symbol in SYMBOLS:
        try:
            df = public_data.fetch_ohlcv(symbol, '1m', 500)
            if df is None or df.empty:
                logger.warning(f"[SKIP] {symbol}: داده‌ای دریافت نشد")
                continue
            
            logger.info(f"[DATA] {symbol}: {len(df)} کندل")
            
            signal, entry, stop, target, early, emoji, label, score = detect_signal(
                df, SYMBOL_STATES[symbol], symbol, debug=True
            )
            current_price = df['close'].iloc[-1]
            
            if early and not SYMBOL_STATES[symbol].alert_sent:
                send_telegram_message(
                    f"⚡ **آماده باش - {symbol}**\n"
                    f"💰 قیمت: {current_price:.4f}\n"
                    f"🕒 {format_iran_time()}"
                )
                SYMBOL_STATES[symbol].alert_sent = True
            
            if signal and stop and target:
                if signal == "BUY":
                    profit_pct = (target - entry) / entry * 100
                    loss_pct = (entry - stop) / entry * 100
                else:
                    profit_pct = (entry - target) / entry * 100
                    loss_pct = (stop - entry) / entry * 100
                
                direction_text = "🟢 خرید" if signal == "BUY" else "🔴 فروش"
                connection_status = "✅ متصل" if private_exchange.connected else "❌ قطع"
                
                message = (
                    f"{emoji} **سیگنال {label} - {symbol}**\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"🔸 {direction_text} | امتیاز: {score}/5\n"
                    f"📡 صرافی: {connection_status}\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"📍 ورود: {entry:.4f}\n"
                    f"🛑 ضرر: {stop:.4f}\n"
                    f"🎯 سود: {target:.4f}\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"📈 سود: {profit_pct:.2f}% | 📉 ضرر: {loss_pct:.2f}%\n"
                    f"🕒 {format_iran_time()}"
                )
                send_telegram_message(message)
                logger.info(f"[SIGNAL] {symbol}: {signal} | Score: {score}/5")
                
                history = load_history()
                history.append({
                    'symbol': symbol, 'direction': signal,
                    'entry_price': entry, 'stop_loss': stop, 'take_profit': target,
                    'signal_time': format_iran_time(), 'result': None,
                    'score': score, 'label': label
                })
                save_history(history)
                
                if private_exchange.connected:
                    try:
                        risk_dist = abs(entry - stop)
                        if risk_dist > 0:
                            qty = 3.5 / risk_dist
                            private_exchange.create_order(
                                symbol, "market", signal.lower(), qty, None,
                                {'leverage': 10, 'stopLoss': stop, 'takeProfit': target}
                            )
                            send_telegram_message(f"✅ **معامله ثبت شد!** {symbol} | حجم: {qty:.6f}")
                    except Exception as e:
                        send_telegram_message(f"❌ **خطای ثبت معامله** {symbol}: {str(e)[:100]}")
                
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
            logger.info(f"[LOOP] شروع دور جدید - {format_iran_time()}")
            analyze_and_execute()
            logger.info(f"[LOOP] پایان دور، ۶۰ ثانیه مکث...")
            time.sleep(60)
        except Exception as e:
            logger.error(f"[LOOP ERROR] {e}")
            time.sleep(60)

# =====================================================================================
# راه‌اندازی
# =====================================================================================
app = Flask(__name__)

@app.route("/")
def health_check():
    return "DTM Hybrid Bot is running.", 200

if __name__ == "__main__":
    logger.info("="*50)
    logger.info("DTM Hybrid Bot Starting...")
    logger.info("="*50)
    
    send_telegram_message(
        "🤖 **ربات DTM راه‌اندازی شد!**\n"
        "✅ باگ fatal رفع شد\n"
        "✅ lookback فعال\n"
        "✅ لاگ تضمینی ۵ بار اول\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "📡 لاگ‌ها هر ۵ دقیقه (۵ بار اول)"
    )
    
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=10000), daemon=True).start()
    logger.info("[STARTUP] Flask روی پورت 10000")
    
    main_loop()
