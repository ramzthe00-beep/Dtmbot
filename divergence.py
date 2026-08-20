# -*- coding: utf-8 -*-
"""
تابع تشخیص سیگنال واگرایی
"""

import logging
import pandas as pd

from config import (
    RSI_LEN,
    MACD_FAST,
    MACD_SLOW,
    MACD_SIG,
    TREND_LOOKBACK,
    MIN_CONFIRMATIONS,
    ENABLE_HIDDEN,
    ENABLE_MACD_COLOR_FILTER,
    LEFT_BARS,
    RIGHT_BARS,
    BIG_CANDLE_AVG_LEN,
    SHADOW_TO_BODY_RATIO,
    MAX_OPPOSITE_SHADOW_PCT,
    MIN_CANDLE_ATR_RATIO,
    BIG_CANDLE_MULTIPLIER,
    HISTORY_BARS,
    API_RETURNS_OPEN_CANDLE,
)

from indicators import calc_rsi, calc_macd, calc_atr
from pivots import find_pivot_high, find_pivot_low
from fibonacci import (
    is_trending_up,
    is_trending_down,
    find_trend_start_low,
    find_trend_start_high,
    check_fib_level,
)
from price_action import candle_confirmation
from utils import save_debug_log_to_file, format_iran_time

logger = logging.getLogger(__name__)


def histogram_changed_phase(hist_series, bar_start_ts, bar_end_ts):
    """بررسی تغییر واقعی فاز Histogram"""
    found = False
    if bar_start_ts is not None and bar_end_ts is not None and bar_end_ts > bar_start_ts:
        try:
            start_pos = hist_series.index.get_loc(bar_start_ts)
            end_pos = hist_series.index.get_loc(bar_end_ts)
            
            for i in range(start_pos + 1, end_pos):
                if i >= len(hist_series) or i + 1 >= len(hist_series):
                    break
                h1 = hist_series.iloc[i]
                h2 = hist_series.iloc[i + 1]
                
                if pd.isna(h1) or pd.isna(h2):
                    continue
                
                crossed_up = h1 > 0 and h2 <= 0
                crossed_down = h1 < 0 and h2 >= 0
                
                if crossed_up or crossed_down:
                    found = True
                    break
        except KeyError:
            pass
    return found


def passes_min_requirement(base3, fib_ok, pa_ok, color_filter_ok):
    """تابع نهایی تأییدها با فیلتر تغییر رنگ مستقل"""
    result = False
    if base3 and color_filter_ok:
        if MIN_CONFIRMATIONS == '۳ تعییدیه (حداقل مجاز)':
            result = True
        elif MIN_CONFIRMATIONS == '۳ تعییدیه + فیبوناچی (۴ امتیاز) [Custom]':
            result = fib_ok
        elif MIN_CONFIRMATIONS == '۳ تعییدیه + پرایس‌اکشن (۴ امتیاز) [Custom]':
            result = pa_ok
        elif MIN_CONFIRMATIONS == '۵ امتیاز کامل (ایده‌آل)':
            result = fib_ok and pa_ok
    return result


def detect_signal(df, state, symbol):
    """تابع تشخیص سیگنال"""
    debug_log = []
    debug_file_lines = []
    
    def log(msg):
        debug_log.append(msg)
        debug_file_lines.append(msg)
        logger.info(msg)
    
    log(f"🔍 DTM — {symbol} | {format_iran_time()}")
    
    if API_RETURNS_OPEN_CANDLE:
        closed_df = df.iloc[:-1].copy()
    else:
        closed_df = df.copy()
    
    if len(closed_df) > 0:
        last_bar_start = closed_df.index[-1]
        if last_bar_start.tzinfo is None:
            last_bar_start = last_bar_start.tz_localize('UTC')
        last_bar_end = last_bar_start + pd.Timedelta(minutes=1)
        now_utc = pd.Timestamp.now(tz='UTC')
        if now_utc < last_bar_end:
            closed_df = closed_df.iloc[:-1].copy()
    
    if len(closed_df) > HISTORY_BARS:
        closed_df = closed_df.tail(HISTORY_BARS)
    
    n = len(closed_df)
    if n < 33:
        log(f"❌ داده ناکافی: {n}")
        return None, None, None, None, False, None, None, 0, [], None, None
    
    close_series = closed_df["close"]
    high_series = closed_df["high"]
    low_series = closed_df["low"]
    open_series = closed_df["open"]
    
    # محاسبه اندیکاتورها
    rsi_val = calc_rsi(close_series, RSI_LEN)
    macd_line, signal_line, hist_line = calc_macd(close_series, MACD_FAST, MACD_SLOW, MACD_SIG)
    atr14 = calc_atr(high_series, low_series, close_series, 14)
    
    # محاسبه pivotها
    pivot_high = find_pivot_high(high_series, LEFT_BARS, RIGHT_BARS)
    pivot_low = find_pivot_low(low_series, LEFT_BARS, RIGHT_BARS)
    
    # آخرین کندل تأیید شده
    last_confirmed_pos = n - 1 - RIGHT_BARS
    last_confirmed_ts = closed_df.index[last_confirmed_pos]
    
    # بازیابی state
    ph_price_2 = state.ph_price_2
    ph_price_1 = state.ph_price_1
    ph_ts_2 = state.ph_ts_2
    ph_ts_1 = state.ph_ts_1
    ph_bar_2 = state.ph_bar_2
    ph_bar_1 = state.ph_bar_1
    ph_rsi_2 = state.ph_rsi_2
    ph_rsi_1 = state.ph_rsi_1
    ph_macdline_2 = state.ph_macdline_2
    ph_macdline_1 = state.ph_macdline_1
    ph_hist_2 = state.ph_hist_2
    ph_hist_1 = state.ph_hist_1
    
    pl_price_2 = state.pl_price_2
    pl_price_1 = state.pl_price_1
    pl_ts_2 = state.pl_ts_2
    pl_ts_1 = state.pl_ts_1
    pl_bar_2 = state.pl_bar_2
    pl_bar_1 = state.pl_bar_1
    pl_rsi_2 = state.pl_rsi_2
    pl_rsi_1 = state.pl_rsi_1
    pl_macdline_2 = state.pl_macdline_2
    pl_macdline_1 = state.pl_macdline_1
    pl_hist_2 = state.pl_hist_2
    pl_hist_1 = state.pl_hist_1
    
    new_pivot_high = False
    new_pivot_low = False
    pivot_processed_high = False
    pivot_processed_low = False
    
    # تشخیص پیوت جدید
    if not pd.isna(pivot_high.iloc[last_confirmed_pos]):
        real_pivot_pos = last_confirmed_pos - RIGHT_BARS
        
        if state.last_processed_pivot_bar == real_pivot_pos:
            log(f"   ⏭️ Pivot High {real_pivot_pos} قبلاً پردازش شده")
        else:
            ph_price_1 = ph_price_2
            ph_ts_1 = ph_ts_2
            ph_bar_1 = ph_bar_2
            ph_rsi_1 = ph_rsi_2
            ph_macdline_1 = ph_macdline_2
            ph_hist_1 = ph_hist_2
            
            real_pivot_ts = closed_df.index[real_pivot_pos]
            
            ph_price_2 = float(pivot_high.iloc[last_confirmed_pos])
            ph_ts_2 = real_pivot_ts
            ph_bar_2 = real_pivot_pos
            ph_rsi_2 = float(rsi_val.iloc[real_pivot_pos])
            ph_macdline_2 = float(macd_line.iloc[real_pivot_pos])
            ph_hist_2 = float(hist_line.iloc[real_pivot_pos])
            new_pivot_high = True
            pivot_processed_high = True
            
            state.ph_price_2 = ph_price_2
            state.ph_price_1 = ph_price_1
            state.ph_ts_2 = ph_ts_2
            state.ph_ts_1 = ph_ts_1
            state.ph_bar_2 = ph_bar_2
            state.ph_bar_1 = ph_bar_1
            state.ph_rsi_2 = ph_rsi_2
            state.ph_rsi_1 = ph_rsi_1
            state.ph_macdline_2 = ph_macdline_2
            state.ph_macdline_1 = ph_macdline_1
            state.ph_hist_2 = ph_hist_2
            state.ph_hist_1 = ph_hist_1
            
            state.pivot_highs.append({
                'price': ph_price_2,
                'ts': ph_ts_2
            })
            if len(state.pivot_highs) > 10:
                state.pivot_highs = state.pivot_highs[-10:]
            
            logger.info(f"[PIVOT] {symbol} New Pivot High: price={ph_price_2:.4f}, ts={ph_ts_2}")
    
    if not pd.isna(pivot_low.iloc[last_confirmed_pos]):
        real_pivot_pos = last_confirmed_pos - RIGHT_BARS
        
        if state.last_processed_pivot_bar == real_pivot_pos:
            log(f"   ⏭️ Pivot Low {real_pivot_pos} قبلاً پردازش شده")
        else:
            pl_price_1 = pl_price_2
            pl_ts_1 = pl_ts_2
            pl_bar_1 = pl_bar_2
            pl_rsi_1 = pl_rsi_2
            pl_macdline_1 = pl_macdline_2
            pl_hist_1 = pl_hist_2
            
            real_pivot_ts = closed_df.index[real_pivot_pos]
            
            pl_price_2 = float(pivot_low.iloc[last_confirmed_pos])
            pl_ts_2 = real_pivot_ts
            pl_bar_2 = real_pivot_pos
            pl_rsi_2 = float(rsi_val.iloc[real_pivot_pos])
            pl_macdline_2 = float(macd_line.iloc[real_pivot_pos])
            pl_hist_2 = float(hist_line.iloc[real_pivot_pos])
            new_pivot_low = True
            pivot_processed_low = True
            
            state.pl_price_2 = pl_price_2
            state.pl_price_1 = pl_price_1
            state.pl_ts_2 = pl_ts_2
            state.pl_ts_1 = pl_ts_1
            state.pl_bar_2 = pl_bar_2
            state.pl_bar_1 = pl_bar_1
            state.pl_rsi_2 = pl_rsi_2
            state.pl_rsi_1 = pl_rsi_1
            state.pl_macdline_2 = pl_macdline_2
            state.pl_macdline_1 = pl_macdline_1
            state.pl_hist_2 = pl_hist_2
            state.pl_hist_1 = pl_hist_1
            
            state.pivot_lows.append({
                'price': pl_price_2,
                'ts': pl_ts_2
            })
            if len(state.pivot_lows) > 10:
                state.pivot_lows = state.pivot_lows[-10:]
            
            logger.info(f"[PIVOT] {symbol} New Pivot Low: price={pl_price_2:.4f}, ts={pl_ts_2}")
    
    if pivot_processed_high or pivot_processed_low:
        state.last_processed_pivot_bar = last_confirmed_pos - RIGHT_BARS
    
    state.last_processed_ts = last_confirmed_ts
    early_signal = new_pivot_high or new_pivot_low
    
    log(f"   n={n}, last_confirmed={last_confirmed_ts}")
    log(f"   new_high={1 if new_pivot_high else 0}, new_low={1 if new_pivot_low else 0}")
    
    # فیلتر مستقل Histogram
    histogram_phase_changed_for_highs = False
    histogram_phase_changed_for_lows = False
    
    if new_pivot_high and ph_ts_1 is not None:
        histogram_phase_changed_for_highs = histogram_changed_phase(
            hist_line, ph_ts_1, ph_ts_2
        )
    
    if new_pivot_low and pl_ts_1 is not None:
        histogram_phase_changed_for_lows = histogram_changed_phase(
            hist_line, pl_ts_1, pl_ts_2
        )
    
    best_signal = None
    best_entry = None
    best_stop = None
    best_target = None
    best_emoji = None
    best_label = None
    best_score = 0
    best_details = []
    best_pivot1 = None
    best_pivot2 = None
    
    # ============================================================
    # 1. Classic Bearish
    # ============================================================
    if new_pivot_high and ph_ts_1 is not None:
        price_higher_high = ph_price_2 > ph_price_1
        rsi_lower_high = ph_rsi_2 < ph_rsi_1
        macd_lower_high = ph_macdline_2 < ph_macdline_1
        hist_lower_high = ph_hist_2 < ph_hist_1
        both_peaks_green = ph_hist_1 > 0 and ph_hist_2 > 0
        
        trend_ok = is_trending_up(close_series, ph_ts_1)
        
        fib_ok = False
        trend_start = find_trend_start_low(low_series, ph_ts_1)
        fib_ok = check_fib_level(trend_start, ph_price_1, ph_price_2, True)
        
        classic_bearish_cond1_rsi = price_higher_high and rsi_lower_high
        classic_bearish_cond2_macdl = price_higher_high and macd_lower_high
        classic_bearish_cond3_macdh = price_higher_high and hist_lower_high and both_peaks_green
        classic_bearish_base3 = price_higher_high and trend_ok and classic_bearish_cond3_macdh and classic_bearish_cond1_rsi and classic_bearish_cond2_macdl
        
        macd_color_filter_bearish = (
            (not ENABLE_MACD_COLOR_FILTER)
            or (both_peaks_green and histogram_phase_changed_for_highs)
        )
        
        pa_bullish, pa_bearish = candle_confirmation(
            open_series, close_series, high_series, low_series, atr14,
            BIG_CANDLE_AVG_LEN, SHADOW_TO_BODY_RATIO, MAX_OPPOSITE_SHADOW_PCT,
            MIN_CANDLE_ATR_RATIO, BIG_CANDLE_MULTIPLIER, ph_ts_2
        )
        pa_ok = pa_bearish
        
        score = (
            (1 if classic_bearish_cond1_rsi else 0)
            + (1 if classic_bearish_cond2_macdl else 0)
            + (1 if classic_bearish_cond3_macdh else 0)
            + (1 if fib_ok else 0)
            + (1 if pa_ok else 0)
        )
        
        log(f"   🔴 CD- check | PH1={ph_price_1:.4f} (RSI={ph_rsi_1:.2f}) → PH2={ph_price_2:.4f} (RSI={ph_rsi_2:.2f})")
        log(f"      ColorFilter: {'✅' if macd_color_filter_bearish else '❌'} (phase_changed={histogram_phase_changed_for_highs})")
        
        if passes_min_requirement(classic_bearish_base3, fib_ok, pa_ok, macd_color_filter_bearish):
            entry_price = float(close_series.iloc[-1])
            
            from strategy_engine import compute_stop_and_targets
            stop_price, target_price, mid_peak = compute_stop_and_targets(
                state.pivot_highs, state.pivot_lows, "short", 
                closed_df, atr14.iloc[-1]
            )
            
            if stop_price is not None and target_price is not None:
                state.stored_short_stop = stop_price
                state.stored_short_tp = target_price
                
                details = [
                    f"✅ priceHigherHigh and rsiLowerHighOnPeaks",
                    f"✅ priceHigherHigh and macdLineLowerHighOnPeaks",
                    f"✅ priceHigherHigh and histLowerHighOnPeaks and bothPeaksGreen",
                    f"✅ trendOkForBearish",
                    f"✅ fibScoreBearish" if fib_ok else "❌ fibScoreBearish",
                    f"✅ priceActionBearishAtPivot" if pa_ok else "❌ priceActionBearishAtPivot",
                    f"✅ macdColorFilter" if macd_color_filter_bearish else "❌ macdColorFilter"
                ]
                
                if score > best_score:
                    best_signal = "SELL"
                    best_entry = entry_price
                    best_stop = stop_price
                    best_target = target_price
                    best_emoji = "🔴"
                    best_label = "Classic Bearish"
                    best_score = score
                    best_details = details
                    best_pivot1 = {'price': ph_price_1, 'rsi': ph_rsi_1, 'macdline': ph_macdline_1, 'hist': ph_hist_1, 'ts': ph_ts_1}
                    best_pivot2 = {'price': ph_price_2, 'rsi': ph_rsi_2, 'macdline': ph_macdline_2, 'hist': ph_hist_2, 'ts': ph_ts_2}
        else:
            log(f"   🔴 CD- score={score}/5")
            log(f"      {'✅' if rsi_lower_high else '❌'} RSI")
            log(f"      {'✅' if macd_lower_high else '❌'} MACD Line")
            log(f"      {'✅' if hist_lower_high and both_peaks_green else '❌'} MACD Histogram")
            log(f"      {'✅' if trend_ok else '❌'} Trend")
            log(f"      {'✅' if macd_color_filter_bearish else '❌'} Color Filter")
            log(f"      ❌ Base3 برقرار نیست")
    
    # ============================================================
    # 2. Classic Bullish
    # ============================================================
    if new_pivot_low and pl_ts_1 is not None:
        price_lower_low = pl_price_2 < pl_price_1
        rsi_higher_low = pl_rsi_2 > pl_rsi_1
        macd_higher_low = pl_macdline_2 > pl_macdline_1
        hist_higher_low = pl_hist_2 > pl_hist_1
        both_troughs_red = pl_hist_1 < 0 and pl_hist_2 < 0
        
        trend_ok = is_trending_down(close_series, pl_ts_1)
        
        fib_ok = False
        trend_start = find_trend_start_high(high_series, pl_ts_1)
        fib_ok = check_fib_level(trend_start, pl_price_1, pl_price_2, False)
        
        classic_bullish_cond1_rsi = price_lower_low and rsi_higher_low
        classic_bullish_cond2_macdl = price_lower_low and macd_higher_low
        classic_bullish_cond3_macdh = price_lower_low and hist_higher_low and both_troughs_red
        classic_bullish_base3 = price_lower_low and trend_ok and classic_bullish_cond3_macdh and classic_bullish_cond1_rsi and classic_bullish_cond2_macdl
        
        macd_color_filter_bullish = (
            (not ENABLE_MACD_COLOR_FILTER)
            or (both_troughs_red and histogram_phase_changed_for_lows)
        )
        
        pa_bullish, pa_bearish = candle_confirmation(
            open_series, close_series, high_series, low_series, atr14,
            BIG_CANDLE_AVG_LEN, SHADOW_TO_BODY_RATIO, MAX_OPPOSITE_SHADOW_PCT,
            MIN_CANDLE_ATR_RATIO, BIG_CANDLE_MULTIPLIER, pl_ts_2
        )
        pa_ok = pa_bullish
        
        score = (
            (1 if classic_bullish_cond1_rsi else 0)
            + (1 if classic_bullish_cond2_macdl else 0)
            + (1 if classic_bullish_cond3_macdh else 0)
            + (1 if fib_ok else 0)
            + (1 if pa_ok else 0)
        )
        
        log(f"   🟢 CD+ check | PL1={pl_price_1:.4f} (RSI={pl_rsi_1:.2f}) → PL2={pl_price_2:.4f} (RSI={pl_rsi_2:.2f})")
        log(f"      ColorFilter: {'✅' if macd_color_filter_bullish else '❌'} (phase_changed={histogram_phase_changed_for_lows})")
        
        if passes_min_requirement(classic_bullish_base3, fib_ok, pa_ok, macd_color_filter_bullish):
            entry_price = float(close_series.iloc[-1])
            
            from strategy_engine import compute_stop_and_targets
            stop_price, target_price, mid_trough = compute_stop_and_targets(
                state.pivot_highs, state.pivot_lows, "long", 
                closed_df, atr14.iloc[-1]
            )
            
            if stop_price is not None and target_price is not None:
                state.stored_long_stop = stop_price
                state.stored_long_tp = target_price
                
                details = [
                    f"✅ priceLowerLow and rsiHigherLowOnTroughs",
                    f"✅ priceLowerLow and macdLineHigherLowOnTroughs",
                    f"✅ priceLowerLow and histHigherLowOnTroughs and bothTroughsRed",
                    f"✅ trendOkForBullish",
                    f"✅ fibScoreBullish" if fib_ok else "❌ fibScoreBullish",
                    f"✅ priceActionBullishAtPivot" if pa_ok else "❌ priceActionBullishAtPivot",
                    f"✅ macdColorFilter" if macd_color_filter_bullish else "❌ macdColorFilter"
                ]
                
                if score > best_score:
                    best_signal = "BUY"
                    best_entry = entry_price
                    best_stop = stop_price
                    best_target = target_price
                    best_emoji = "🟢"
                    best_label = "Classic Bullish"
                    best_score = score
                    best_details = details
                    best_pivot1 = {'price': pl_price_1, 'rsi': pl_rsi_1, 'macdline': pl_macdline_1, 'hist': pl_hist_1, 'ts': pl_ts_1}
                    best_pivot2 = {'price': pl_price_2, 'rsi': pl_rsi_2, 'macdline': pl_macdline_2, 'hist': pl_hist_2, 'ts': pl_ts_2}
        else:
            log(f"   🟢 CD+ score={score}/5")
            log(f"      {'✅' if rsi_higher_low else '❌'} RSI")
            log(f"      {'✅' if macd_higher_low else '❌'} MACD Line")
            log(f"      {'✅' if hist_higher_low and both_troughs_red else '❌'} MACD Histogram")
            log(f"      {'✅' if trend_ok else '❌'} Trend")
            log(f"      {'✅' if macd_color_filter_bullish else '❌'} Color Filter")
            log(f"      ❌ Base3 برقرار نیست")
    
    # ============================================================
    # 3. Hidden Bullish
    # ============================================================
    if new_pivot_low and pl_ts_1 is not None and ENABLE_HIDDEN:
        price_higher_low = pl_price_2 > pl_price_1
        rsi_lower_low = pl_rsi_2 < pl_rsi_1
        macd_lower_low = pl_macdline_2 < pl_macdline_1
        hist_lower_low = pl_hist_2 < pl_hist_1
        both_troughs_red = pl_hist_1 < 0 and pl_hist_2 < 0
        
        trend_ok = is_trending_up(close_series, pl_ts_1)
        
        fib_ok = False
        trend_start = find_trend_start_high(high_series, pl_ts_1)
        fib_ok = check_fib_level(trend_start, pl_price_1, pl_price_2, False)
        
        hidden_bullish_cond1_rsi = price_higher_low and rsi_lower_low
        hidden_bullish_cond2_macdl = price_higher_low and macd_lower_low
        hidden_bullish_cond3_macdh = price_higher_low and hist_lower_low and both_troughs_red
        hidden_bullish_base3 = price_higher_low and hidden_bullish_cond3_macdh and hidden_bullish_cond1_rsi and hidden_bullish_cond2_macdl
        
        macd_color_filter_bullish = (
            (not ENABLE_MACD_COLOR_FILTER)
            or (both_troughs_red and histogram_phase_changed_for_lows)
        )
        
        pa_bullish, pa_bearish = candle_confirmation(
            open_series, close_series, high_series, low_series, atr14,
            BIG_CANDLE_AVG_LEN, SHADOW_TO_BODY_RATIO, MAX_OPPOSITE_SHADOW_PCT,
            MIN_CANDLE_ATR_RATIO, BIG_CANDLE_MULTIPLIER, pl_ts_2
        )
        pa_ok = pa_bullish
        
        score = (
            (1 if hidden_bullish_cond1_rsi else 0)
            + (1 if hidden_bullish_cond2_macdl else 0)
            + (1 if hidden_bullish_cond3_macdh else 0)
            + (1 if fib_ok else 0)
            + (1 if pa_ok else 0)
        )
        
        log(f"   🔵 HD+ check | PL1={pl_price_1:.4f} (RSI={pl_rsi_1:.2f}) → PL2={pl_price_2:.4f} (RSI={pl_rsi_2:.2f})")
        log(f"      ColorFilter: {'✅' if macd_color_filter_bullish else '❌'} (phase_changed={histogram_phase_changed_for_lows})")
        
        if passes_min_requirement(hidden_bullish_base3, fib_ok, pa_ok, macd_color_filter_bullish):
            entry_price = float(close_series.iloc[-1])
            
            from strategy_engine import compute_stop_and_targets
            stop_price, target_price, mid_trough = compute_stop_and_targets(
                state.pivot_highs, state.pivot_lows, "long", 
                closed_df, atr14.iloc[-1]
            )
            
            if stop_price is not None and target_price is not None:
                state.stored_long_stop = stop_price
                state.stored_long_tp = target_price
                
                details = [
                    f"✅ priceHigherLow and rsiLowerLowOnTroughs",
                    f"✅ priceHigherLow and macdLineLowerLowOnTroughs",
                    f"✅ priceHigherLow and histLowerLowOnTroughs and bothTroughsRed",
                    f"✅ trendOkForBullish" if trend_ok else "❌ trendOkForBullish",
                    f"✅ fibScoreBullish" if fib_ok else "❌ fibScoreBullish",
                    f"✅ priceActionBullishAtPivot" if pa_ok else "❌ priceActionBullishAtPivot",
                    f"✅ macdColorFilter" if macd_color_filter_bullish else "❌ macdColorFilter"
                ]
                
                if score > best_score:
                    best_signal = "BUY"
                    best_entry = entry_price
                    best_stop = stop_price
                    best_target = target_price
                    best_emoji = "🔵"
                    best_label = "Hidden Bullish"
                    best_score = score
                    best_details = details
                    best_pivot1 = {'price': pl_price_1, 'rsi': pl_rsi_1, 'macdline': pl_macdline_1, 'hist': pl_hist_1, 'ts': pl_ts_1}
                    best_pivot2 = {'price': pl_price_2, 'rsi': pl_rsi_2, 'macdline': pl_macdline_2, 'hist': pl_hist_2, 'ts': pl_ts_2}
        else:
            log(f"   🔵 HD+ score={score}/5")
            log(f"      {'✅' if rsi_lower_low else '❌'} RSI")
            log(f"      {'✅' if macd_lower_low else '❌'} MACD Line")
            log(f"      {'✅' if hist_lower_low and both_troughs_red else '❌'} MACD Histogram")
            log(f"      {'✅' if macd_color_filter_bullish else '❌'} Color Filter")
            log(f"      ❌ Base3 برقرار نیست")
    
    # ============================================================
    # 4. Hidden Bearish
    # ============================================================
    if new_pivot_high and ph_ts_1 is not None and ENABLE_HIDDEN:
        price_lower_high = ph_price_2 < ph_price_1
        rsi_higher_high = ph_rsi_2 > ph_rsi_1
        macd_higher_high = ph_macdline_2 > ph_macdline_1
        hist_higher_high = ph_hist_2 > ph_hist_1
        both_peaks_green = ph_hist_1 > 0 and ph_hist_2 > 0
        
        trend_ok = is_trending_down(close_series, ph_ts_1)
        
        fib_ok = False
        trend_start = find_trend_start_low(low_series, ph_ts_1)
        fib_ok = check_fib_level(trend_start, ph_price_1, ph_price_2, True)
        
        hidden_bearish_cond1_rsi = price_lower_high and rsi_higher_high
        hidden_bearish_cond2_macdl = price_lower_high and macd_higher_high
        hidden_bearish_cond3_macdh = price_lower_high and hist_higher_high and both_peaks_green
        hidden_bearish_base3 = price_lower_high and hidden_bearish_cond3_macdh and hidden_bearish_cond1_rsi and hidden_bearish_cond2_macdl
        
        macd_color_filter_bearish = (
            (not ENABLE_MACD_COLOR_FILTER)
            or (both_peaks_green and histogram_phase_changed_for_highs)
        )
        
        pa_bullish, pa_bearish = candle_confirmation(
            open_series, close_series, high_series, low_series, atr14,
            BIG_CANDLE_AVG_LEN, SHADOW_TO_BODY_RATIO, MAX_OPPOSITE_SHADOW_PCT,
            MIN_CANDLE_ATR_RATIO, BIG_CANDLE_MULTIPLIER, ph_ts_2
        )
        pa_ok = pa_bearish
        
        score = (
            (1 if hidden_bearish_cond1_rsi else 0)
            + (1 if hidden_bearish_cond2_macdl else 0)
            + (1 if hidden_bearish_cond3_macdh else 0)
            + (1 if fib_ok else 0)
            + (1 if pa_ok else 0)
        )
        
        log(f"   🟠 HD- check | PH1={ph_price_1:.4f} (RSI={ph_rsi_1:.2f}) → PH2={ph_price_2:.4f} (RSI={ph_rsi_2:.2f})")
        log(f"      ColorFilter: {'✅' if macd_color_filter_bearish else '❌'} (phase_changed={histogram_phase_changed_for_highs})")
        
        if passes_min_requirement(hidden_bearish_base3, fib_ok, pa_ok, macd_color_filter_bearish):
            entry_price = float(close_series.iloc[-1])
            
            from strategy_engine import compute_stop_and_targets
            stop_price, target_price, mid_peak = compute_stop_and_targets(
                state.pivot_highs, state.pivot_lows, "short", 
                closed_df, atr14.iloc[-1]
            )
            
            if stop_price is not None and target_price is not None:
                state.stored_short_stop = stop_price
                state.stored_short_tp = target_price
                
                details = [
                    f"✅ priceLowerHigh and rsiHigherHighOnPeaks",
                    f"✅ priceLowerHigh and macdLineHigherHighOnPeaks",
                    f"✅ priceLowerHigh and histHigherHighOnPeaks and bothPeaksGreen",
                    f"✅ trendOkForBearish" if trend_ok else "❌ trendOkForBearish",
                    f"✅ fibScoreBearish" if fib_ok else "❌ fibScoreBearish",
                    f"✅ priceActionBearishAtPivot" if pa_ok else "❌ priceActionBearishAtPivot",
                    f"✅ macdColorFilter" if macd_color_filter_bearish else "❌ macdColorFilter"
                ]
                
                if score > best_score:
                    best_signal = "SELL"
                    best_entry = entry_price
                    best_stop = stop_price
                    best_target = target_price
                    best_emoji = "🟠"
                    best_label = "Hidden Bearish"
                    best_score = score
                    best_details = details
                    best_pivot1 = {'price': ph_price_1, 'rsi': ph_rsi_1, 'macdline': ph_macdline_1, 'hist': ph_hist_1, 'ts': ph_ts_1}
                    best_pivot2 = {'price': ph_price_2, 'rsi': ph_rsi_2, 'macdline': ph_macdline_2, 'hist': ph_hist_2, 'ts': ph_ts_2}
        else:
            log(f"   🟠 HD- score={score}/5")
            log(f"      {'✅' if rsi_higher_high else '❌'} RSI")
            log(f"      {'✅' if macd_higher_high else '❌'} MACD Line")
            log(f"      {'✅' if hist_higher_high and both_peaks_green else '❌'} MACD Histogram")
            log(f"      {'✅' if macd_color_filter_bearish else '❌'} Color Filter")
            log(f"      ❌ Base3 برقرار نیست")
    
    if best_signal is None:
        log(f"   ⚪ No signal (none passed Base3 + ColorFilter)")
    
    save_debug_log_to_file(symbol, debug_file_lines)
    
    if best_signal is not None and best_stop is not None and best_target is not None:
        return (best_signal, best_entry, best_stop, best_target, early_signal, 
                best_emoji, best_label, best_score, best_details, best_pivot1, best_pivot2)
    
    return None, None, None, None, early_signal, None, None, 0, [], None, None
