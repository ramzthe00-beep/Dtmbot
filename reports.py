# -*- coding: utf-8 -*-
"""
توابع گزارش‌گیری
"""

import logging
import numpy as np

from config import HASHTAGS
from utils import (
    send_telegram_message,
    format_iran_time,
    format_iran_date,
    load_history,
)

logger = logging.getLogger(__name__)


def generate_daily_report_text(trades):
    today_str = format_iran_date()
    if not trades:
        return None
    total_trades = len(trades)
    total_realized_pnl = sum(float(t.get('realized_pnl', 0)) for t in trades)
    wins = len([t for t in trades if float(t.get('realized_pnl', 0)) > 0])
    losses = len([t for t in trades if float(t.get('realized_pnl', 0)) < 0])
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
    total_realized_pnl = sum(float(t.get('realized_pnl', 0)) for t in trades)
    wins = len([t for t in trades if float(t.get('realized_pnl', 0)) > 0])
    losses = len([t for t in trades if float(t.get('realized_pnl', 0)) < 0])
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


def send_reports():
    try:
        today_str = format_iran_date()
        history = load_history()
        today_trades = [t for t in history if t.get('signal_time', '').startswith(today_str)]
        if today_trades:
            total = len(today_trades)
            wins = len([t for t in today_trades if t.get('result') == 'TAKE_PROFIT'])
            losses = len([t for t in today_trades if t.get('result') == 'STOP_LOSS'])
            closed = wins + losses
            win_rate = (wins / closed * 100) if closed > 0 else 0
            total_pnl = sum([t.get('realized_pnl', 0) for t in today_trades if t.get('result') is not None])
            
            daily_msg = f"""📊 گزارش روزانه (محلی) — {today_str} {HASHTAGS['daily']}
━━━━━━━━━━━━━━━━━━━━━━
📈 کل معاملات: {total} عدد
✅ موفق: {wins} ({win_rate:.1f}%)
❌ ناموفق: {losses}
💰 سود/زیان خالص: {total_pnl:.2f} USDT
📊 نرخ موفقیت: {win_rate:.1f}%
━━━━━━━━━━━━━━━━━━━━━━
🕒 {format_iran_time()}"""
            send_telegram_message(daily_msg)
            logger.info("[REPORT] Local daily report sent.")
    except Exception as e:
        logger.error(f"[REPORT ERROR] Local daily: {e}")
    
    try:
        history = load_history()
        if history:
            monthly_msg = generate_monthly_report_text(history)
            if monthly_msg:
                send_telegram_message(monthly_msg)
                logger.info("[REPORT] Monthly report sent.")
    except Exception as e:
        logger.error(f"[REPORT ERROR] Monthly: {e}")
