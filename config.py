# -*- coding: utf-8 -*-
"""
تنظیمات و ثابت‌های استراتژی
"""

import os
import numpy as np

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

# =====================================================================================
# مسیر فایل‌ها
# =====================================================================================
HISTORY_FILE = "trades_history_hybrid.json"
STATE_FILE = "pivot_state.json"
CACHE_DIR = "ohlcv_cache"

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
# ثابت‌های استراتژی (مطابق PyneCore اصلی)
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
ENABLE_MACD_COLOR_FILTER = True  # فیلتر تغییر رنگ Histogram
FIB_USE_618 = True
FIB_USE_786 = True
FIB_TOLERANCE_PCT = 0.5
FIB_TREND_SEARCH_BARS = 100
SHADOW_TO_BODY_RATIO = 2.0
MAX_OPPOSITE_SHADOW_PCT = 20.0
MIN_CANDLE_ATR_RATIO = 0.3
BIG_CANDLE_AVG_LEN = 14
BIG_CANDLE_MULTIPLIER = 1.5
ENABLE_MTF = False
MTF_TIMEFRAME = "240"

LEFT_BARS = 5
RIGHT_BARS = 5 if PIVOT_MODE == 'استاندارد (5/5)' else 3
STOP_BUFFER_PCT = 0.05
HISTORY_BARS = 500
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
# نمادها
# =====================================================================================
SYMBOLS = ["LTCUSDT", "DOGEUSDT", "ETHUSDT"]
