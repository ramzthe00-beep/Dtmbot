# -*- coding: utf-8 -*-
"""
توابع تشخیص Pivot
"""

import pandas as pd
import numpy as np

from config import LEFT_BARS, RIGHT_BARS


def find_pivot_high(high, left_bars=LEFT_BARS, right_bars=RIGHT_BARS):
    """
    معادل دقیق ta.pivothigh در Pine Script
    استفاده از > به جای >= برای تطبیق با Pine
    """
    n = len(high)
    result = pd.Series(np.nan, index=high.index, dtype=float)

    for i in range(left_bars, n - right_bars):
        candidate = high.iloc[i]

        left_ok = True
        for j in range(1, left_bars + 1):
            if high.iloc[i - j] > candidate:
                left_ok = False
                break

        if not left_ok:
            continue

        right_ok = True
        for j in range(1, right_bars + 1):
            if high.iloc[i + j] > candidate:
                right_ok = False
                break

        if right_ok:
            result.iloc[i] = candidate

    return result


def find_pivot_low(low, left_bars=LEFT_BARS, right_bars=RIGHT_BARS):
    """
    معادل دقیق ta.pivotlow در Pine Script
    استفاده از < به جای <= برای تطبیق با Pine
    """
    n = len(low)
    result = pd.Series(np.nan, index=low.index, dtype=float)

    for i in range(left_bars, n - right_bars):
        candidate = low.iloc[i]

        left_ok = True
        for j in range(1, left_bars + 1):
            if low.iloc[i - j] < candidate:
                left_ok = False
                break

        if not left_ok:
            continue

        right_ok = True
        for j in range(1, right_bars + 1):
            if low.iloc[i + j] < candidate:
                right_ok = False
                break

        if right_ok:
            result.iloc[i] = candidate

    return result
