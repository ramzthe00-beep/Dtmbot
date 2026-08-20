# -*- coding: utf-8 -*-
"""
توابع محاسباتی اندیکاتورها
"""

import pandas as pd
import numpy as np


def calc_rma(series, length):
    """معادل ta.rma در Pine Script"""
    n = len(series)
    rma = pd.Series(np.nan, index=series.index)
    if n == 0:
        return rma
    alpha = 1.0 / length
    vals = series.to_numpy(dtype=float)

    leading_na = 0
    while leading_na < n and np.isnan(vals[leading_na]):
        leading_na += 1

    seed_idx = leading_na + length - 1
    if seed_idx >= n:
        return rma

    prev = vals[seed_idx - length + 1: seed_idx + 1].mean()
    rma.iloc[seed_idx] = prev
    for i in range(seed_idx + 1, n):
        prev = alpha * vals[i] + (1 - alpha) * prev
        rma.iloc[i] = prev
    return rma


def calc_rsi(close, length=14):
    """معادل ta.rsi در Pine Script"""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = calc_rma(gain, length)
    avg_loss = calc_rma(loss, length)
    rsi = pd.Series(np.nan, index=close.index)
    for i in range(len(close)):
        ag = avg_gain.iloc[i]
        al = avg_loss.iloc[i]
        if pd.isna(ag) or pd.isna(al):
            continue
        if al == 0:
            rsi.iloc[i] = 100.0
        elif ag == 0:
            rsi.iloc[i] = 0.0
        else:
            rsi.iloc[i] = 100.0 - (100.0 / (1.0 + ag / al))
    return rsi


def calc_macd(close, fast=12, slow=26, signal=9):
    """معادل ta.macd در Pine Script"""
    def calc_ema(series, length):
        alpha = 2.0 / (length + 1)
        ema = pd.Series(np.nan, index=series.index)
        if len(series) == 0:
            return ema
        first_valid = series.first_valid_index()
        if first_valid is None:
            return ema
        start_pos = series.index.get_loc(first_valid)
        ema.iloc[start_pos] = series.iloc[start_pos]
        prev = ema.iloc[start_pos]
        for i in range(start_pos + 1, len(series)):
            prev = alpha * series.iloc[i] + (1 - alpha) * prev
            ema.iloc[i] = prev
        return ema
    
    ema_fast = calc_ema(close, fast)
    ema_slow = calc_ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calc_ema(macd_line, signal)
    hist_line = macd_line - signal_line
    return macd_line, signal_line, hist_line


def calc_atr(high, low, close, length=14):
    """معادل ta.atr در Pine Script"""
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return calc_rma(tr, length)


def calc_linreg(series, length, offset=0):
    """معادل ta.linreg در Pine Script"""
    result = pd.Series(np.nan, index=series.index)
    for i in range(length - 1, len(series)):
        y = series.iloc[i - length + 1: i + 1].values
        x = np.arange(length)
        slope, intercept = np.polyfit(x, y, 1)
        result.iloc[i] = intercept + slope * (length - 1 + offset)
    return result


def calc_sma(series, length):
    """معادل ta.sma در Pine Script"""
    return series.rolling(window=length).mean()


def calc_lowest(series, length):
    """معادل ta.lowest در Pine Script"""
    return series.rolling(window=length).min()


def calc_highest(series, length):
    """معادل ta.highest در Pine Script"""
    return series.rolling(window=length).max()
