# -*- coding: utf-8 -*-
"""
محاسبه Stop/Target و اجرای سفارش
"""

import time
import logging
import pandas as pd

from config import (
    STOP_BUFFER_PCT,
    PRICE_PRECISION,
    HASHTAGS,
)

from utils import (
    send_telegram_message,
    format_iran_time,
    load_history,
    save_history,
    get_next_signal_number,
)

logger = logging.getLogger(__name__)


def resolve_bar_from_ts(df_indexed, ts):
    """دریافت ایندکس کندل بر اساس timestamp"""
    if ts is None:
        return None
    try:
        if ts.tzinfo is None:
            ts = ts.tz_localize('UTC')
        if df_indexed.index.tzinfo is None:
            df_index = df_indexed.index.tz_localize('UTC')
        else:
            df_index = df_indexed.index
        return df_index.get_loc(ts)
    except KeyError:
        return None


def compute_stop_and_targets(pivot_highs, pivot_lows, direction, df_indexed, atr_val, stop_buffer_pct=STOP_BUFFER_PCT, min_rr=2.0):
    """محاسبه Stop Loss و Take Profit"""
    entry_price = float(df_indexed['close'].iloc[-1])
    
    if direction == "long":
        if len(pivot_lows) < 2:
            logger.warning("[STOP] Not enough pivot lows for LONG")
            return None, None, None
        
        pl_1, pl_2 = pivot_lows[-2], pivot_lows[-1]
        bar1 = resolve_bar_from_ts(df_indexed, pl_1['ts'])
        bar2 = resolve_bar_from_ts(df_indexed, pl_2['ts'])
        
        if bar1 is None or bar2 is None or bar2 <= bar1:
            logger.warning("[STOP] Invalid bar indices for LONG")
            return None, None, None
        
        stop_price = min(pl_1['price'], pl_2['price']) - stop_buffer_pct * atr_val
        
        try:
            mid_peak = df_indexed["high"].iloc[bar1+1:bar2].max()
            if pd.isna(mid_peak):
                logger.warning("[STOP] No mid peak found")
                return None, None, None
        except:
            return None, None, None
        
        target_price = float(mid_peak)
        
        risk = abs(entry_price - stop_price)
        reward = abs(target_price - entry_price)
        rr = reward / risk if risk > 0 else 0
        
        if rr < min_rr:
            target_price = entry_price + risk * min_rr
            logger.info(f"[STOP] LONG RRR={rr:.2f} < {min_rr}, target adjusted to {target_price:.4f}")
        
        logger.info(f"[STOP] LONG: entry={entry_price:.4f}, stop={stop_price:.4f}, target={target_price:.4f}, RRR={max(rr, min_rr):.2f}")
        return stop_price, target_price, mid_peak
        
    elif direction == "short":
        if len(pivot_highs) < 2:
            logger.warning("[STOP] Not enough pivot highs for SHORT")
            return None, None, None
        
        ph_1, ph_2 = pivot_highs[-2], pivot_highs[-1]
        bar1 = resolve_bar_from_ts(df_indexed, ph_1['ts'])
        bar2 = resolve_bar_from_ts(df_indexed, ph_2['ts'])
        
        if bar1 is None or bar2 is None or bar2 <= bar1:
            logger.warning("[STOP] Invalid bar indices for SHORT")
            return None, None, None
        
        stop_price = max(ph_1['price'], ph_2['price']) + stop_buffer_pct * atr_val
        
        try:
            mid_trough = df_indexed["low"].iloc[bar1+1:bar2].min()
            if pd.isna(mid_trough):
                logger.warning("[STOP] No mid trough found")
                return None, None, None
        except:
            return None, None, None
        
        target_price = float(mid_trough)
        
        risk = abs(stop_price - entry_price)
        reward = abs(entry_price - target_price)
        rr = reward / risk if risk > 0 else 0
        
        if rr < min_rr:
            target_price = entry_price - risk * min_rr
            logger.info(f"[STOP] SHORT RRR={rr:.2f} < {min_rr}, target adjusted to {target_price:.4f}")
        
        logger.info(f"[STOP] SHORT: entry={entry_price:.4f}, stop={stop_price:.4f}, target={target_price:.4f}, RRR={max(rr, min_rr):.2f}")
        return stop_price, target_price, mid_trough
    
    return None, None, None


def execute_order(exchange, symbol, signal, entry, stop, target, balance, score, label, pivot1, pivot2):
    """اجرای سفارش"""
    side_map = {"BUY": "LONG", "SELL": "SHORT"}
    leverage_map = {"LTCUSDT": 75, "DOGEUSDT": 75, "ETHUSDT": 50}
    
    if signal == "BUY":
        if stop >= entry:
            logger.warning(f"[ORDER] {symbol} LONG: stop ({stop}) >= entry ({entry}), adjusting...")
            stop = entry * 0.98
        if target <= entry:
            logger.warning(f"[ORDER] {symbol} LONG: target ({target}) <= entry ({entry}), adjusting...")
            target = entry * 1.05
    elif signal == "SELL":
        if stop <= entry:
            logger.warning(f"[ORDER] {symbol} SHORT: stop ({stop}) <= entry ({entry}), adjusting...")
            stop = entry * 1.02
        if target >= entry:
            logger.warning(f"[ORDER] {symbol} SHORT: target ({target}) >= entry ({entry}), adjusting...")
            target = entry * 0.95
    
    entry = exchange._round_price(entry, symbol)
    stop = exchange._round_price(stop, symbol)
    target = exchange._round_price(target, symbol)
    
    profit_pct = (target-entry)/entry*100 if signal=="BUY" else (entry-target)/entry*100
    loss_pct = (entry-stop)/entry*100 if signal=="BUY" else (stop-entry)/entry*100
    rr = abs(profit_pct/loss_pct) if loss_pct != 0 else 0
    direction_text = "LONG" if signal == "BUY" else "SHORT"
    
    signal_number = get_next_signal_number()
    
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
    
    signal_type = "CD+" if signal == "BUY" and "Classic" in label else "HD+" if signal == "BUY" else "CD-" if "Classic" in label else "HD-"
    
    pivot1_info = f"Pivot اول: قیمت {pivot1['price']:.4f} @ {pivot1['ts']} (RSI={pivot1['rsi']:.2f})" if pivot1 else "Pivot اول: نامشخص"
    pivot2_info = f"Pivot دوم: قیمت {pivot2['price']:.4f} @ {pivot2['ts']} (RSI={pivot2['rsi']:.2f})" if pivot2 else "Pivot دوم: نامشخص"
    
    emoji = "🔴" if signal == "SELL" else ("🟢" if "Classic" in label else "🔵")
    
    signal_message = (
        f"{emoji} {signal_type} — {symbol} #Signal_{signal_number}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Score: {score}/5\n"
        f"🔸 Direction: {direction_text}\n"
        f"📍 Entry: {entry:.{PRICE_PRECISION.get(symbol, 2)}f}\n"
        f"🛑 Stop Loss: {stop:.{PRICE_PRECISION.get(symbol, 2)}f}\n"
        f"🎯 Take Profit: {target:.{PRICE_PRECISION.get(symbol, 2)}f}\n"
        f"📈 Profit: +{profit_pct:.2f}% | 📉 Loss: -{loss_pct:.2f}%\n"
        f"⚖️ R/R: {rr:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Pivot‌ها:\n"
        f"• {pivot1_info}\n"
        f"• {pivot2_info}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕒 {format_iran_time()}"
    )
    
    send_telegram_message(signal_message)
    time.sleep(0.5)
    
    if exchange.connected:
        try:
            order_result = exchange.create_order(symbol, "market", side_map[signal], capital, None,
                {'leverage': int(used_leverage), 'stopLoss': stop, 'takeProfit': target})
            
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
                'pivot1_ts': str(pivot1['ts']) if pivot1 else None,
                'pivot1_price': pivot1['price'] if pivot1 else None,
                'pivot1_rsi': pivot1['rsi'] if pivot1 else None,
                'pivot2_ts': str(pivot2['ts']) if pivot2 else None,
                'pivot2_price': pivot2['price'] if pivot2 else None,
                'pivot2_rsi': pivot2['rsi'] if pivot2 else None
            })
            save_history(history)
            
            order_message = (
                f"✅ سفارش ثبت شد — {symbol} #سیگنال_{signal_number}\n"
                f"🔸 {side_map[signal]} | 💰 {capital:.2f} USDT | 🔧 {int(used_leverage)}x\n"
            )
            if capital_reduced:
                order_message += (
                    f"⚠️ سرمایه کاهش یافت! (لازم: {required_capital:.2f} | موجود: {balance:.2f})\n"
                )
            order_message += (
                f"🛑 {stop:.4f} | 🎯 {target:.4f}\n"
                f"📉 ریسک: {actual_risk:.2f} USDT | 📈 سود: {potential_profit:.2f} USDT\n"
                f"🕒 {format_iran_time()}"
            )
            send_telegram_message(order_message)
        except Exception as e:
            send_telegram_message(f"❌ خطا — {symbol} #سیگنال_{signal_number}\n{str(e)[:200]}\n🕒 {format_iran_time()}")
