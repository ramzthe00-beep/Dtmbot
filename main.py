# -*- coding: utf-8 -*-
"""
DTM Divergence Auto-Trading Bot - TheTrueTrade (نسخه هیبریدی)
====================================================================
نسخه نهایی کامل — منطق واگرایی دقیقاً مطابق Pine Script:
- Pivot: حالت سریع (5/3) — **FIXED**: دقیقاً مطابق Pine (strict, value on confirmation bar)
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
- **FIXED**: فقط ۲ پیوت آخر بررسی میشن (مثل Pine)
- **FIXED [PINE-EXACT GATE]**: ۴ شرط اجباری (RSI + MACD Line + MACD Hist + Trend)
- رفع فرمول فیبوناچی
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
- پیام سیگنال با اطلاعات کامل پیوت‌ها
- گزارش واقعی صرافی (معاملات + موجودی)
- **SECURITY**: کلیدهای API فقط از متغیر محیطی خوانده میشن
- **DEBUG**: Full Debug Log (فایل `full_debug_log.txt`)
- **FIXED [v2.0]**: Base3 = RSI+MACDl+MACDh (روند امتیازی شد)
- **FIXED [v2.0]**: MACD Color Change با منطق sign-based
- **FIXED [v2.0]**: فیبوناچی از کندل تأیید جستجو میکند
- **FIXED [v2.0]**: همه ۴ نوع واگرایی همزمان بررسی میشوند
- **FIXED [v2.0]**: جلوگیری از تقسیم بر صفر در Price Action
- **FIXED [v3.0]**: معماری Non-Blocking با asyncio + aiohttp
- **FIXED [v3.0]**: Atomic Persistence با فایل موقت
- **FIXED [v3.0]**: Decoupled Telegram با asyncio.Queue
- **FIXED [v3.0]**: Circuit Breaker برای جلوگیری از خطاهای متوالی
- **FIXED [v3.0]**: Order Reconciliation در تایم‌اوت
"""

import os
import sys
import time
import threading
import traceback
import hashlib
import hmac
import asyncio
import aiohttp
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from flask import Flask
import json
import logging
from typing import Dict, Any, Optional, List, Tuple

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

HISTORY_BARS = 1000

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
# 🔥 تغییر ۱: Atomic Persistence
# =====================================================================================
async def atomic_write_json(file_path: str, data: Any):
    """نوشتن اتمی فایل JSON با استفاده از فایل موقت"""
    temp_file = f"{file_path}.tmp"
    try:
        def _write():
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(temp_file, file_path)
        await asyncio.to_thread(_write)
    except Exception as e:
        logger.error(f"[PERSISTENCE] Failed atomic write to {file_path}: {e}")

# =====================================================================================
# 🔥 تغییر ۲: Decoupled Asynchronous Telegram
# =====================================================================================
class AsyncTelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.queue = asyncio.Queue()
        self.session: Optional[aiohttp.ClientSession] = None
        self._worker_task = None

    async def start(self, session: aiohttp.ClientSession):
        self.session = session
        self._worker_task = asyncio.create_task(self._dispatcher_loop())

    def notify(self, message: str):
        """ارسال غیربلاک‌شونده پیام به تلگرام"""
        self.queue.put_nowait(message)

    async def _dispatcher_loop(self):
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        while True:
            msg = await self.queue.get()
            try:
                payload = {"chat_id": self.chat_id, "text": msg, "parse_mode": "Markdown"}
                async with self.session.post(url, json=payload, timeout=10) as resp:
                    if resp.status == 429:  # Rate limited
                        await asyncio.sleep(2)
                    elif resp.status != 200:
                        logger.error(f"[TELEGRAM] Status: {resp.status}")
            except Exception as e:
                logger.error(f"[TELEGRAM] Dispatch failed: {e}")
            finally:
                self.queue.task_done()

# =====================================================================================
# 🔥 تغییر ۳: Circuit Breaker (Kill-Switch)
# =====================================================================================
class KillSwitchException(Exception):
    pass

class CircuitBreaker:
    def __init__(self, max_consecutive_errors: int = 5):
        self.max_errors = max_consecutive_errors
        self.consecutive_errors = 0
        self.tripped = False

    def record_success(self):
        self.consecutive_errors = 0

    def record_failure(self):
        self.consecutive_errors += 1
        if self.consecutive_errors >= self.max_errors:
            self.tripped = True
            logger.critical("[KILL-SWITCH] Maximum consecutive errors reached.")

# =====================================================================================
# 🔥 تغییر ۴: Async Exchange Client با Order Reconciliation
# =====================================================================================
class AsyncTrueTradeExchange:
    def __init__(self, session: aiohttp.ClientSession, api_key: str, api_secret: str, base_url: str):
        self.session = session
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url

    def _sign_request(self, method: str, uri: str, timestamp: str) -> str:
        payload = f"{timestamp}{method.upper()}{uri}"
        return hmac.new(self.api_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    async def _request(self, method: str, uri: str, data: Dict[str, Any] = None) -> Any:
        timestamp = str(int(time.time() * 1000))
        signature = self._sign_request(method, uri, timestamp)
        headers = {
            "X-API-Key": self.api_key,
            "X-Timestamp": timestamp,
            "X-Signature": signature,
            "Content-Type": "application/json"
        }
        url = f"{self.base_url}{uri}"
        
        async with self.session.request(method, url, headers=headers, json=data, timeout=10) as resp:
            body = await resp.text()
            if resp.status >= 400:
                raise RuntimeError(f"HTTP {resp.status} - {body}")
            return json.loads(body)

    async def fetch_open_positions(self) -> List[Dict[str, Any]]:
        try:
            res = await self._request('GET', '/futures/positions?active=true')
            return res if isinstance(res, list) else []
        except Exception as e:
            logger.error(f"[EXCHANGE] Fetch open positions error: {e}")
            raise

    async def create_order_safe(self, symbol: str, side: str, capital: float, 
                                leverage: int, sl: float, tp: float) -> Dict[str, Any]:
        """
        ثبت سفارش با مدیریت تایم‌اوت و Reconciliation
        """
        uri = '/futures/positions'
        prec = PRICE_PRECISION.get(symbol.upper(), 2)
        
        order_data = {
            "symbol": symbol.upper(),
            "side": side.upper(),
            "tradeType": "MARKET",
            "leverage": leverage,
            "cost": f"{capital:.{prec}f}",
            "walletType": "debit",
            "stopLoss": f"{sl:.{prec}f}",
            "takeProfit": f"{tp:.{prec}f}"
        }

        try:
            return await self._request('POST', uri, order_data)
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            logger.warning(f"[ORDER TIMEOUT] Network error for {symbol}. Reconciling...")
            await asyncio.sleep(1.0)
            
            # بررسی اینکه آیا سفارش با وجود تایم‌اوت پر شده است
            positions = await self.fetch_open_positions()
            for pos in positions:
                if pos.get('symbol') == symbol.upper() and pos.get('side') == side.upper():
                    logger.info(f"[RECONCILIATION] Order filled despite timeout. ID: {pos.get('positionId')}")
                    return pos
            
            raise RuntimeError(f"Order failed and position not found: {e}")

    async def emergency_close_all(self, notifier: AsyncTelegramNotifier):
        """بستن اضطراری همه پوزیشن‌ها (Kill-Switch)"""
        logger.critical("[EMERGENCY] Closing all positions...")
        try:
            positions = await self.fetch_open_positions()
            for pos in positions:
                symbol = pos.get('symbol')
                side = "SELL" if pos.get('side') == "LONG" else "BUY"
                cost = pos.get('cost', 0)
                close_payload = {
                    "symbol": symbol,
                    "side": side,
                    "tradeType": "MARKET",
                    "cost": str(cost),
                    "walletType": "debit"
                }
                await self._request('POST', '/futures/positions', close_payload)
                notifier.notify(f"🚨 **EMERGENCY CLOSE** | Closed {symbol} {side}")
        except Exception as e:
            logger.error(f"[EMERGENCY] Failed to close positions: {e}")

# =====================================================================================
# ادامه توابع محاسباتی (بدون تغییر)
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
    prev_sign = None
    for i in range(bar1 + 1, bar2 + 1):
        if i >= len(hist_series):
            break
        current_val = hist_series.iloc[i]
        current_sign = 1 if current_val > 0 else (-1 if current_val < 0 else 0)
        if prev_sign is not None and current_sign != 0 and prev_sign != 0:
            if need_negative_phase and prev_sign > 0 and current_sign < 0:
                return True
            if not need_negative_phase and prev_sign < 0 and current_sign > 0:
                return True
        if current_sign != 0:
            prev_sign = current_sign
    return False

def find_trend_start_low(low_series, ref_bar, search_bars=FIB_SEARCH_BARS):
    if ref_bar is None:
        return None
    confirm_bar = min(ref_bar + RIGHT_BARS, len(low_series) - 1)
    start = max(0, confirm_bar - search_bars + 1)
    window = low_series.iloc[start:confirm_bar + 1]
    return window.min() if len(window) > 0 else None

def find_trend_start_high(high_series, ref_bar, search_bars=FIB_SEARCH_BARS):
    if ref_bar is None:
        return None
    confirm_bar = min(ref_bar + RIGHT_BARS, len(high_series) - 1)
    start = max(0, confirm_bar - search_bars + 1)
    window = high_series.iloc[start:confirm_bar + 1]
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

def calculate_divergence_score(p1, p2, div_type, direction, bar1, bar2, hist_series, high_series, low_series, df_indexed, atr_series, close):
    details = []
    
    # شرط ۱: RSI
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
    
    # شرط ۲: MACD Line
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
    
    # شرط ۳: MACD Histogram
    if direction == "SELL" and div_type == 'classic':
        hist_ok = (p1['hist'] > 0 and p2['hist'] > 0 and 
                   p2['hist'] < p1['hist'] and
                   check_macd_color_change(hist_series, bar1, bar2, True))
    elif direction == "SELL" and div_type == 'hidden':
        hist_ok = (p1['hist'] > 0 and p2['hist'] > 0 and 
                   p2['hist'] > p1['hist'] and
                   check_macd_color_change(hist_series, bar1, bar2, True))
    elif direction == "BUY" and div_type == 'classic':
        hist_ok = (p1['hist'] < 0 and p2['hist'] < 0 and 
                   p2['hist'] > p1['hist'] and
                   check_macd_color_change(hist_series, bar1, bar2, False))
    elif direction == "BUY" and div_type == 'hidden':
        hist_ok = (p1['hist'] < 0 and p2['hist'] < 0 and 
                   p2['hist'] < p1['hist'] and
                   check_macd_color_change(hist_series, bar1, bar2, False))
    else:
        hist_ok = False
    details.append("✅ MACD Histogram" if hist_ok else "❌ MACD Histogram")
    
    # Base3 Gate
    base3_ok = rsi_ok and macd_ok and hist_ok
    if not base3_ok:
        details.append("❌ Base3 برقرار نیست")
        return 0, details
    
    score = 3
    
    # شرط ۴: Trend
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
    if trend_ok:
        score += 1
        details.append("✅ Trend")
    else:
        details.append("❌ Trend")
    
    # شرط ۵: Fibonacci
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
    
    # شرط ۶: Price Action
    confirm_bar = min(bar2 + RIGHT_BARS, len(df_indexed) - 1)
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
    elif score >= 3: return "🔵", "Base3 Only"
    else: return None, None

# =====================================================================================
# کلاس وضعیت (بدون تغییر)
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

async def save_states_async():
    data = {s: SYMBOL_STATES[s].to_dict() for s in SYMBOLS}
    await atomic_write_json(STATE_FILE, data)

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
# کلاس دریافت داده (همزمان برای سازگاری)
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
# تابع تشخیص سیگنال (بدون تغییر)
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

    if API_RETURNS_OPEN_CANDLE:
        closed_df_indexed = df.iloc[:-1].copy()
    else:
        closed_df_indexed = df.copy()
    
    if len(closed_df_indexed) > 0:
        last_bar_start = closed_df_indexed.index[-1]
        if last_bar_start.tzinfo is None:
            last_bar_start = last_bar_start.tz_localize('UTC')
        last_bar_end = last_bar_start + pd.Timedelta(minutes=1)
        now_utc = pd.Timestamp.now(tz='UTC')
        if now_utc < last_bar_end:
            closed_df_indexed = closed_df_indexed.iloc[:-1].copy()
    
    if len(closed_df_indexed) > HISTORY_BARS:
        closed_df_indexed = closed_df_indexed.tail(HISTORY_BARS).copy()
    
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
    pivot_high = find_pivot_high(high, LEFT_BARS, RIGHT_BARS)
    pivot_low = find_pivot_low(low, LEFT_BARS, RIGHT_BARS)

    last_confirmed = n - 1 - RIGHT_BARS

    existing_high_ts = {p['ts'] for p in state.pivot_highs}
    existing_low_ts = {p['ts'] for p in state.pivot_lows}

    new_pivots_high = []
    new_pivots_low = []

    for i in range(LEFT_BARS, last_confirmed + 1):
        ts = closed_df_indexed.index[i]
        if not pd.isna(pivot_high.iloc[i]) and ts not in existing_high_ts:
            real_bar = i - RIGHT_BARS
            new_pivots_high.append({
                'ts': ts, 'price': float(pivot_high.iloc[i]),
                'rsi': float(rsi_val.iloc[real_bar]),
                'macdline': float(macd_line.iloc[real_bar]),
                'hist': float(hist_line.iloc[real_bar]),
                'bar': real_bar
            })
        if not pd.isna(pivot_low.iloc[i]) and ts not in existing_low_ts:
            real_bar = i - RIGHT_BARS
            new_pivots_low.append({
                'ts': ts, 'price': float(pivot_low.iloc[i]),
                'rsi': float(rsi_val.iloc[real_bar]),
                'macdline': float(macd_line.iloc[real_bar]),
                'hist': float(hist_line.iloc[real_bar]),
                'bar': real_bar
            })

    if new_pivots_high:
        state.pivot_highs.extend(new_pivots_high)
        if len(state.pivot_highs) > 500:
            state.pivot_highs = state.pivot_highs[-500:]
    if new_pivots_low:
        state.pivot_lows.extend(new_pivots_low)
        if len(state.pivot_lows) > 500:
            state.pivot_lows = state.pivot_lows[-500:]

    state.last_processed_ts = closed_df_indexed.index[last_confirmed]

    log(f"   n={n}, last_confirmed={last_confirmed}")
    log(f"   new_high={len(new_pivots_high)}, new_low={len(new_pivots_low)} | mem: H={len(state.pivot_highs)} L={len(state.pivot_lows)}")

    early_signal = len(new_pivots_high) > 0 or len(new_pivots_low) > 0
    entry_price = float(close.iloc[-1])

    signals = []
    
    if len(state.pivot_lows) >= 2:
        pl_1 = state.pivot_lows[-2]
        pl_2 = state.pivot_lows[-1]
        bar1 = resolve_bar_from_ts(closed_df_indexed, pl_1['ts'])
        bar2 = resolve_bar_from_ts(closed_df_indexed, pl_2['ts'])

        if bar1 is not None and bar2 is not None and bar2 > bar1:
            is_classic_bull = pl_2['price'] < pl_1['price'] and pl_2['rsi'] > pl_1['rsi']
            is_hidden_bull = pl_2['price'] > pl_1['price'] and pl_2['rsi'] < pl_1['rsi']
            
            if is_classic_bull:
                score, details = calculate_divergence_score(
                    pl_1, pl_2, 'classic', 'BUY', bar1, bar2,
                    hist_line, high, low, closed_df_indexed, atr14, close
                )
                if score >= 3:
                    signals.append({
                        'type': 'BUY', 'div_type': 'classic', 'score': score,
                        'details': details, 'p1': pl_1, 'p2': pl_2,
                        'bar1': bar1, 'bar2': bar2
                    })
            
            if is_hidden_bull:
                score, details = calculate_divergence_score(
                    pl_1, pl_2, 'hidden', 'BUY', bar1, bar2,
                    hist_line, high, low, closed_df_indexed, atr14, close
                )
                if score >= 3:
                    signals.append({
                        'type': 'BUY', 'div_type': 'hidden', 'score': score,
                        'details': details, 'p1': pl_1, 'p2': pl_2,
                        'bar1': bar1, 'bar2': bar2
                    })

    if len(state.pivot_highs) >= 2:
        ph_1 = state.pivot_highs[-2]
        ph_2 = state.pivot_highs[-1]
        bar1 = resolve_bar_from_ts(closed_df_indexed, ph_1['ts'])
        bar2 = resolve_bar_from_ts(closed_df_indexed, ph_2['ts'])

        if bar1 is not None and bar2 is not None and bar2 > bar1:
            is_classic_bear = ph_2['price'] > ph_1['price'] and ph_2['rsi'] < ph_1['rsi']
            is_hidden_bear = ph_2['price'] < ph_1['price'] and ph_2['rsi'] > ph_1['rsi']
            
            if is_classic_bear:
                score, details = calculate_divergence_score(
                    ph_1, ph_2, 'classic', 'SELL', bar1, bar2,
                    hist_line, high, low, closed_df_indexed, atr14, close
                )
                if score >= 3:
                    signals.append({
                        'type': 'SELL', 'div_type': 'classic', 'score': score,
                        'details': details, 'p1': ph_1, 'p2': ph_2,
                        'bar1': bar1, 'bar2': bar2
                    })
            
            if is_hidden_bear:
                score, details = calculate_divergence_score(
                    ph_1, ph_2, 'hidden', 'SELL', bar1, bar2,
                    hist_line, high, low, closed_df_indexed, atr14, close
                )
                if score >= 3:
                    signals.append({
                        'type': 'SELL', 'div_type': 'hidden', 'score': score,
                        'details': details, 'p1': ph_1, 'p2': ph_2,
                        'bar1': bar1, 'bar2': bar2
                    })

    if signals:
        best = max(signals, key=lambda x: (x['score'], 1 if x['div_type'] == 'classic' else 0))
        signal = best['type']
        
        confirm_bar = min(best['bar2'] + RIGHT_BARS, len(atr14) - 1)
        atr_at_confirm = atr14.iloc[confirm_bar]
        
        direction = "long" if signal == "BUY" else "short"
        stop, tp_raw, _ = compute_stop_and_targets(
            state.pivot_highs, state.pivot_lows, direction, 
            closed_df_indexed, atr_at_confirm
        )
        
        if stop and tp_raw:
            target = resolve_final_target(entry_price, stop, tp_raw, direction)
            emoji, label = classify_signal(best['score'])
            
            log(f"   ✅ SIGNAL: {signal} {best['div_type']} | Score={best['score']}/6 | {label}")
            log(f"      Entry={entry_price:.4f}, SL={stop:.4f}, TP={target:.4f}")
            
            save_debug_log_to_file(symbol, debug_file_lines)
            
            return (signal, entry_price, stop, target, early_signal, 
                    emoji, label, best['score'], best['details'],
                    best['p1'], best['p2'])

    if not signals:
        log(f"   ⚪ No signal")

    save_debug_log_to_file(symbol, debug_file_lines)
    return None, None, None, None, early_signal, None, None, None, [], None, None

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
# توابع کمکی
# =====================================================================================
def format_iran_time(dt=None):
    if dt is None:
        dt = datetime.now(timezone(timedelta(hours=3, minutes=30)))
    return dt.strftime('%Y-%m-%d %H:%M:%S')

def format_iran_date(dt=None):
    if dt is None:
        dt = datetime.now(timezone(timedelta(hours=3, minutes=30)))
    return dt.strftime('%Y-%m-%d')

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
            break
    save_history(h)

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

# =====================================================================================
# 🔥 تغییر ۵: تابع پردازش Async هر نماد
# =====================================================================================
async def process_symbol_async(symbol: str, exchange: AsyncTrueTradeExchange, 
                               notifier: AsyncTelegramNotifier, circuit_breaker: CircuitBreaker):
    """پردازش غیربلاک‌شونده هر نماد"""
    try:
        # دریافت داده به صورت همزمان (برای سازگاری با کد فعلی)
        public_data = TrueTradePublicData()
        df = await asyncio.to_thread(public_data.fetch_ohlcv, symbol, '1m', HISTORY_BARS)
        
        if df is None or df.empty:
            logger.warning(f"[SKIP] {symbol}")
            circuit_breaker.record_success()
            return
        
        logger.info(f"[DATA] {symbol}: {len(df)} کندل")
        
        # تشخیص سیگنال (همزمان، چون محاسبات سنگین است)
        result = await asyncio.to_thread(
            detect_signal, df, SYMBOL_STATES[symbol], symbol, True
        )
        
        if len(result) >= 11:
            signal, entry, stop, target, early, emoji, label, score, details, pivot1, pivot2 = result
        else:
            signal, entry, stop, target, early, emoji, label, score = result[:8]
            details = result[8] if len(result) > 8 else []
            pivot1 = result[9] if len(result) > 9 else None
            pivot2 = result[10] if len(result) > 10 else None
            
        cp = df['close'].iloc[-1] if not df.empty else 0

        if early and not SYMBOL_STATES[symbol].alert_sent:
            SYMBOL_STATES[symbol].alert_sent = True
            notifier.notify(f"⚡ Pivot جدید — {symbol} {HASHTAGS['pivot']}\n💰 {cp:.4f}\n⏳ ~۲ دقیقه تا تأیید\n🕒 {format_iran_time()}")

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

            # مدیریت سرمایه (همزمان)
            balance = await asyncio.to_thread(lambda: exchange.fetch_balance())
            if balance is None:
                balance = 0

            leverage_map = {"LTCUSDT": 75, "DOGEUSDT": 75, "ETHUSDT": 50}
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
            
            notifier.notify(signal_message)

            # ✅ حذف time.sleep(0.5) و ارسال همزمان تلگرام
            
            # ثبت سفارش به صورت غیربلاک‌شونده
            try:
                side_map = {"BUY": "LONG", "SELL": "SHORT"}
                order_result = await exchange.create_order_safe(
                    symbol, "market", side_map[signal], capital,
                    int(used_leverage), stop, target
                )
                
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
                notifier.notify(order_message)
                
            except Exception as e:
                notifier.notify(f"❌ خطا — {symbol} #سیگنال_{signal_number}\n{str(e)[:200]}\n🕒 {format_iran_time()}")
            
            SYMBOL_STATES[symbol].alert_sent = False
        else:
            logger.info(f"[ANALYSIS] {symbol}: بدون سیگنال")
            
        circuit_breaker.record_success()
        
    except Exception as e:
        circuit_breaker.record_failure()
        logger.error(f"[PROCESS ERROR] Symbol {symbol}: {e}")
        if circuit_breaker.tripped:
            raise KillSwitchException(f"Circuit breaker triggered for {symbol}")

# =====================================================================================
# 🔥 تغییر ۶: حلقه اصلی Non-Blocking
# =====================================================================================
async def async_main_loop():
    circuit_breaker = CircuitBreaker(max_consecutive_errors=5)
    
    async with aiohttp.ClientSession() as session:
        notifier = AsyncTelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
        await notifier.start(session)
        
        exchange = AsyncTrueTradeExchange(session, API_KEY, API_SECRET, BASE_URL)
        
        notifier.notify("🚀 **Quant Engine Started** | Non-blocking Event Loop Active.")
        
        while True:
            loop_start = time.time()
            try:
                # پردازش موازی همه نمادها
                tasks = [
                    process_symbol_async(symbol, exchange, notifier, circuit_breaker)
                    for symbol in SYMBOLS
                ]
                await asyncio.gather(*tasks)
                
                # ذخیره اتمی state
                await save_states_async()
                
            except KillSwitchException as e:
                logger.critical(f"[MAIN LOOP] Kill-switch activated: {e}")
                await exchange.emergency_close_all(notifier)
                notifier.notify("🚨 **KILL-SWITCH ACTIVATED**: Engine halted. Positions closed.")
                break
                
            except Exception as e:
                logger.error(f"[LOOP EXCEPTION] {e}")
                circuit_breaker.record_failure()
                if circuit_breaker.tripped:
                    await exchange.emergency_close_all(notifier)
                    notifier.notify("🚨 **KILL-SWITCH ACTIVATED**: Engine halted.")
                    break
            
            # زمان‌بندی دقیق ۶۰ ثانیه
            elapsed = time.time() - loop_start
            sleep_duration = max(1.0, 60.0 - elapsed)
            await asyncio.sleep(sleep_duration)

# =====================================================================================
# تابع همگام‌سازی برای Flask و Main
# =====================================================================================
def run_async_main():
    asyncio.run(async_main_loop())

# =====================================================================================
# Flask App (بدون تغییر)
# =====================================================================================
app = Flask(__name__)

@app.route("/")
def health():
    return "OK", 200

# =====================================================================================
# Main Entry Point
# =====================================================================================
if __name__ == "__main__":
    logger.info("DTM Bot Starting (v3.0 - Non-Blocking)...")
    
    load_signal_counter()
    load_states()

    hashtag_list = "\n".join([f"• {v} → {k}" for k, v in HASHTAGS.items()])
    send_telegram_message(
        f"🤖 DTM Pro — آنلاین {HASHTAGS['startup']}\n\n"
        f"🧠 DTM Divergence (Pine Script Mirror — v3.0 Non-Blocking)\n"
        f"📊 سیگنال + ترید خودکار\n\n"
        f"⚙️ Pivot: 5/3 (Pine-Exact: strict, value on confirmation bar)\n"
        f"⚙️ Gating: Base3 (RSI+MACDl+MACDh) + Trend/Fib/PA امتیازی\n"
        f"⚙️ همه ۴ نوع واگرایی همزمان بررسی میشوند\n"
        f"⚙️ معماری Non-Blocking: asyncio + aiohttp\n"
        f"⚙️ Atomic Persistence: فایل موقت + os.replace\n"
        f"⚙️ Decoupled Telegram: asyncio.Queue\n"
        f"⚙️ Circuit Breaker: {5} خطای متوالی\n"
        f"🔧 ETH=50x | LTC/DOGE=75x\n\n"
        f"📌 هشتگ‌های ثابت:\n{hashtag_list}\n\n"
        f"🕒 {format_iran_time()}"
    )
    
    # اجرای Flask در thread جداگانه
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=10000), daemon=True).start()
    logger.info("[STARTUP] Flask روی پورت 10000")
    
    # اجرای حلقه اصلی Non-Blocking
    run_async_main()
