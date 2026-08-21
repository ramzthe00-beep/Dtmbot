# -*- coding: utf-8 -*-
"""
محاسبه اندیکاتورها با منطق نزدیک به Pine Script.

فرض:
- داده‌ها از قدیمی به جدید مرتب شده‌اند.
- index سری‌ها یکتا و هم‌تراز است.
- محاسبه فقط روی کندل‌های بسته‌شده انجام می‌شود.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _validate_length(length: int) -> None:
    if not isinstance(length, (int, np.integer)) or length < 1:
        raise ValueError(
            "length باید عدد صحیح بزرگ‌تر از صفر باشد."
        )


def _empty_like(series: pd.Series) -> pd.Series:
    return pd.Series(
        np.nan,
        index=series.index,
        dtype="float64",
    )


def calc_rma(
    series: pd.Series,
    length: int,
) -> pd.Series:
    """
    RMA با alpha = 1 / length.

    Seed با میانگین اولین length مقدار معتبر ساخته می‌شود.
    مقدارهای NaN و inf بین داده‌ها از محاسبه حذف می‌شوند.
    """

    _validate_length(length)

    result = _empty_like(series)
    values = series.astype(float).to_numpy()

    valid_count = 0
    seed_sum = 0.0
    previous = np.nan
    alpha = 1.0 / length

    for i, value in enumerate(values):
        if not np.isfinite(value):
            # Pine-style: مقدار قبلی حفظ می‌شود.
            if np.isfinite(previous):
                result.iloc[i] = previous
            continue

        valid_count += 1

        if valid_count < length:
            seed_sum += value
            continue

        if valid_count == length:
            seed_sum += value
            previous = seed_sum / length
            result.iloc[i] = previous
            continue

        previous = (
            alpha * value
            + (1.0 - alpha) * previous
        )

        result.iloc[i] = previous

    return result


def calc_rsi(
    close: pd.Series,
    length: int = 14,
) -> pd.Series:
    """
    محاسبه RSI بر پایه RMA/Wilder.

    Pine:
        ta.rsi(close, length)
    """

    _validate_length(length)

    close = close.astype(float)
    delta = close.diff()

    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = calc_rma(gain, length)
    avg_loss = calc_rma(loss, length)

    result = _empty_like(close)

    for i in range(len(close)):
        gain_value = avg_gain.iloc[i]
        loss_value = avg_loss.iloc[i]

        if not np.isfinite(gain_value) or not np.isfinite(loss_value):
            continue

        # مطابق الگوی رایج Pine برای RSI
        if loss_value == 0:
            result.iloc[i] = 100.0
        elif gain_value == 0:
            result.iloc[i] = 0.0
        else:
            rs = gain_value / loss_value
            result.iloc[i] = 100.0 - (
                100.0 / (1.0 + rs)
            )

    return result


def calc_ema(
    series: pd.Series,
    length: int,
) -> pd.Series:
    """
    EMA با Seed اولین مقدار معتبر (Pine Style).

    Pine:
        ema := na(ema[1]) ? source : alpha * source + (1 - alpha) * ema[1]
        alpha = 2 / (length + 1)
    """

    _validate_length(length)

    result = _empty_like(series)
    values = series.astype(float).to_numpy()

    alpha = 2.0 / (length + 1.0)
    previous = np.nan

    for i, value in enumerate(values):
        if not np.isfinite(value):
            if np.isfinite(previous):
                result.iloc[i] = previous
            continue

        if not np.isfinite(previous):
            previous = value
        else:
            previous = (
                alpha * value
                + (1.0 - alpha) * previous
            )

        result.iloc[i] = previous

    return result


def calc_macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
):
    """
    محاسبه MACD:

        MACD Line = EMA(fast) - EMA(slow)
        Signal    = EMA(MACD Line, signal)
        Histogram = MACD Line - Signal

    Pine:
        ta.macd(close, fast, slow, signal)
    """

    _validate_length(fast)
    _validate_length(slow)
    _validate_length(signal)

    if fast >= slow:
        raise ValueError(
            "fast باید کوچک‌تر از slow باشد."
        )

    close = close.astype(float)

    ema_fast = calc_ema(close, fast)
    ema_slow = calc_ema(close, slow)

    macd_line = ema_fast - ema_slow

    signal_line = calc_ema(
        macd_line,
        signal,
    )

    histogram = macd_line - signal_line

    return (
        macd_line,
        signal_line,
        histogram,
    )


def calc_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    length: int = 14,
) -> pd.Series:
    """
    ATR بر پایه True Range و RMA/Wilder.

    نکته:
    - کندل اول: True Range = high - low
    - اگر high یا low ناقص باشد، True Range نامعتبر می‌ماند.
    - اگر close قبلی ناقص باشد، فقط high - low استفاده می‌شود.

    Pine:
        ta.atr(high, low, close, length)
    """

    _validate_length(length)

    high = high.astype(float)
    low = low.astype(float)
    close = close.astype(float)

    true_range = pd.Series(
        np.nan,
        index=close.index,
        dtype="float64",
    )

    previous_close = close.shift(1)

    for i in range(len(close)):
        h = high.iloc[i]
        l = low.iloc[i]

        if not np.isfinite(h) or not np.isfinite(l):
            continue

        if i == 0:
            true_range.iloc[i] = h - l
            continue

        pc = previous_close.iloc[i]

        if not np.isfinite(pc):
            true_range.iloc[i] = h - l
            continue

        true_range.iloc[i] = max(
            h - l,
            abs(h - pc),
            abs(l - pc),
        )

    return calc_rma(true_range, length)


def calc_linreg(
    series: pd.Series,
    length: int,
    offset: int = 0,
) -> pd.Series:
    """
    معادل ta.linreg از نظر فرمول.

    Pine:
        intercept + slope * (length - 1 - offset)

    نکته:
    - offset=0 → مقدار خط در انتهای پنجره
    - offset=length-1 → مقدار خط در ابتدای پنجره
    """

    _validate_length(length)

    result = _empty_like(series)
    values = series.astype(float).to_numpy()

    if length == 1:
        for i, value in enumerate(values):
            if np.isfinite(value):
                result.iloc[i] = value
        return result

    x = np.arange(length, dtype=float)
    x_mean = x.mean()

    denominator = np.sum(
        (x - x_mean) ** 2
    )

    for i in range(length - 1, len(values)):
        y = values[
            i - length + 1:i + 1
        ]

        if not np.isfinite(y).all():
            continue

        y_mean = y.mean()

        slope = np.sum(
            (x - x_mean) * (y - y_mean)
        ) / denominator

        intercept = y_mean - slope * x_mean

        result.iloc[i] = (
            intercept
            + slope * (length - 1 - offset)
        )

    return result


def calc_sma(
    series: pd.Series,
    length: int,
) -> pd.Series:
    """
    SMA با حداقل length مقدار معتبر.

    Pine-like:
        NaN نادیده گرفته می‌شود.
        آخرین length مقدار معتبر استفاده می‌شود.
    """

    _validate_length(length)

    result = _empty_like(series)
    values = series.astype(float).to_numpy()

    valid_values = []

    for i, value in enumerate(values):
        if np.isfinite(value):
            valid_values.append(value)

        if len(valid_values) >= length:
            result.iloc[i] = (
                sum(valid_values[-length:]) / length
            )

    return result


def calc_lowest(
    series: pd.Series,
    length: int,
) -> pd.Series:
    """
    Lowest با حداقل length مقدار معتبر.

    Pine-like:
        NaN نادیده گرفته می‌شود.
        آخرین length مقدار معتبر استفاده می‌شود.
    """

    _validate_length(length)

    result = _empty_like(series)
    values = series.astype(float).to_numpy()

    valid_values = []

    for i, value in enumerate(values):
        if np.isfinite(value):
            valid_values.append(value)

        if len(valid_values) >= length:
            result.iloc[i] = min(
                valid_values[-length:]
            )

    return result


def calc_highest(
    series: pd.Series,
    length: int,
) -> pd.Series:
    """
    Highest با حداقل length مقدار معتبر.

    Pine-like:
        NaN نادیده گرفته می‌شود.
        آخرین length مقدار معتبر استفاده می‌شود.
    """

    _validate_length(length)

    result = _empty_like(series)
    values = series.astype(float).to_numpy()

    valid_values = []

    for i, value in enumerate(values):
        if np.isfinite(value):
            valid_values.append(value)

        if len(valid_values) >= length:
            result.iloc[i] = max(
                valid_values[-length:]
            )

    return result
