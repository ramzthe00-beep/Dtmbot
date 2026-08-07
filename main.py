# -*- coding: utf-8 -*-
"""
DTM Divergence Auto-Trading Bot - TheTrueTrade (نسخه هیبریدی)
====================================================================
ربات معاملاتی هیبریدی با حافظه ۱۰۰ Pivot، سیستم امتیازدهی ۳ سطحی و لاگ کامل
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

# =====================================================================================
# کلیدهای API (برای اتصال به صرافی در صورت امکان)
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
# کلاس دریافت داده عمومی (بدون نیاز به احراز هویت)
# =====================================================================================
class TrueTradePublicData:
    def __init__(self):
        self.base_url = BASE_URL

    def fetch_ohlcv(self, symbol, timeframe='1m', limit=500):
        """دریافت داده‌های تاریخچه قیمت بدون نیاز به کلید API"""
        symbol_clean = symbol.upper()
        
        resolution_map = {
            "1m": "1",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "1h": "60",
            "4h": "240",
            "1d": "D",
            "1w": "W",
            "1M": "M"
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
            print(f"[PUBLIC FETCH ERROR] {symbol}: {e}")
            return None

# =====================================================================================
# کلاس صرافی خصوصی (برای ترید خودکار در صورت امکان) با دیباگ کامل
# =====================================================================================
class TrueTradePrivateExchange:
    def __init__(self, api_key, api_secret, base_url):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self.session = requests.Session()
        self.connected = False
        self._last_debug_time = 0

    def _sign_request(self, method, uri, timestamp):
        payload = f"{timestamp}{method.upper()}{uri}"
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            payload.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature

    def _send_debug_to_telegram(self, method, uri, headers, data, response, symbol="N/A"):
        current_time = time.time()
        if (current_time - self._last_debug_time) >= 3600:
            self._last_debug_time = current_time
            try:
                iran_time = format_iran_time()
                message = (
                    f"🐞 **گزارش دیباگ - خطای صرافی**\n"
                    f"🕒 **زمان ایران:** {iran_time}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔹 **نماد:** {symbol}\n"
                    f"🔸 **متد:** {method}\n"
                    f"🔹 **آدرس:** {uri}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📤 **هدرهای ارسالی:**\n"
                    f"`{json.dumps(headers, indent=2)}`\n"
                )
                if data:
                    message += f"📦 **بدنه ارسالی:**\n`{json.dumps(data, indent=2)}`\n"
                message += (
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📥 **پاسخ دریافتی:**\n"
                    f"**کد وضعیت:** {response.status_code}\n"
                    f"**بدنه پاسخ:**\n"
                    f"`{response.text[:800]}`\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📌 **علت احتمالی:** خطای احراز هویت یا دسترسی.\n"
                    f"💡 **توصیه:** لاگ‌های Railway را برای جزئیات بیشتر بررسی کنید."
                )
                send_telegram_message(message)
                print(f"[DEBUG] گزارش دیباگ به تلگرام ارسال شد (ساعت {iran_time})")
            except Exception as e:
                print(f"[DEBUG REPORT ERROR] {e}")

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
        
        print(f"\n[DEBUG REQUEST] {symbol}")
        print(f"Method: {method}")
        print(f"URL: {url}")
        print(f"Headers: {headers}")
        if data:
            print(f"Body: {json.dumps(data, indent=2)}")
        print(f"Signature Payload: {timestamp}{method.upper()}{uri}")
        
        response = self.session.request(method, url, headers=headers, json=data, timeout=15)
        
        print(f"\n[DEBUG RESPONSE] {symbol}")
        print(f"Status: {response.status_code}")
        try:
            print(f"Body: {json.dumps(response.json(), indent=2)}")
        except:
            print(f"Body: {response.text}")
        print("-" * 50)
        
        if not response.ok:
            error_msg = f"\n[PRIVATE EXCHANGE ERROR] Status: {response.status_code}"
            try:
                error_body = response.json()
                error_msg += f"\nError Response: {json.dumps(error_body, indent=2)}"
            except:
                error_msg += f"\nResponse Body: {response.text}"
            print(error_msg)
            
            self._send_debug_to_telegram(method, uri, headers, data, response, symbol)
            
            if response.status_code in [401, 403]:
                self.connected = False
            response.raise_for_status()
        else:
            self.connected = True
        return response.json()

    def test_connection(self, symbol="N/A"):
        print(f"[EXCHANGE] تست اتصال به صرافی... (نماد: {symbol})")
        try:
            response = self._request('GET', '/futures/positions', symbol=symbol)
            self.connected = True
            print("[EXCHANGE] ✅ اتصال به صرافی برقرار است.")
            print(f"[EXCHANGE] پاسخ سرور: {json.dumps(response, indent=2)[:200]}")
            return True
        except requests.exceptions.HTTPError as e:
            self.connected = False
            print(f"[EXCHANGE] ❌ اتصال به صرافی برقرار نیست. خطای HTTP: {e.response.status_code}")
            if e.response:
                print(f"[EXCHANGE] متن خطا: {e.response.text}")
            return False
        except Exception as e:
            self.connected = False
            print(f"[EXCHANGE] ❌ اتصال به صرافی برقرار نیست. خطا: {type(e).__name__}: {e}")
            return False

    def fetch_positions(self, symbol="N/A"):
        try:
            data = self._request('GET', '/futures/positions', symbol=symbol)
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
        symbol_clean = symbol.upper()
        side_upper = side.upper()
        trade_type = order_type.upper()
        
        order_data = {
            "symbol": symbol_clean,
            "side": side_upper,
            "tradeType": trade_type,
            "leverage": params.get('leverage', 1) if params else 1,
            "walletType": "debit"
        }
        
        if trade_type == "MARKET":
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
        
        uri = "/futures/positions"
        result = self._request('POST', uri, order_data, symbol=symbol)
        
        return {
            'id': result.get('positionId'),
            'symbol': symbol,
            'side': side,
            'type': order_type,
            'price': price,
            'amount': amount,
            'status': 'closed' if result.get('positionId') else 'open'
        }

    def fetch_balance(self, symbol="N/A"):
        try:
            uri = "/accounting/assets"
            data = self._request('GET', uri, symbol=symbol)
            if isinstance(data, list):
                for asset in data:
                    if asset.get('asset') == 'USDT' and asset.get('accountType') == 'futures':
                        return {
                            'total': float(asset.get('balance', 0)),
                            'locked': float(asset.get('lockedBalance', 0)),
                            'available': float(asset.get('balance', 0)) - float(asset.get('lockedBalance', 0))
                        }
            return {'total': 0, 'locked': 0, 'available': 0}
        except:
            return None

# =====================================================================================
# توابع ارسال پیام به تلگرام (با قالب‌های جذاب)
# =====================================================================================
def send_telegram_message(message: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")

def get_iran_time():
    return datetime.now(timezone(timedelta(hours=3, minutes=30)))

def format_iran_time(dt=None):
    if dt is None:
        dt = get_iran_time()
    return dt.strftime('%Y-%m-%d %H:%M:%S')

# =====================================================================================
# توابع محاسباتی استراتژی
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

def check_color_change(hist_line: pd.Series, bar_start: int, bar_end: int, need_red_phase: bool) -> bool:
    if bar_start is None or bar_end is None or bar_end <= bar_start:
        return False
    segment = hist_line.iloc[bar_start + 1:bar_end]
    return (segment < 0).any() if need_red_phase else (segment > 0).any()

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
            return None
        pl_1 = pivot_lows[-2]
        pl_2 = pivot_lows[-1]
        stop_price = min(pl_1['price'], pl_2['price']) - stop_buffer_pct * atr_val

        bar1, bar2 = pl_1['bar'], pl_2['bar']
        if bar1 is None or bar2 is None or bar2 <= bar1:
            return None
        mid_peak = df["high"].iloc[bar1 + 1:bar2].max() if bar2 > bar1 + 1 else df["high"].iloc[bar1:bar2 + 1].max()
        if pd.isna(mid_peak):
            return None
        return {"stop": stop_price, "tp1_raw": mid_peak}

    elif direction == "short":
        if len(pivot_highs) < 2:
            return None
        ph_1 = pivot_highs[-2]
        ph_2 = pivot_highs[-1]
        stop_price = max(ph_1['price'], ph_2['price']) + stop_buffer_pct * atr_val

        bar1, bar2 = ph_1['bar'], ph_2['bar']
        if bar1 is None or bar2 is None or bar2 <= bar1:
            return None
        mid_trough = df["low"].iloc[bar1 + 1:bar2].min() if bar2 > bar1 + 1 else df["low"].iloc[bar1:bar2 + 1].min()
        if pd.isna(mid_trough):
            return None
        return {"stop": stop_price, "tp1_raw": mid_trough}

    return None

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
# سیستم امتیازدهی ۳ سطحی (دایره سبز، زرد، سفید)
# =====================================================================================
def calculate_divergence_score(pivot1, pivot2, direction, df, current_price):
    """
    محاسبه امتیاز واگرایی بر اساس ۵ معیار
    بازگشت: (score, details)
    """
    score = 0
    details = []
    
    # 1. RSI واگرایی
    if direction == "BUY":
        if pivot2['price'] < pivot1['price'] and pivot2['rsi'] > pivot1['rsi']:
            score += 1
            details.append("✅ RSI واگرایی")
        elif pivot2['price'] > pivot1['price'] and pivot2['rsi'] < pivot1['rsi']:
            score += 1
            details.append("✅ RSI واگرایی (مخفی)")
        else:
            details.append("❌ RSI واگرایی ندارد")
    else:  # SELL
        if pivot2['price'] > pivot1['price'] and pivot2['rsi'] < pivot1['rsi']:
            score += 1
            details.append("✅ RSI واگرایی")
        elif pivot2['price'] < pivot1['price'] and pivot2['rsi'] > pivot1['rsi']:
            score += 1
            details.append("✅ RSI واگرایی (مخفی)")
        else:
            details.append("❌ RSI واگرایی ندارد")
    
    # 2. خط MACD واگرایی
    if direction == "BUY":
        if pivot2['price'] < pivot1['price'] and pivot2['macdline'] > pivot1['macdline']:
            score += 1
            details.append("✅ خط MACD واگرایی")
        elif pivot2['price'] > pivot1['price'] and pivot2['macdline'] < pivot1['macdline']:
            score += 1
            details.append("✅ خط MACD واگرایی (مخفی)")
        else:
            details.append("❌ خط MACD واگرایی ندارد")
    else:  # SELL
        if pivot2['price'] > pivot1['price'] and pivot2['macdline'] < pivot1['macdline']:
            score += 1
            details.append("✅ خط MACD واگرایی")
        elif pivot2['price'] < pivot1['price'] and pivot2['macdline'] > pivot1['macdline']:
            score += 1
            details.append("✅ خط MACD واگرایی (مخفی)")
        else:
            details.append("❌ خط MACD واگرایی ندارد")
    
    # 3. هیستوگرام MACD واگرایی + تغییر رنگ
    hist_divergence = False
    if direction == "BUY":
        if pivot2['price'] < pivot1['price'] and pivot2['hist'] > pivot1['hist']:
            hist_divergence = True
        elif pivot2['price'] > pivot1['price'] and pivot2['hist'] < pivot1['hist']:
            hist_divergence = True
    else:  # SELL
        if pivot2['price'] > pivot1['price'] and pivot2['hist'] < pivot1['hist']:
            hist_divergence = True
        elif pivot2['price'] < pivot1['price'] and pivot2['hist'] > pivot1['hist']:
            hist_divergence = True
    
    color_changed = (pivot1['hist'] < 0 and pivot2['hist'] > 0) or (pivot1['hist'] > 0 and pivot2['hist'] < 0)
    
    if hist_divergence and color_changed:
        score += 1
        details.append("✅ هیستوگرام MACD واگرایی + تغییر رنگ")
    elif hist_divergence:
        details.append("⚠️ هیستوگرام MACD واگرایی (بدون تغییر رنگ)")
    else:
        details.append("❌ هیستوگرام MACD واگرایی ندارد")
    
    # 4. فیبوناچی (ساده شده)
    if len(df) > 20:
        high_20 = df['high'].iloc[-20:].max()
        low_20 = df['low'].iloc[-20:].min()
        if high_20 != low_20:
            fib_618 = low_20 + 0.618 * (high_20 - low_20)
            fib_786 = low_20 + 0.786 * (high_20 - low_20)
            if abs(current_price - fib_618) / fib_618 < 0.005 or abs(current_price - fib_786) / fib_786 < 0.005:
                score += 1
                details.append("✅ فیبوناچی (۰.۶۱۸/۰.۷۸۶)")
            else:
                details.append("❌ فیبوناچی ندارد")
        else:
            details.append("❌ فیبوناچی ندارد")
    else:
        details.append("❌ فیبوناچی ندارد")
    
    # 5. کندل تأییدیه پرایس‌اکشن
    if len(df) >= 3:
        last = df.iloc[-1]
        prev = df.iloc[-2]
        avg_range = (df['high'] - df['low']).rolling(10).mean().iloc[-1]
        candle_range = last['high'] - last['low']
        
        pa_signal = False
        # کندل بزرگ
        if candle_range > avg_range * 2:
            pa_signal = True
        # Pin Bar
        body = abs(last['close'] - last['open'])
        upper_wick = last['high'] - max(last['open'], last['close'])
        lower_wick = min(last['open'], last['close']) - last['low']
        if direction == "BUY" and lower_wick > body * 2 and upper_wick < body * 0.5:
            pa_signal = True
        if direction == "SELL" and upper_wick > body * 2 and lower_wick < body * 0.5:
            pa_signal = True
        # Engulfing
        if direction == "BUY" and last['close'] > last['open'] and prev['close'] < prev['open'] and last['close'] > prev['open'] and last['open'] < prev['close']:
            pa_signal = True
        if direction == "SELL" and last['close'] < last['open'] and prev['close'] > prev['open'] and last['close'] < prev['open'] and last['open'] > prev['close']:
            pa_signal = True
        
        if pa_signal:
            score += 1
            details.append("✅ کندل تأییدیه پرایس‌اکشن")
        else:
            details.append("❌ کندل تأییدیه ندارد")
    else:
        details.append("❌ کندل تأییدیه ندارد")
    
    return score, details

def classify_signal(score, details, direction):
    """طبقه‌بندی سیگنال بر اساس امتیاز"""
    if score >= 5:
        return "🟢", "ایده‌آل (Ideal)", score, details
    elif score >= 4:
        return "🟡", "سفارشی (Custom)", score, details
    elif score >= 3:
        return "⚪", "حداقل مجاز (Minimal)", score, details
    else:
        return None, None, score, details

# =====================================================================================
# کلاس وضعیت (با حافظه ۱۰۰ Pivot)
# =====================================================================================
class SymbolState:
    def __init__(self):
        self.pivot_highs = []
        self.pivot_lows = []
        self.last_processed_bar = 0
        self.alert_sent = False

# =====================================================================================
# تعریف حالت‌های نمادها (خارج از تابع برای حفظ تاریخچه)
# =====================================================================================
SYMBOLS = ["LTCUSDT", "DOGEUSDT", "ETHUSDT"]
SYMBOL_STATES = {symbol: SymbolState() for symbol in SYMBOLS}

# =====================================================================================
# مدیریت تاریخچه معاملات
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
# تابع تشخیص سیگنال کامل با لاگ کامل
# =====================================================================================
def detect_signal(df, state, symbol):
    """تشخیص سیگنال با لاگ کامل و سیستم امتیازدهی ۳ سطحی"""
    closed_df = df.iloc[:-1].reset_index(drop=True)
    n = len(closed_df)
    if n < 5 + 3 + 20 + 5:
        return None, None, None, None, False, None, None, None

    close = closed_df["close"]
    high = closed_df["high"]
    low = closed_df["low"]

    rsi_val = calc_rsi(close, 14)
    macd_line, signal_line, hist_line = calc_macd(close, 12, 26, 9)
    atr14 = calc_atr(high, low, close, 14)
    pivot_high = find_pivot_high(high, 5, 3)
    pivot_low = find_pivot_low(low, 5, 3)

    last_i = n - 1
    
    # پیدا کردن همه Pivotهای جدید
    new_pivots_high = []
    new_pivots_low = []
    
    start_bar = state.last_processed_bar
    for i in range(start_bar, last_i + 1):
        if not pd.isna(pivot_high.iloc[i]):
            new_pivots_high.append({
                'price': pivot_high.iloc[i],
                'bar': i,
                'rsi': rsi_val.iloc[i],
                'macdline': macd_line.iloc[i],
                'hist': hist_line.iloc[i]
            })
        if not pd.isna(pivot_low.iloc[i]):
            new_pivots_low.append({
                'price': pivot_low.iloc[i],
                'bar': i,
                'rsi': rsi_val.iloc[i],
                'macdline': macd_line.iloc[i],
                'hist': hist_line.iloc[i]
            })
    
    state.last_processed_bar = last_i + 1
    state.pivot_highs.extend(new_pivots_high)
    state.pivot_lows.extend(new_pivots_low)
    
    if len(state.pivot_highs) > 100:
        state.pivot_highs = state.pivot_highs[-100:]
    if len(state.pivot_lows) > 100:
        state.pivot_lows = state.pivot_lows[-100:]
    
    # ✅ لاگ Pivot
    print(f"\n[DEBUG PIVOT] {symbol}")
    print(f"High pivots: {len(state.pivot_highs)}")
    print(f"Low pivots: {len(state.pivot_lows)}")
    if len(state.pivot_highs) > 0:
        print(f"Last high price: {state.pivot_highs[-1]['price']:.6f}")
    if len(state.pivot_lows) > 0:
        print(f"Last low price: {state.pivot_lows[-1]['price']:.6f}")
    print("-" * 40)
    
    early_signal = False
    if len(new_pivots_high) > 0 or len(new_pivots_low) > 0:
        early_signal = True
    
    entry_price = close.iloc[last_i]
    current_price = df['close'].iloc[-1]
    
    # =================================================================================
    # منطق تشخیص سیگنال با سیستم امتیازدهی ۳ سطحی
    # =================================================================================
    
    buy_signal = None
    sell_signal = None
    buy_score = 0
    sell_score = 0
    buy_details = []
    sell_details = []
    buy_emoji = None
    sell_emoji = None
    buy_label = None
    sell_label = None
    
    if len(state.pivot_lows) >= 2:
        pl_1 = state.pivot_lows[-2]
        pl_2 = state.pivot_lows[-1]
        
        # شرط قیمت برای واگرایی کلاسیک خرید
        classic_price_lower = pl_2['price'] < pl_1['price']
        # شرط قیمت برای واگرایی مخفی خرید
        hidden_price_higher = pl_2['price'] > pl_1['price']
        
        if classic_price_lower or hidden_price_higher:
            # بررسی روند
            trend_ok_bullish = is_trending_down(close, pl_1['bar'], 20, 0.05)
            print(f"[DEBUG TREND] {symbol} - Trend Down: {trend_ok_bullish}")
            
            if trend_ok_bullish:
                score, details = calculate_divergence_score(pl_1, pl_2, "BUY", df, current_price)
                buy_score = score
                buy_details = details
                buy_emoji, buy_label, _, _ = classify_signal(score, details, "BUY")
                
                if buy_emoji is not None and score >= 3:
                    # محاسبه استاپ و تارگت
                    levels = compute_stop_and_targets(
                        state.pivot_highs, state.pivot_lows, "long", closed_df, atr14.iloc[last_i]
                    )
                    if levels:
                        target = resolve_final_target(entry_price, levels["stop"], levels["tp1_raw"], "long")
                        buy_signal = "BUY"
    
    if len(state.pivot_highs) >= 2:
        ph_1 = state.pivot_highs[-2]
        ph_2 = state.pivot_highs[-1]
        
        # شرط قیمت برای واگرایی کلاسیک فروش
        classic_price_higher = ph_2['price'] > ph_1['price']
        # شرط قیمت برای واگرایی مخفی فروش
        hidden_price_lower = ph_2['price'] < ph_1['price']
        
        if classic_price_higher or hidden_price_lower:
            trend_ok_bearish = is_trending_up(close, ph_1['bar'], 20, 0.05)
            print(f"[DEBUG TREND] {symbol} - Trend Up: {trend_ok_bearish}")
            
            if trend_ok_bearish:
                score, details = calculate_divergence_score(ph_1, ph_2, "SELL", df, current_price)
                sell_score = score
                sell_details = details
                sell_emoji, sell_label, _, _ = classify_signal(score, details, "SELL")
                
                if sell_emoji is not None and score >= 3:
                    levels = compute_stop_and_targets(
                        state.pivot_highs, state.pivot_lows, "short", closed_df, atr14.iloc[last_i]
                    )
                    if levels:
                        target = resolve_final_target(entry_price, levels["stop"], levels["tp1_raw"], "short")
                        sell_signal = "SELL"
    
    # ✅ لاگ واگرایی
    print(f"\n[DIVERGENCE CHECK] {symbol}")
    print(f"Buy Signal: {buy_signal}")
    print(f"Buy Score: {buy_score}/5")
    if buy_details:
        for d in buy_details:
            print(f"  {d}")
    print(f"Sell Signal: {sell_signal}")
    print(f"Sell Score: {sell_score}/5")
    if sell_details:
        for d in sell_details:
            print(f"  {d}")
    print("-" * 40)
    
    # اولویت با سیگنال خرید (در صورت وجود)
    if buy_signal == "BUY":
        return "BUY", entry_price, None, None, early_signal, buy_emoji, buy_label, buy_score
    elif sell_signal == "SELL":
        return "SELL", entry_price, None, None, early_signal, sell_emoji, sell_label, sell_score
    
    return None, None, None, None, early_signal, None, None, None

# =====================================================================================
# بررسی نزدیکی به تارگت یا استاپ
# =====================================================================================
def check_proximity(symbol, current_price, entry, stop, target):
    if entry is None or stop is None or target is None:
        return
    
    stop_distance = abs(current_price - stop) / entry * 100
    target_distance = abs(current_price - target) / entry * 100
    
    if target_distance < 5 and target_distance > 0:
        message = (
            f"🎯 **هشدار نزدیکی به تارگت - ربات DTM**\n"
            f"🔹 **نماد:** {symbol}\n"
            f"💰 **قیمت فعلی:** {current_price:.4f}\n"
            f"🎯 **تارگت:** {target:.4f}\n"
            f"📊 **فاصله:** {target_distance:.2f}%\n"
            f"🕒 **زمان ایران:** {format_iran_time()}\n"
            f"💡 **وضعیت:** در آستانه رسیدن به تارگت!"
        )
        send_telegram_message(message)
        print(f"[PROXIMITY] {symbol}: نزدیک به تارگت! فاصله: {target_distance:.2f}%")
    
    elif stop_distance < 5 and stop_distance > 0:
        message = (
            f"🛑 **هشدار نزدیکی به استاپ - ربات DTM**\n"
            f"🔹 **نماد:** {symbol}\n"
            f"💰 **قیمت فعلی:** {current_price:.4f}\n"
            f"🛑 **استاپ:** {stop:.4f}\n"
            f"📊 **فاصله:** {stop_distance:.2f}%\n"
            f"🕒 **زمان ایران:** {format_iran_time()}\n"
            f"💡 **وضعیت:** در آستانه رسیدن به استاپ!"
        )
        send_telegram_message(message)
        print(f"[PROXIMITY] {symbol}: نزدیک به استاپ! فاصله: {stop_distance:.2f}%")

# =====================================================================================
# پیگیری سیگنال‌های باز
# =====================================================================================
def track_open_signals():
    history = load_history()
    data = TrueTradePublicData()
    exchange = TrueTradePrivateExchange(API_KEY, API_SECRET, BASE_URL)
    
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
            
            check_proximity(symbol, current_price, entry, stop, target)
            
            if trade['direction'] == 'BUY':
                if current_price >= target:
                    update_trade_result(symbol, trade['signal_time'], 'TAKE_PROFIT', current_price)
                    message = (
                        f"🎉 **تارگت محقق شد! - ربات DTM**\n"
                        f"🔹 **نماد:** {symbol}\n"
                        f"💰 **قیمت فعلی:** {current_price:.4f}\n"
                        f"🎯 **تارگت:** {target:.4f}\n"
                        f"📈 **سود:** {(current_price - entry) / entry * 100:.2f}%\n"
                        f"🕒 **زمان ایران:** {format_iran_time()}"
                    )
                    send_telegram_message(message)
                elif current_price <= stop:
                    update_trade_result(symbol, trade['signal_time'], 'STOP_LOSS', current_price)
                    message = (
                        f"💔 **استاپ خورد! - ربات DTM**\n"
                        f"🔹 **نماد:** {symbol}\n"
                        f"💰 **قیمت فعلی:** {current_price:.4f}\n"
                        f"🛑 **استاپ:** {stop:.4f}\n"
                        f"📉 **ضرر:** {(current_price - entry) / entry * 100:.2f}%\n"
                        f"🕒 **زمان ایران:** {format_iran_time()}"
                    )
                    send_telegram_message(message)
            elif trade['direction'] == 'SELL':
                if current_price <= target:
                    update_trade_result(symbol, trade['signal_time'], 'TAKE_PROFIT', current_price)
                    message = (
                        f"🎉 **تارگت محقق شد! - ربات DTM**\n"
                        f"🔹 **نماد:** {symbol}\n"
                        f"💰 **قیمت فعلی:** {current_price:.4f}\n"
                        f"🎯 **تارگت:** {target:.4f}\n"
                        f"📈 **سود:** {(entry - current_price) / entry * 100:.2f}%\n"
                        f"🕒 **زمان ایران:** {format_iran_time()}"
                    )
                    send_telegram_message(message)
                elif current_price >= stop:
                    update_trade_result(symbol, trade['signal_time'], 'STOP_LOSS', current_price)
                    message = (
                        f"💔 **استاپ خورد! - ربات DTM**\n"
                        f"🔹 **نماد:** {symbol}\n"
                        f"💰 **قیمت فعلی:** {current_price:.4f}\n"
                        f"🛑 **استاپ:** {stop:.4f}\n"
                        f"📉 **ضرر:** {(entry - current_price) / entry * 100:.2f}%\n"
                        f"🕒 **زمان ایران:** {format_iran_time()}"
                    )
                    send_telegram_message(message)

# =====================================================================================
# گزارش روزانه و ماهانه
# =====================================================================================
def send_daily_report():
    history = load_history()
    if not history:
        send_telegram_message("📋 **گزارش روزانه - ربات DTM**\nامروز هیچ معامله‌ای انجام نشده است.")
        return
    
    today = datetime.now().date()
    today_trades = [t for t in history if datetime.fromisoformat(t['signal_time']).date() == today]
    
    if not today_trades:
        send_telegram_message("📋 **گزارش روزانه - ربات DTM**\nامروز هیچ معامله‌ای انجام نشده است.")
        return
    
    total = len(today_trades)
    wins = len([t for t in today_trades if t.get('result') == 'TAKE_PROFIT'])
    losses = len([t for t in today_trades if t.get('result') == 'STOP_LOSS'])
    open_trades = len([t for t in today_trades if t.get('result') is None])
    
    message = (
        f"📊 **گزارش روزانه - ربات DTM**\n"
        f"🕒 **تاریخ:** {format_iran_time().split()[0]}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 **معاملات امروز:** {total} عدد\n"
        f"✅ **موفق (تارگت):** {wins} عدد\n"
        f"❌ **ناموفق (استاپ):** {losses} عدد\n"
        f"⏳ **باز:** {open_trades} عدد\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 **نرخ موفقیت:** {wins/total*100 if total > 0 else 0:.1f}%\n"
        f"💪 **کارنامه:** {'عالی! 🚀' if wins > losses else 'نیاز به بررسی 📊'}\n"
    )
    
    for i, trade in enumerate(today_trades[-5:], 1):
        result_emoji = "✅" if trade.get('result') == 'TAKE_PROFIT' else "❌" if trade.get('result') == 'STOP_LOSS' else "⏳"
        message += (
            f"\n{i}. {trade['symbol']} {trade['direction']} {result_emoji}"
            f" | ورود: {trade['entry_price']:.4f}"
            f" | SL: {trade['stop_loss']:.4f}"
            f" | TP: {trade['take_profit']:.4f}"
        )
    
    send_telegram_message(message)

def send_monthly_report():
    history = load_history()
    if not history:
        send_telegram_message("📊 **گزارش ماهانه - ربات DTM**\nاین ماه هیچ معامله‌ای انجام نشده است.")
        return
    
    today = datetime.now()
    month_ago = today - timedelta(days=30)
    month_trades = [t for t in history if datetime.fromisoformat(t['signal_time']) >= month_ago]
    
    if not month_trades:
        send_telegram_message("📊 **گزارش ماهانه - ربات DTM**\nاین ماه هیچ معامله‌ای انجام نشده است.")
        return
    
    total = len(month_trades)
    wins = len([t for t in month_trades if t.get('result') == 'TAKE_PROFIT'])
    losses = len([t for t in month_trades if t.get('result') == 'STOP_LOSS'])
    open_trades = len([t for t in month_trades if t.get('result') is None])
    
    message = (
        f"📈 **گزارش ماهانه - ربات DTM**\n"
        f"📅 **۳۰ روز گذشته**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 **کل معاملات:** {total} عدد\n"
        f"✅ **موفق (تارگت):** {wins} عدد\n"
        f"❌ **ناموفق (استاپ):** {losses} عدد\n"
        f"⏳ **باز:** {open_trades} عدد\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 **نرخ موفقیت:** {wins/total*100 if total > 0 else 0:.1f}%\n"
        f"📊 **میانگین سود/ضرر روزانه:** {(wins - losses) / 30:.2f} معامله\n"
        f"💪 **ارزیابی:** {'پروژه موفق! 🎉' if wins > losses else 'نیاز به بهینه‌سازی ⚙️'}\n"
    )
    
    send_telegram_message(message)

# =====================================================================================
# تابع اصلی تحلیل، ارسال سیگنال و ترید خودکار
# =====================================================================================
def analyze_and_execute():
    """دریافت داده، تحلیل، ارسال سیگنال و در صورت امکان ترید خودکار"""
    
    print("[ANALYZE] شروع فرآیند تحلیل و معامله...")
    private_exchange = TrueTradePrivateExchange(API_KEY, API_SECRET, BASE_URL)
    
    print("[EXCHANGE] در حال تست اتصال به صرافی...")
    connection_ok = private_exchange.test_connection()
    
    # پیام وضعیت اتصال و قابلیت‌ها
    current_time = format_iran_time()
    if connection_ok:
        connection_status = "✅ **اتصال به صرافی برقرار است**"
        connection_detail = "ربات قادر به انجام معاملات خودکار است."
    else:
        connection_status = "⚠️ **اتصال به صرافی برقرار نیست**"
        connection_detail = "ربات فقط سیگنال ارسال می‌کند و معامله‌ای انجام نمی‌شود."
    
    capabilities = (
        "🔧 **قابلیت‌های فعال در ربات:**\n"
        "• 📊 دریافت داده از راه عمومی (فعال)\n"
        "• 🧠 تشخیص سیگنال با استراتژی DTM (فعال)\n"
        "• 💰 ترید خودکار در صورت اتصال به صرافی ({})\n"
        "• 🔔 ارسال هشدار در صورت عدم اتصال (فعال)\n"
        "• 📅 گزارش‌های روزانه و ماهانه (فعال)\n"
        "• ⚡ هشدارهای آماده باش، تارگت و استاپ (فعال)\n"
        "• 📈 ذخیره ۱۰۰ Pivot اخیر برای تحلیل دقیق‌تر (فعال)"
    ).format("✅ فعال" if connection_ok else "❌ غیرفعال")
    
    full_message = (
        f"📡 **وضعیت اتصال به صرافی:**\n"
        f"{connection_status}\n"
        f"{connection_detail}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{capabilities}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕒 **زمان ایران:** {current_time}"
    )
    
    if not hasattr(analyze_and_execute, "_last_status"):
        send_telegram_message(full_message)
        analyze_and_execute._last_status = connection_ok
    elif analyze_and_execute._last_status != connection_ok:
        send_telegram_message(
            f"🔄 **تغییر وضعیت اتصال به صرافی**\n"
            f"وضعیت قبلی: {'✅ متصل' if analyze_and_execute._last_status else '❌ قطع'}\n"
            f"وضعیت جدید: {'✅ متصل' if connection_ok else '❌ قطع'}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{full_message}"
        )
        analyze_and_execute._last_status = connection_ok
    
    # دریافت داده و تحلیل
    public_data = TrueTradePublicData()
    track_open_signals()
    
    for symbol in SYMBOLS:
        try:
            df = public_data.fetch_ohlcv(symbol, '1m', 500)
            if df is None or df.empty:
                print(f"[SKIP] {symbol}: داده‌ای دریافت نشد")
                continue
            
            print(f"[DATA] {symbol}: {len(df)} کندل دریافت شد")
            
            signal, entry_price, stop_loss, take_profit, early_signal, emoji, label, score = detect_signal(
                df, SYMBOL_STATES[symbol], symbol
            )
            current_price = df['close'].iloc[-1]
            
            if early_signal and not SYMBOL_STATES[symbol].alert_sent:
                message = (
                    f"⚡ **هشدار آماده باش - ربات DTM**\n"
                    f"🔹 **نماد:** {symbol}\n"
                    f"💰 **قیمت فعلی:** {current_price:.4f}\n"
                    f"🕒 **زمان ایران:** {format_iran_time()}\n"
                    f"💡 **وضعیت:** احتمال تشکیل قله/کف جدید!\n"
                    f"⏳ **زمان تا سیگنال نهایی:** ~۲ دقیقه"
                )
                send_telegram_message(message)
                SYMBOL_STATES[symbol].alert_sent = True
                print(f"[EARLY] {symbol}: هشدار آماده باش ارسال شد")
            
            if signal is not None:
                # محاسبه استاپ و تارگت
                if signal == "BUY":
                    levels = compute_stop_and_targets(
                        SYMBOL_STATES[symbol].pivot_highs, 
                        SYMBOL_STATES[symbol].pivot_lows, 
                        "long", df, 
                        calc_atr(df['high'], df['low'], df['close'], 14).iloc[-1]
                    )
                    if levels:
                        stop_loss = levels["stop"]
                        take_profit = resolve_final_target(entry_price, levels["stop"], levels["tp1_raw"], "long")
                else:  # SELL
                    levels = compute_stop_and_targets(
                        SYMBOL_STATES[symbol].pivot_highs, 
                        SYMBOL_STATES[symbol].pivot_lows, 
                        "short", df, 
                        calc_atr(df['high'], df['low'], df['close'], 14).iloc[-1]
                    )
                    if levels:
                        stop_loss = levels["stop"]
                        take_profit = resolve_final_target(entry_price, levels["stop"], levels["tp1_raw"], "short")
                
                if stop_loss is None or take_profit is None:
                    print(f"[SKIP] {symbol}: محاسبه استاپ/تارگت ناموفق")
                    continue
                
                # تخمین سود و ضرر
                if signal == "BUY":
                    potential_profit = (take_profit - entry_price) / entry_price * 100
                    potential_loss = (entry_price - stop_loss) / entry_price * 100
                else:
                    potential_profit = (entry_price - take_profit) / entry_price * 100
                    potential_loss = (stop_loss - entry_price) / entry_price * 100
                
                iran_time = format_iran_time()
                direction_text = "🟢 خرید (BUY)" if signal == "BUY" else "🔴 فروش (SELL)"
                connection_status = "✅ متصل" if private_exchange.connected else "❌ قطع"
                score_text = f"{score}/5" if score else "N/A"
                label_text = label if label else "N/A"
                emoji_text = emoji if emoji else "📊"
                
                message = (
                    f"{emoji_text} **سیگنال معاملاتی - {label_text}**\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔹 **نماد:** {symbol}\n"
                    f"🔸 **نوع:** {direction_text}\n"
                    f"💰 **قیمت فعلی:** {current_price:.4f}\n"
                    f"📊 **امتیاز:** {score_text}\n"
                    f"📡 **اتصال به صرافی:** {connection_status}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📍 **نقطه ورود:** {entry_price:.4f}\n"
                    f"🛑 **حد ضرر:** {stop_loss:.4f}\n"
                    f"🎯 **حد سود:** {take_profit:.4f}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📈 **سود احتمالی:** {potential_profit:.2f}%\n"
                    f"📉 **ضرر احتمالی:** {potential_loss:.2f}%\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🕒 **زمان ایران:** {iran_time}\n"
                    f"🤖 **ربات:** DTM Pro (Hybrid)\n"
                    f"💡 **توجه:** این سیگنال با استراتژی DTM Divergence تولید شده است."
                )
                
                send_telegram_message(message)
                print(f"[SIGNAL] {symbol}: {signal} | Score: {score}/5 | Entry: {entry_price:.4f} | SL: {stop_loss:.4f} | TP: {take_profit:.4f}")
                
                # ذخیره سیگنال در تاریخچه
                history = load_history()
                history.append({
                    'symbol': symbol,
                    'direction': signal,
                    'entry_price': entry_price,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'signal_time': format_iran_time(),
                    'result': None,
                    'close_price': None,
                    'close_time': None,
                    'score': score,
                    'label': label
                })
                save_history(history)
                
                # تلاش برای ترید خودکار
                if private_exchange.connected:
                    try:
                        print(f"[EXCHANGE] تلاش برای ثبت معامله...")
                        risk_dist = abs(entry_price - stop_loss)
                        if risk_dist > 0:
                            qty = 3.5 / risk_dist
                            leverage = 10
                            order_result = private_exchange.create_order(
                                symbol, "market", signal.lower(), qty, None,
                                {'leverage': leverage, 'stopLoss': stop_loss, 'takeProfit': take_profit}
                            )
                            print(f"[ORDER EXECUTED] {symbol}: {signal} | Qty: {qty:.6f} | Leverage: {leverage}x")
                            success_msg = (
                                f"✅ **معامله با موفقیت ثبت شد! - ربات DTM**\n"
                                f"🔹 **نماد:** {symbol}\n"
                                f"🔸 **جهت:** {signal}\n"
                                f"💰 **قیمت ورود:** {entry_price:.4f}\n"
                                f"🛑 **حد ضرر:** {stop_loss:.4f}\n"
                                f"🎯 **حد سود:** {take_profit:.4f}\n"
                                f"📊 **اهرم:** {leverage}x\n"
                                f"📦 **حجم:** {qty:.6f}\n"
                                f"🕒 **زمان ایران:** {format_iran_time()}"
                            )
                            send_telegram_message(success_msg)
                        else:
                            print(f"[SKIP] {symbol}: فاصله قیمت تا استاپ معتبر نیست")
                    except Exception as e:
                        error_msg = (
                            f"❌ **خطا در ثبت معامله - ربات DTM**\n"
                            f"🔹 **نماد:** {symbol}\n"
                            f"💡 **خطا:** {str(e)[:100]}\n"
                            f"📌 **اقدام:** لطفاً به صورت دستی اقدام کنید.\n"
                            f"🕒 **زمان ایران:** {format_iran_time()}"
                        )
                        send_telegram_message(error_msg)
                        print(f"[ERROR] {symbol}: {e}")
                
                SYMBOL_STATES[symbol].alert_sent = False
                
            else:
                print(f"[ANALYSIS] {symbol}: بدون سیگنال")
                
        except Exception as e:
            print(f"[ERROR] {symbol}: {e}")

# =====================================================================================
# حلقه اصلی (با دیباگ کامل)
# =====================================================================================
def main_loop():
    last_daily_report = None
    last_monthly_report = None
    
    while True:
        try:
            print("[LOOP] شروع یک دور جدید بررسی...")
            try:
                analyze_and_execute()
            except Exception as e:
                print(f"[ANALYZE ERROR] {e}")
                traceback.print_exc()
            print("[LOOP] پایان دور بررسی، ۶۰ ثانیه مکث...")
            
            today = datetime.now().date()
            if last_daily_report != today:
                send_daily_report()
                last_daily_report = today
            
            if last_monthly_report is None or (datetime.now() - last_monthly_report).days >= 30:
                send_monthly_report()
                last_monthly_report = datetime.now()
            
            time.sleep(60)
            
        except Exception as e:
            print(f"[LOOP ERROR] {e}")
            traceback.print_exc()
            time.sleep(60)

# =====================================================================================
# راه‌اندازی Flask
# =====================================================================================
app = Flask(__name__)

@app.route("/")
def health_check():
    return "DTM Hybrid Bot is running.", 200

def run_flask():
    app.run(host="0.0.0.0", port=10000)

# =====================================================================================
# اجرای اصلی
# =====================================================================================
if __name__ == "__main__":
    send_telegram_message(
        "🤖 **ربات معاملاتی هیبریدی DTM (Hybrid Pro) راه‌اندازی شد!**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 در حال دریافت داده و تحلیل بازار...\n"
        "💡 **قابلیت‌های ربات:**\n"
        "• دریافت داده از راه عمومی\n"
        "• تشخیص سیگنال با استراتژی DTM\n"
        "• ترید خودکار در صورت اتصال به صرافی\n"
        "• ارسال هشدار در صورت عدم اتصال\n"
        "• گزارش‌های روزانه و ماهانه\n"
        "• هشدارهای آماده باش، تارگت و استاپ\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ **توجه:** در صورت عدم اتصال به صرافی، فقط سیگنال ارسال می‌شود."
    )
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("[STARTUP] وب‌سرور Flask روی پورت 10000 راه‌اندازی شد.")
    
    print("[STARTUP] شروع حلقه اصلی...")
    main_loop()
