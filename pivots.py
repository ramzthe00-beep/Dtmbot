# -*- coding: utf-8 -*-
"""
توابع تشخیص Pivot - هماهنگ با Pine Script
نسخه بهینه‌سازی شده با مدیریت کامل NaN و محدوده
"""

import pandas as pd
import numpy as np

from config import LEFT_BARS, RIGHT_BARS


def find_pivot_high(high, left_bars=LEFT_BARS, right_bars=RIGHT_BARS):
    """
    شبیه‌سازی ta.pivothigh در Pine Script
    
    نکات:
    - Pivot در کندل i شناسایی می‌شود
    - مقدار آن در کندل تأیید (i + right_bars) ذخیره می‌شود
    - مدیریت کامل NaN
    - جلوگیری از Out of Bounds
    - استفاده از وکتوریزه برای سرعت بیشتر
    """
    n = len(high)
    result = pd.Series(np.nan, index=high.index, dtype=float)

    for i in range(left_bars, n - right_bars):
        candidate = high.iloc[i]
        
        # بررسی NaN برای کاندید
        if pd.isna(candidate):
            continue

        # استخراج پنجره‌ها
        left_window = high.iloc[i - left_bars:i]
        right_window = high.iloc[i + 1:i + right_bars + 1]

        # بررسی NaN در پنجره‌ها
        if left_window.isna().any() or right_window.isna().any():
            continue

        # بررسی شرط Pivot High
        # استفاده از <= یعنی برابری مجاز است
        left_ok = (left_window <= candidate).all()
        right_ok = (right_window <= candidate).all()

        if left_ok and right_ok:
            confirmation_index = i + right_bars
            
            # جلوگیری از Out of Bounds
            if confirmation_index < n:
                result.iloc[confirmation_index] = candidate

    return result


def find_pivot_low(low, left_bars=LEFT_BARS, right_bars=RIGHT_BARS):
    """
    شبیه‌سازی ta.pivotlow در Pine Script
    
    نکات:
    - Pivot در کندل i شناسایی می‌شود
    - مقدار آن در کندل تأیید (i + right_bars) ذخیره می‌شود
    - مدیریت کامل NaN
    - جلوگیری از Out of Bounds
    - استفاده از وکتوریزه برای سرعت بیشتر
    """
    n = len(low)
    result = pd.Series(np.nan, index=low.index, dtype=float)

    for i in range(left_bars, n - right_bars):
        candidate = low.iloc[i]
        
        # بررسی NaN برای کاندید
        if pd.isna(candidate):
            continue

        # استخراج پنجره‌ها
        left_window = low.iloc[i - left_bars:i]
        right_window = low.iloc[i + 1:i + right_bars + 1]

        # بررسی NaN در پنجره‌ها
        if left_window.isna().any() or right_window.isna().any():
            continue

        # بررسی شرط Pivot Low
        # استفاده از >= یعنی برابری مجاز است
        left_ok = (left_window >= candidate).all()
        right_ok = (right_window >= candidate).all()

        if left_ok and right_ok:
            confirmation_index = i + right_bars
            
            # جلوگیری از Out of Bounds
            if confirmation_index < n:
                result.iloc[confirmation_index] = candidate

    return result
