# -*- coding: utf-8 -*-
"""
توابع Fibonacci و Trend
"""

import pandas as pd
import numpy as np

from config import (
    TREND_LOOKBACK,
    TREND_SLOPE_MIN_PCT,
    FIB_USE_618,
    FIB_USE_786,
    FIB_TOLERANCE_PCT,
    FIB_TREND_SEARCH_BARS,
)


def calc_linreg_pine(series, start_index, length):
    """معادل دقیق ta.linreg در Pine Script"""
    window = series.iloc[start_index : start_index + length]

    if len(window) < length:
        return np.nan

    y = window.values
    x = np.arange(length)

    slope, intercept = np.polyfit(x, y, 1)

    return intercept + slope * (length - 1)


def is_trending_up(close_series, ref_ts):
    """معادل دقیق isTrendingUp در Pine Script"""
    if ref_ts is None:
        return False

    try:
        offset = close_series.index.get_loc(ref_ts)

        if offset < 0 or offset + TREND_LOOKBACK * 2 >= len(close_series):
            return False

        linreg_current = calc_linreg_pine(close_series, offset, TREND_LOOKBACK)
        linreg_past = calc_linreg_pine(close_series, offset + TREND_LOOKBACK, TREND_LOOKBACK)

        slope = linreg_current - linreg_past

        avg_price = close_series.iloc[offset : offset + TREND_LOOKBACK].mean()

        slope_pct = (slope / avg_price) * 100 if avg_price != 0 else 0.0

        return slope_pct > TREND_SLOPE_MIN_PCT

    except (KeyError, IndexError):
        return False


def is_trending_down(close_series, ref_ts):
    """معادل دقیق isTrendingDown در Pine Script"""
    if ref_ts is None:
        return False

    try:
        offset = close_series.index.get_loc(ref_ts)

        if offset < 0 or offset + TREND_LOOKBACK * 2 >= len(close_series):
            return False

        linreg_current = calc_linreg_pine(close_series, offset, TREND_LOOKBACK)
        linreg_past = calc_linreg_pine(close_series, offset + TREND_LOOKBACK, TREND_LOOKBACK)

        slope = linreg_current - linreg_past

        avg_price = close_series.iloc[offset : offset + TREND_LOOKBACK].mean()

        slope_pct = (slope / avg_price) * 100 if avg_price != 0 else 0.0

        return slope_pct < -TREND_SLOPE_MIN_PCT

    except (KeyError, IndexError):
        return False


def find_trend_start_low(low_series, ref_ts):
    """کمترین low در بازه تا refBar بدون Look-ahead"""
    if ref_ts is None:
        return None

    try:
        ref_bar = low_series.index.get_loc(ref_ts)
        current_index = len(low_series) - 1
        
        start = ref_bar - FIB_TREND_SEARCH_BARS + 1
        end = ref_bar + 1

        if start < 0 or end > current_index + 1:
            return None

        window = low_series.iloc[start:end]
        result = window.min()

        return None if pd.isna(result) else float(result)

    except (KeyError, IndexError):
        return None


def find_trend_start_high(high_series, ref_ts):
    """بیشترین high در بازه تا refBar بدون Look-ahead"""
    if ref_ts is None:
        return None

    try:
        ref_bar = high_series.index.get_loc(ref_ts)
        current_index = len(high_series) - 1
        
        start = ref_bar - FIB_TREND_SEARCH_BARS + 1
        end = ref_bar + 1

        if start < 0 or end > current_index + 1:
            return None

        window = high_series.iloc[start:end]
        result = window.max()

        return None if pd.isna(result) else float(result)

    except (KeyError, IndexError):
        return None


def check_fib_level(fib_start, fib_end, target_price, is_retrace_down):
    """بررسی سطح Fibonacci با تلورانس"""
    if fib_start is None or fib_end is None or target_price is None:
        return False
    if fib_start == fib_end:
        return False

    ok = False

    if is_retrace_down:
        distance = fib_end - fib_start
        
        if distance <= 0:
            return False
        
        level618 = fib_end - distance * 0.618
        level786 = fib_end - distance * 0.786
    else:
        distance = fib_start - fib_end
        
        if distance <= 0:
            return False
        
        level618 = fib_end + distance * 0.618
        level786 = fib_end + distance * 0.786

    tolerance = distance * (FIB_TOLERANCE_PCT / 100.0)

    valid618 = FIB_USE_618 and abs(target_price - level618) <= tolerance
    valid786 = FIB_USE_786 and abs(target_price - level786) <= tolerance

    ok = valid618 or valid786

    return ok
