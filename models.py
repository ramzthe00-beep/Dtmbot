# -*- coding: utf-8 -*-
"""
مدل‌های داده و کلاس وضعیت
"""

import pandas as pd
import numpy as np


class SymbolState:
    def __init__(self):
        # Pivot High state
        self.ph_price_2 = None
        self.ph_price_1 = None
        self.ph_ts_2 = None
        self.ph_ts_1 = None
        self.ph_bar_2 = None
        self.ph_bar_1 = None
        self.ph_rsi_2 = None
        self.ph_rsi_1 = None
        self.ph_macdline_2 = None
        self.ph_macdline_1 = None
        self.ph_hist_2 = None
        self.ph_hist_1 = None
        
        # Pivot Low state
        self.pl_price_2 = None
        self.pl_price_1 = None
        self.pl_ts_2 = None
        self.pl_ts_1 = None
        self.pl_bar_2 = None
        self.pl_bar_1 = None
        self.pl_rsi_2 = None
        self.pl_rsi_1 = None
        self.pl_macdline_2 = None
        self.pl_macdline_1 = None
        self.pl_hist_2 = None
        self.pl_hist_1 = None
        
        # لیست pivot ها برای محاسبه stop و target
        self.pivot_highs = []
        self.pivot_lows = []
        
        # Stored Stop و Target برای مدیریت موقعیت
        self.stored_long_stop = None
        self.stored_short_stop = None
        self.stored_long_tp = None
        self.stored_short_tp = None
        
        self.last_processed_ts = None
        self.last_processed_pivot_bar = None
        self.alert_sent = False

    def to_dict(self):
        return {
            'ph_price_2': self.ph_price_2,
            'ph_price_1': self.ph_price_1,
            'ph_ts_2': str(self.ph_ts_2) if self.ph_ts_2 else None,
            'ph_ts_1': str(self.ph_ts_1) if self.ph_ts_1 else None,
            'ph_bar_2': self.ph_bar_2,
            'ph_bar_1': self.ph_bar_1,
            'ph_rsi_2': self.ph_rsi_2,
            'ph_rsi_1': self.ph_rsi_1,
            'ph_macdline_2': self.ph_macdline_2,
            'ph_macdline_1': self.ph_macdline_1,
            'ph_hist_2': self.ph_hist_2,
            'ph_hist_1': self.ph_hist_1,
            'pl_price_2': self.pl_price_2,
            'pl_price_1': self.pl_price_1,
            'pl_ts_2': str(self.pl_ts_2) if self.pl_ts_2 else None,
            'pl_ts_1': str(self.pl_ts_1) if self.pl_ts_1 else None,
            'pl_bar_2': self.pl_bar_2,
            'pl_bar_1': self.pl_bar_1,
            'pl_rsi_2': self.pl_rsi_2,
            'pl_rsi_1': self.pl_rsi_1,
            'pl_macdline_2': self.pl_macdline_2,
            'pl_macdline_1': self.pl_macdline_1,
            'pl_hist_2': self.pl_hist_2,
            'pl_hist_1': self.pl_hist_1,
            'pivot_highs': [
                {'price': p['price'], 'ts': str(p['ts'])} 
                for p in self.pivot_highs
            ] if self.pivot_highs else [],
            'pivot_lows': [
                {'price': p['price'], 'ts': str(p['ts'])} 
                for p in self.pivot_lows
            ] if self.pivot_lows else [],
            'stored_long_stop': self.stored_long_stop,
            'stored_short_stop': self.stored_short_stop,
            'stored_long_tp': self.stored_long_tp,
            'stored_short_tp': self.stored_short_tp,
            'last_processed_ts': str(self.last_processed_ts) if self.last_processed_ts else None,
            'last_processed_pivot_bar': self.last_processed_pivot_bar,
            'alert_sent': self.alert_sent
        }

    @classmethod
    def from_dict(cls, data):
        state = cls()
        if data:
            state.ph_price_2 = data.get('ph_price_2')
            state.ph_price_1 = data.get('ph_price_1')
            state.ph_ts_2 = pd.Timestamp(data['ph_ts_2']) if data.get('ph_ts_2') else None
            state.ph_ts_1 = pd.Timestamp(data['ph_ts_1']) if data.get('ph_ts_1') else None
            state.ph_bar_2 = data.get('ph_bar_2')
            state.ph_bar_1 = data.get('ph_bar_1')
            state.ph_rsi_2 = data.get('ph_rsi_2')
            state.ph_rsi_1 = data.get('ph_rsi_1')
            state.ph_macdline_2 = data.get('ph_macdline_2')
            state.ph_macdline_1 = data.get('ph_macdline_1')
            state.ph_hist_2 = data.get('ph_hist_2')
            state.ph_hist_1 = data.get('ph_hist_1')
            state.pl_price_2 = data.get('pl_price_2')
            state.pl_price_1 = data.get('pl_price_1')
            state.pl_ts_2 = pd.Timestamp(data['pl_ts_2']) if data.get('pl_ts_2') else None
            state.pl_ts_1 = pd.Timestamp(data['pl_ts_1']) if data.get('pl_ts_1') else None
            state.pl_bar_2 = data.get('pl_bar_2')
            state.pl_bar_1 = data.get('pl_bar_1')
            state.pl_rsi_2 = data.get('pl_rsi_2')
            state.pl_rsi_1 = data.get('pl_rsi_1')
            state.pl_macdline_2 = data.get('pl_macdline_2')
            state.pl_macdline_1 = data.get('pl_macdline_1')
            state.pl_hist_2 = data.get('pl_hist_2')
            state.pl_hist_1 = data.get('pl_hist_1')
            
            # بازسازی pivot_highs و pivot_lows
            state.pivot_highs = []
            for p in data.get('pivot_highs', []):
                state.pivot_highs.append({
                    'price': p['price'],
                    'ts': pd.Timestamp(p['ts']) if p.get('ts') else None
                })
            
            state.pivot_lows = []
            for p in data.get('pivot_lows', []):
                state.pivot_lows.append({
                    'price': p['price'],
                    'ts': pd.Timestamp(p['ts']) if p.get('ts') else None
                })
            
            state.stored_long_stop = data.get('stored_long_stop')
            state.stored_short_stop = data.get('stored_short_stop')
            state.stored_long_tp = data.get('stored_long_tp')
            state.stored_short_tp = data.get('stored_short_tp')
            state.last_processed_ts = pd.Timestamp(data['last_processed_ts']) if data.get('last_processed_ts') else None
            state.last_processed_pivot_bar = data.get('last_processed_pivot_bar')
            state.alert_sent = data.get('alert_sent', False)
        return state
