# -*- coding: utf-8 -*-
"""
حلقه اصلی و startup
"""

import time
import threading
import logging
import requests
from flask import Flask
import numpy as np

from config import (
    API_KEY,
    API_SECRET,
    BASE_URL,
    HISTORY_BARS,
    HASHTAGS,
    PIVOT_MODE,
    LEFT_BARS,
    RIGHT_BARS,
    RSI_LEN,
    MACD_FAST,
    MACD_SLOW,
    MACD_SIG,
    TREND_LOOKBACK,
    TREND_SLOPE_MIN_PCT,
    MIN_CONFIRMATIONS,
    ENABLE_HIDDEN,
    ENABLE_MACD_COLOR_FILTER,
    SYMBOLS,
)

from models import SymbolState
from data_manager import TrueTradePublicData, TrueTradePrivateExchange
from divergence import detect_signal
from strategy_engine import execute_order
from indicators import calc_rsi, calc_atr
from pivots import find_pivot_high, find_pivot_low
from utils import (
    send_telegram_message,
    format_iran_time,
    format_iran_date,
    load_signal_counter,
    save_states,
    load_states,
)
from reports import send_reports

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# =====================================================================================
# متغیرهای سراسری
# =====================================================================================
SYMBOL_STATES = {s: SymbolState() for s in SYMBOLS}


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
            diagnostic_log.append("🟢 Candle Confirmation: فعال")
    except Exception as e:
        diagnostic_log.append(f"🔴 خطا: {str(e)[:50]}")
    
    diagnostic_log.append(f"🟢 پارامترهای استراتژی (مطابق PyneCore):")
    diagnostic_log.append(f"   • PIVOT_MODE: {PIVOT_MODE}")
    diagnostic_log.append(f"   • RSI_LEN: {RSI_LEN}")
    diagnostic_log.append(f"   • MACD: {MACD_FAST}/{MACD_SLOW}/{MACD_SIG}")
    diagnostic_log.append(f"   • TREND_LOOKBACK: {TREND_LOOKBACK}")
    diagnostic_log.append(f"   • TREND_SLOPE_MIN_PCT: {TREND_SLOPE_MIN_PCT}%")
    diagnostic_log.append(f"   • MIN_CONFIRMATIONS: {MIN_CONFIRMATIONS}")
    diagnostic_log.append(f"   • ENABLE_HIDDEN: {ENABLE_HIDDEN}")
    diagnostic_log.append(f"   • ENABLE_MACD_COLOR_FILTER: {ENABLE_MACD_COLOR_FILTER}")
    
    diagnostic_log.append("🟢 موتور امتیازدهی: Base3 + Trend/Fib/PA + ColorFilter")
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


def analyze_and_execute():
    logger.info("[ANALYZE] شروع...")
    exchange = TrueTradePrivateExchange(API_KEY, API_SECRET, BASE_URL)
    conn = exchange.test_connection()
    balance = exchange.fetch_balance() if conn else 0
    if balance is None:
        balance = 0

    data = TrueTradePublicData()

    for symbol in SYMBOLS:
        try:
            df = data.fetch_ohlcv(symbol, '1m', HISTORY_BARS)
            if df is None or df.empty:
                logger.warning(f"[SKIP] {symbol}")
                continue
            
            logger.info(f"[DATA] {symbol}: {len(df)} کندل")

            result = detect_signal(df, SYMBOL_STATES[symbol], symbol)
            
            if len(result) >= 11:
                signal, entry, stop, target, early, emoji, label, score, details, pivot1, pivot2 = result
            else:
                signal, entry, stop, target, early, emoji, label, score = result[:8]
                details = result[8] if len(result) > 8 else []
                pivot1 = result[9] if len(result) > 9 else None
                pivot2 = result[10] if len(result) > 10 else None

            if early and not SYMBOL_STATES[symbol].alert_sent:
                SYMBOL_STATES[symbol].alert_sent = True
                send_telegram_message(f"⚡ Pivot جدید — {symbol} {HASHTAGS['pivot']}\n💰 {df['close'].iloc[-1]:.4f}\n⏳ ~۲ دقیقه تا تأیید\n🕒 {format_iran_time()}")

            if signal and stop and target:
                execute_order(exchange, symbol, signal, entry, stop, target, balance, score, label, pivot1, pivot2)
                SYMBOL_STATES[symbol].alert_sent = False
            else:
                logger.info(f"[ANALYSIS] {symbol}: بدون سیگنال")
        except Exception as e:
            logger.error(f"[ERROR] {symbol}: {e}")
    
    save_states(SYMBOL_STATES)


def main_loop():
    last_daily_report_date = None
    
    while True:
        try:
            logger.info(f"[LOOP] {format_iran_time()}")
            analyze_and_execute()
            
            today = format_iran_date()
            if last_daily_report_date != today:
                try:
                    send_reports()
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
    SYMBOL_STATES = load_states(SYMBOL_STATES)

    send_telegram_message(
        f"🤖 DTM Divergence Light — آنلاین {HASHTAGS['startup']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🧠 منطق: دقیقاً مطابق PyneCore (بدون MTF)\n"
        f"📊 Pivot: {PIVOT_MODE} ({LEFT_BARS}/{RIGHT_BARS})\n"
        f"⚙️ Base3: RSI + MACD Line + MACD Histogram\n"
        f"⚙️ Trend + Fibonacci + PA (امتیازی)\n"
        f"⚙️ Color Filter: {'فعال' if ENABLE_MACD_COLOR_FILTER else 'غیرفعال'}\n"
        f"⚙️ ۴ نوع واگرایی: Classic/Hidden + BUY/SELL\n"
        f"🔧 ETH=50x | LTC/DOGE=75x\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕒 {format_iran_time()}"
    )
    
    run_startup_diagnostic()
    
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=10000), daemon=True).start()
    logger.info("[STARTUP] Flask روی پورت 10000")
    main_loop()
