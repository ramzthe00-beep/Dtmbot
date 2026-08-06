# -*- coding: utf-8 -*-
"""
Signal Bot - TheTrueTrade
====================================================================
ربات دریافت داده و ارسال سیگنال به تلگرام (بدون معامله خودکار)
"""

import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
import threading
from flask import Flask

# =====================================================================================
# تنظیمات تلگرام
# =====================================================================================
TELEGRAM_BOT_TOKEN = "8514469828:AAFC76EiVA7I4TFiX08jJ5N6-eKtOLMKitE"
TELEGRAM_CHAT_ID = "7402770612"

# =====================================================================================
# تنظیمات صرافی
# =====================================================================================
BASE_URL = "https://apiv2.thetruetrade.io"

# =====================================================================================
# کلاس دریافت داده بدون نیاز به احراز هویت
# =====================================================================================
class TrueTradeData:
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
            
            # تبدیل به دیتافریم
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
            print(f"[FETCH ERROR] {symbol}: {e}")
            return None

# =====================================================================================
# توابع ارسال پیام به تلگرام
# =====================================================================================
def send_telegram_message(message: str):
    """ارسال پیام به تلگرام"""
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
    """دریافت زمان ایران (UTC+3:30)"""
    return datetime.now(timezone(timedelta(hours=3, minutes=30)))

def format_iran_time(dt=None):
    """فرمت‌سازی زمان ایران"""
    if dt is None:
        dt = get_iran_time()
    return dt.strftime('%Y-%m-%d %H:%M:%S')

# =====================================================================================
# توابع محاسباتی (ساده‌شده برای شناسایی سیگنال)
# =====================================================================================
def calc_rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=length, min_periods=length).mean()
    avg_loss = loss.rolling(window=length, min_periods=length).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

def calc_macd(close: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist_line = macd_line - signal_line
    return macd_line, signal_line, hist_line

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

def detect_divergence(df, lookback=20):
    """
    تشخیص واگرایی ساده DTM
    برگرداندن سیگنال: 'BUY', 'SELL', یا 'NONE'
    """
    close = df['close']
    high = df['high']
    low = df['low']
    
    rsi = calc_rsi(close)
    macd_line, signal_line, hist_line = calc_macd(close)
    
    pivot_high = find_pivot_high(high)
    pivot_low = find_pivot_low(low)
    
    last_i = len(df) - 1
    pivot_check_i = last_i - 3
    
    if pivot_check_i < 5:
        return 'NONE'
    
    new_pivot_high = not pd.isna(pivot_high.iloc[pivot_check_i])
    new_pivot_low = not pd.isna(pivot_low.iloc[pivot_check_i])
    
    # تشخیص واگرایی ساده
    if new_pivot_low:
        # بررسی کف‌های پایین‌تر قیمت و RSI بالاتر
        if pivot_low.iloc[pivot_check_i] < pivot_low.iloc[pivot_check_i - 5]:
            if rsi.iloc[pivot_check_i] > rsi.iloc[pivot_check_i - 5]:
                return 'BUY'
    
    if new_pivot_high:
        # بررسی قله‌های بالاتر قیمت و RSI پایین‌تر
        if pivot_high.iloc[pivot_check_i] > pivot_high.iloc[pivot_check_i - 5]:
            if rsi.iloc[pivot_check_i] < rsi.iloc[pivot_check_i - 5]:
                return 'SELL'
    
    return 'NONE'

# =====================================================================================
# تابع اصلی تحلیل و ارسال سیگنال
# =====================================================================================
def analyze_and_send():
    """دریافت داده، تحلیل و ارسال سیگنال به تلگرام"""
    data = TrueTradeData()
    symbols = ["LTCUSDT", "DOGEUSDT", "ETHUSDT"]
    
    for symbol in symbols:
        try:
            df = data.fetch_ohlcv(symbol, '1m', 500)
            if df is None or df.empty:
                print(f"[SKIP] {symbol}: داده‌ای دریافت نشد")
                continue
            
            signal = detect_divergence(df)
            if signal != 'NONE':
                iran_time = format_iran_time()
                message = f"🔔 **سیگنال معاملاتی**\n"
                message += f"🔹 **نماد:** {symbol}\n"
                message += f"🔸 **نوع:** {'🟢 خرید (BUY)' if signal == 'BUY' else '🔴 فروش (SELL)'}\n"
                message += f"💰 **قیمت فعلی:** {df['close'].iloc[-1]:.4f}\n"
                message += f"🕒 **زمان ایران:** {iran_time}\n"
                message += f"📊 **استراتژی:** DTM Divergence"
                send_telegram_message(message)
                print(f"[SIGNAL] {symbol}: {signal} at {df['close'].iloc[-1]}")
                
        except Exception as e:
            print(f"[ERROR] {symbol}: {e}")

# =====================================================================================
# حلقه اصلی
# =====================================================================================
def signal_loop():
    """حلقه بررسی مداوم"""
    last_signal_time = {}
    
    while True:
        try:
            # تحلیل و ارسال سیگنال
            analyze_and_send()
            
            # هر ۱ دقیقه یک بار بررسی کن
            time.sleep(60)
            
        except Exception as e:
            print(f"[LOOP ERROR] {e}")
            time.sleep(60)

# =====================================================================================
# راه‌اندازی Flask (برای Health Check)
# =====================================================================================
app = Flask(__name__)

@app.route("/")
def health_check():
    return "Signal Bot is running.", 200

def run_flask():
    app.run(host="0.0.0.0", port=10000)

# =====================================================================================
# اجرای اصلی
# =====================================================================================
if __name__ == "__main__":
    # ارسال پیام استارت
    send_telegram_message("🤖 **ربات سیگنال‌دهی DTM راه‌اندازی شد!**\n📊 در حال دریافت داده و تحلیل بازار...")
    
    # اجرای Flask در یک ترد جداگانه
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("[STARTUP] وب‌سرور Flask روی پورت 10000 راه‌اندازی شد.")
    
    # شروع حلقه اصلی
    print("[STARTUP] شروع حلقه دریافت و تحلیل داده...")
    signal_loop()
