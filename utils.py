# -*- coding: utf-8 -*-
"""
توابع کمکی سراسری
"""

import os
import re
import json
import logging
import requests
import numpy as np
from datetime import datetime, timezone, timedelta

from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    HISTORY_FILE,
    STATE_FILE,
    SYMBOLS,
)

logger = logging.getLogger(__name__)

# =====================================================================================
# متغیرهای سراسری
# =====================================================================================
SIGNAL_COUNTER = 0


def send_telegram_message(message: str):
    try:
        clean_message = re.sub(r'```[^`]*```', '', message)
        clean_message = re.sub(r'[*_~`]', '', clean_message)
        if len(clean_message) > 4000:
            clean_message = clean_message[:4000] + "\n... (ادامه)"
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        response = requests.post(
            url, 
            json={
                "chat_id": TELEGRAM_CHAT_ID, 
                "text": clean_message
            }, 
            timeout=30
        )
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


def save_states(symbol_states):
    data = {s: symbol_states[s].to_dict() for s in SYMBOLS}
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"[STATE] Error saving states: {e}")


def load_states(symbol_states):
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                data = json.load(f)
            for s in SYMBOLS:
                if s in data:
                    from models import SymbolState
                    symbol_states[s] = SymbolState.from_dict(data[s])
            logger.info(f"[STATE] Loaded states from {STATE_FILE}")
        except Exception as e:
            logger.error(f"[STATE] Error loading states: {e}")
    else:
        logger.info(f"[STATE] No state file found, starting fresh")
    return symbol_states
