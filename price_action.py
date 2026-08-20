# -*- coding: utf-8 -*-
"""
توابع Price Action و Candle Confirmation
"""

import pandas as pd
import numpy as np

from config import (
    SHADOW_TO_BODY_RATIO,
    MAX_OPPOSITE_SHADOW_PCT,
    MIN_CANDLE_ATR_RATIO,
    BIG_CANDLE_AVG_LEN,
    BIG_CANDLE_MULTIPLIER,
)


def candle_confirmation(
    open_series,
    close_series,
    high_series,
    low_series,
    atr_series,
    bigCandleAvgLen,
    shadowToBodyRatio,
    maxOppositeShadowPct,
    minCandleATRRatio,
    bigCandleMultiplier,
    pivot_ts
):
    """محاسبه کندل تأییدیه روی کندل Pivot"""
    try:
        pivot_pos = open_series.index.get_loc(pivot_ts)
    except KeyError:
        return False, False

    o = open_series.iloc[pivot_pos]
    c = close_series.iloc[pivot_pos]
    h = high_series.iloc[pivot_pos]
    l = low_series.iloc[pivot_pos]

    pivot_range = h - l
    pivot_body = abs(c - o)
    upper_shadow = h - max(c, o)
    lower_shadow = min(c, o) - l

    atr_at_pivot = atr_series.iloc[pivot_pos]

    if pivot_pos - bigCandleAvgLen >= 0:
        avg_body_at_pivot = abs(
            close_series.iloc[pivot_pos - bigCandleAvgLen: pivot_pos] - 
            open_series.iloc[pivot_pos - bigCandleAvgLen: pivot_pos]
        ).mean()
    else:
        avg_body_at_pivot = pivot_body

    pivot_size_ok = (
        pivot_range > 0
        and not pd.isna(atr_at_pivot)
        and pivot_range >= minCandleATRRatio * atr_at_pivot
    )

    bullish_wick = (
        pivot_size_ok
        and lower_shadow >= shadowToBodyRatio * pivot_body
        and (upper_shadow / pivot_range) * 100 <= maxOppositeShadowPct
    )

    big_green = (
        pivot_size_ok
        and not pd.isna(avg_body_at_pivot)
        and c > o
        and pivot_body >= bigCandleMultiplier * avg_body_at_pivot
    )

    bullishconfirmed = bullish_wick or big_green

    bearish_wick = (
        pivot_size_ok
        and upper_shadow >= shadowToBodyRatio * pivot_body
        and (lower_shadow / pivot_range) * 100 <= maxOppositeShadowPct
    )

    big_red = (
        pivot_size_ok
        and not pd.isna(avg_body_at_pivot)
        and c < o
        and pivot_body >= bigCandleMultiplier * avg_body_at_pivot
    )

    bearishconfirmed = bearish_wick or big_red

    return bullishconfirmed, bearishconfirmed
