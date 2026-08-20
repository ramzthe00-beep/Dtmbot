# -*- coding: utf-8 -*-
"""
توابع تشخیص Pivot - هماهنگ با Pine Script
"""

import pandas as pd
import numpy as np

from config import LEFT_BARS, RIGHT_BARS


def find_pivot_high(high, left_bars=LEFT_BARS, right_bars=RIGHT_BARS):
    """
    شبیه‌سازی ta.pivothigh در Pine Script
    
    نکته مهم:
    - Pivot در کندل i شناسایی می‌شود
    - اما مقدار آن در کندل تأیید (i + right_bars) ذخیره می‌شود
    - این دقیقاً معادل رفتار Pine است
    """
    n = len(high)
    result = pd.Series(np.nan, index=high.index, dtype=float)

    for i in range(left_bars, n - right_bars):
        candidate = high.iloc[i]
        
        # بررسی NaN
        if pd.isna(candidate):
            continue

        # بررسی سمت چپ
        left_ok = True
        for j in range(1, left_bars + 1):
            left_val = high.iloc[i - j]
            if pd.isna(left_val) or left_val > candidate:
                left_ok = False
                break

        if not left_ok:
            continue

        # بررسی سمت راست
        right_ok = True
        for j in range(1, right_bars + 1):
            right_val = high.iloc[i + j]
            if pd.isna(right_val) or right_val > candidate:
                right_ok = False
                break

        if right_ok:
            # ✅ ذخیره در کندل تأیید (i + right_bars)
            confirmation_index = i + right_bars
            result.iloc[confirmation_index] = candidate

    return result


def find_pivot_low(low, left_bars=LEFT_BARS, right_bars=RIGHT_BARS):
    """
    شبیه‌سازی ta.pivotlow در Pine Script
    
    نکته مهم:
    - Pivot در کندل i شناسایی می‌شود
    - اما مقدار آن در کندل تأیید (i + right_bars) ذخیره می‌شود
    - این دقیقاً معادل رفتار Pine است
    """
    n = len(low)
    result = pd.Series(np.nan, index=low.index, dtype=float)

    for i in range(left_bars, n - right_bars):
        candidate = low.iloc[i]
        
        # بررسی NaN
        if pd.isna(candidate):
            continue

        # بررسی سمت چپ
        left_ok = True
        for j in range(1, left_bars + 1):
            left_val = low.iloc[i - j]
            if pd.isna(left_val) or left_val < candidate:
                left_ok = False
                break

        if not left_ok:
            continue

        # بررسی سمت راست
        right_ok = True
        for j in range(1, right_bars + 1):
            right_val = low.iloc[i + j]
            if pd.isna(right_val) or right_val < candidate:
                right_ok = False
                break

        if right_ok:
            # ✅ ذخیره در کندل تأیید (i + right_bars)
            confirmation_index = i + right_bars
            result.iloc[confirmation_index] = candidate

    return result
