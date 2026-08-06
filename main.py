# -*- coding: utf-8 -*-
"""
DTM Divergence Auto-Trading Bot - TheTrueTrade
====================================================================
ربات معاملاتی کاملاً خودکار روی صرافی TheTrueTrade، بر اساس استراتژی واگرایی DTM.
با ارسال سیگنال‌های معاملاتی و گزارش‌های دوره‌ای (صبح، نهار، شام) به تلگرام
"""

import os
import time
import threading
import traceback
import hashlib
import hmac
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from flask import Flask

# =====================================================================================
# کلیدهای API (بهتر است از متغیرهای محیطی خوانده شوند)
# =====================================================================================
API_KEY = os.getenv("API_KEY", "J_MHEOhlJ3xSL8SQGWsyNz8xrGSxk0wQvA8WmXSX")
API_SECRET = os.getenv("API_SECRET", "3a0f92c090ba32cfb0be29542c0ed5bb01fd35452cd191fd7e86817e82cd38cd")
BASE_URL = "https://apiv2.thetruetrade.io"

# =====================================================================================
# تنظیمات تلگرام (بهتر است از متغیرهای محیطی خوانده شوند)
# =====================================================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8514469828:AAFC76EiVA7I4TFiX08jJ5N6-eKtOLMKitE")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7402770612")

# =====================================================================================
# کلاس صرافی سفارشی برای TheTrueTrade
# =====================================================================================
class TrueTradeExchange:
    def __init__(self, api_key, api_secret, base_url):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self.session = requests.Session()

    def _sign_request(self, method, uri, timestamp):
        """ایجاد امضای HMAC-SHA256 برای درخواست بر اساس مستندات"""
        payload = f"{timestamp}{method.upper()}{uri}"
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            payload.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature

    def _request(self, method, uri, data=None):
        """ارسال درخواست به API با امضا و نمایش خطای کامل در صورت بروز مشکل"""
        timestamp = str(int(time.time() * 1000))
        signature = self._sign_request(method, uri, timestamp)

        headers = {
            "X-API-Key": self.api_key,
            "X-Timestamp": timestamp,
            "X-Signature": signature,
            "Content-Type": "application/json"
        }

        url = f"{self.base_url}{uri}"
        
        try:
            response = self.session.request(method, url, headers=headers, json=data, timeout=15)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            # چاپ خطای کامل برای دیباگ
            print(f"[API ERROR] {method} {uri} -> Status: {response.status_code}")
            print(f"Response Body: {response.text}")
            raise
        except requests.exceptions.RequestException as e:
            print(f"[REQUEST ERROR] {method} {uri} -> {e}")
            raise

    def fetch_ohlcv(self, symbol, timeframe='1m', limit=500):
        """دریافت داده‌های تاریخچه قیمت با فرمت صحیح (UDF)"""
        symbol_clean = symbol.upper()
        
        resolution_map = {
            "1m": "1",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "1h": "60",
            "4h": "240",
            "1d": "D",
            "1w": "W",
            "1M": "M"
        }
        resolution = resolution_map.get(timeframe, "1")
        
        to_timestamp = int(time.time())
        from_timestamp = to_timestamp - (limit * 60)
        
        uri = f"/futures/udf/history?symbol={symbol_clean}&resolution={resolution}&from={from_timestamp}&to={to_timestamp}&countback={limit}"
        
        try:
            data = self._request('GET', uri)
        except requests.exceptions.HTTPError as e:
            print(f"[OHLCV ERROR] {symbol}: {e.response.status_code} - {e.response.text}")
            return []
        
        if not data or data.get('s') != 'ok':
            return []
        
        ohlcv = []
        if data and 't' in data:
            for i in range(len(data['t'])):
                ohlcv.append([
                    data['t'][i] * 1000,
                    float(data['o'][i]),
                    float(data['h'][i]),
                    float(data['l'][i]),
                    float(data['c'][i]),
                    float(data['v'][i])
                ])
        return ohlcv

    def fetch_positions(self, symbols=None):
        """دریافت پوزیشن‌های باز"""
        uri = "/futures/positions"
        try:
            data = self._request('GET', uri)
            positions = []
            if isinstance(data, list):
                for pos in data:
                    if pos.get('status') == 'OPEN':
                        positions.append({
                            'symbol': pos['symbol'],
                            'side': pos['side'].lower(),
                            'contracts': float(pos['size']),
                            'entryPrice': float(pos.get('entryPrice', 0)),
                            'markPrice': float(pos.get('markPrice', 0)),
                            'leverage': int(pos.get('leverage', 1)),
                            'unrealizedPnL': float(pos.get('unrealizedPnL', 0))
                        })
            return positions
        except Exception as e:
            print(f"[POSITIONS ERROR] {e}")
            return []

    def create_order(self, symbol, order_type, side, amount, price=None, params=None):
        """ایجاد سفارش جدید"""
        symbol_clean = symbol.upper()
        side_upper = side.upper()
        trade_type = order_type.upper()
        
        order_data = {
            "symbol": symbol_clean,
            "side": side_upper,
            "tradeType": trade_type,
            "leverage": params.get('leverage', 1) if params else 1,
            "walletType": "debit"
        }
        
        if trade_type == "MARKET":
            order_data["cost"] = str(amount)
        else:
            order_data["size"] = str(amount)
            if price:
                order_data["price"] = str(price)
        
        if params:
            if 'stopLoss' in params:
                order_data["stopLoss"] = str(params['stopLoss'])
            if 'takeProfit' in params:
                order_data["takeProfit"] = str(params['takeProfit'])
        
        uri = "/futures/positions"
        result = self._request('POST', uri, order_data)
        
        return {
            'id': result.get('positionId'),
            'symbol': symbol,
            'side': side,
            'type': order_type,
            'price': price,
            'amount': amount,
            'status': 'closed' if result.get('positionId') else 'open'
        }

    def fetch_balance(self):
        """دریافت موجودی کیف پول"""
        try:
            uri = "/accounting/assets"
            data = self._request('GET', uri)
            if isinstance(data, list):
                for asset in data:
                    if asset.get('asset') == 'USDT' and asset.get('accountType') == 'futures':
                        return {
                            'total': float(asset.get('balance', 0)),
                            'locked': float(asset.get('lockedBalance', 0)),
                            'available': float(asset.get('balance', 0)) - float(asset.get('lockedBalance', 0))
                        }
            return {'total': 0, 'locked': 0, 'available': 0}
        except Exception as e:
            print(f"[BALANCE ERROR] {e}")
            return None

# =====================================================================================
# توابع ارسال پیام به تلگرام
# =====================================================================================
def send_telegram_message(message: str):
    """ارسال پیام به تلگرام"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")

def get_iran_time():
    """دریافت زمان ایران (UTC+3:30)"""
    return datetime.now(timezone(timedelta(hours=3, minutes=30)))

def format_iran_time(dt=None):
    """فرمت‌سازی زمان ایران"""
    if dt is None:
        dt = get_iran_time()
    return dt.strftime('%Y-%m-%d %H:%M:%S')

def get_meal_emoji():
    """دریافت شکلک مناسب بر اساس زمان فعلی"""
    now = get_iran_time()
    hour = now.hour
    if 5 <= hour < 10:
        return "🌅", "صبحانه"
    elif 10 <= hour < 16:
        return "🌞", "نهار"
    elif 16 <= hour < 22:
        return "🌆", "شام"
    else:
        return "🌙", "شب"

# =====================================================================================
# کلاس Config
# =====================================================================================
class Config:
    LEFT_BARS = 5
    RIGHT_BARS = 3
    RSI_LEN = 14
    MACD_FAST = 12
    MACD_SLOW = 26
    MACD_SIGNAL = 9
    ATR_LEN = 14
    TREND_LOOKBACK = 20
    TREND_SLOPE_MIN_PCT = 0.05
    ENABLE_HIDDEN = True
    MIN_CONFIRMATIONS_MODE = "3_confirmations"
    STOP_BUFFER_PCT = 0.05
    MAX_LOSS_USD = 3.5
    MIN_RR_RATIO = 2.0
    PYRAMIDING_MAX = 5
    TIMEFRAME = "1m"
    CANDLE_LIMIT = 500
    POLL_INTERVAL_SECONDS = 15
    SYMBOLS = ["LTCUSDT", "DOGEUSDT", "ETHUSDT"]

# =====================================================================================
# توابع محاسباتی استراتژی (تغییر نکرده)
# =====================================================================================
def calc_rsi(close: pd.Series, length: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1.0 / length, min_periods=length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / length, min_periods=length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

def calc_ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()

def calc_macd(close: pd.Series, fast: int, slow: int, signal: int):
    ema_fast = calc_ema(close, fast)
    ema_slow = calc_ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calc_ema(macd_line, signal)
    hist_line = macd_line - signal_line
    return macd_line, signal_line, hist_line

def calc_atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / length, min_periods=length, adjust=False).mean()

def find_pivot_high(high: pd.Series, left_bars: int, right_bars: int) -> pd.Series:
    n = len(high)
    result = pd.Series(np.nan, index=high.index)
    for i in range(left_bars, n - right_bars):
        window_left = high.iloc[i - left_bars:i]
        window_right = high.iloc[i + 1:i + right_bars + 1]
        center = high.iloc[i]
        if not (window_left >= center).any() and not (window_right >= center).any():
            result.iloc[i] = center
    return result

def find_pivot_low(low: pd.Series, left_bars: int, right_bars: int) -> pd.Series:
    n = len(low)
    result = pd.Series(np.nan, index=low.index)
    for i in range(left_bars, n - right_bars):
        window_left = low.iloc[i - left_bars:i]
        window_right = low.iloc[i + 1:i + right_bars + 1]
        center = low.iloc[i]
        if not (window_left <= center).any() and not (window_right <= center).any():
            result.iloc[i] = center
    return result

def check_color_change(hist_line: pd.Series, bar_start: int, bar_end: int, need_red_phase: bool) -> bool:
    if bar_start is None or bar_end is None or bar_end <= bar_start:
        return False
    segment = hist_line.iloc[bar_start + 1:bar_end]
    return (segment < 0).any() if need_red_phase else (segment > 0).any()

def is_trending_up(close: pd.Series, ref_bar: int, lookback: int, slope_min_pct: float) -> bool:
    if ref_bar is None or ref_bar - lookback < 0:
        return False
    y = close.iloc[ref_bar - lookback:ref_bar + 1].values
    if len(y) < 2:
        return False
    x = np.arange(len(y))
    slope = np.polyfit(x, y, 1)[0]
    avg = y.mean()
    if avg == 0:
        return False
    return (slope / avg) * 100 > slope_min_pct

def is_trending_down(close: pd.Series, ref_bar: int, lookback: int, slope_min_pct: float) -> bool:
    if ref_bar is None or ref_bar - lookback < 0:
        return False
    y = close.iloc[ref_bar - lookback:ref_bar + 1].values
    if len(y) < 2:
        return False
    x = np.arange(len(y))
    slope = np.polyfit(x, y, 1)[0]
    avg = y.mean()
    if avg == 0:
        return False
    return (slope / avg) * 100 < -slope_min_pct

# =====================================================================================
# کلاس وضعیت و توابع کمکی
# =====================================================================================
class SymbolState:
    def __init__(self):
        self.ph_price_2 = self.ph_price_1 = None
        self.ph_bar_2 = self.ph_bar_1 = None
        self.ph_rsi_2 = self.ph_rsi_1 = None
        self.ph_macdline_2 = self.ph_macdline_1 = None
        self.ph_hist_2 = self.ph_hist_1 = None

        self.pl_price_2 = self.pl_price_1 = None
        self.pl_bar_2 = self.pl_bar_1 = None
        self.pl_rsi_2 = self.pl_rsi_1 = None
        self.pl_macdline_2 = self.pl_macdline_1 = None
        self.pl_hist_2 = self.pl_hist_1 = None

        self.open_positions = []
        self.closed_positions = []

def compute_stop_and_targets(state: SymbolState, direction: str, df: pd.DataFrame, atr_val: float, cfg: Config):
    if direction == "long":
        if state.pl_price_1 is None or state.pl_price_2 is None:
            return None
        stop_price = min(state.pl_price_1, state.pl_price_2) - cfg.STOP_BUFFER_PCT * atr_val

        bar1, bar2 = state.pl_bar_1, state.pl_bar_2
        if bar1 is None or bar2 is None or bar2 <= bar1:
            return None
        mid_peak = df["high"].iloc[bar1 + 1:bar2].max() if bar2 > bar1 + 1 else df["high"].iloc[bar1:bar2 + 1].max()
        if pd.isna(mid_peak):
            return None
        return {"stop": stop_price, "tp1_raw": mid_peak}

    elif direction == "short":
        if state.ph_price_1 is None or state.ph_price_2 is None:
            return None
        stop_price = max(state.ph_price_1, state.ph_price_2) + cfg.STOP_BUFFER_PCT * atr_val

        bar1, bar2 = state.ph_bar_1, state.ph_bar_2
        if bar1 is None or bar2 is None or bar2 <= bar1:
            return None
        mid_trough = df["low"].iloc[bar1 + 1:bar2].min() if bar2 > bar1 + 1 else df["low"].iloc[bar1:bar2 + 1].min()
        if pd.isna(mid_trough):
            return None
        return {"stop": stop_price, "tp1_raw": mid_trough}

    return None

def resolve_final_target(entry_price: float, stop_price: float, tp1_raw: float, direction: str, cfg: Config) -> float:
    risk_dist = abs(entry_price - stop_price)
    if risk_dist <= 0:
        return tp1_raw
    reward_dist = abs(tp1_raw - entry_price)
    rr = reward_dist / risk_dist
    if rr >= cfg.MIN_RR_RATIO:
        return tp1_raw
    if direction == "long":
        return entry_price + risk_dist * cfg.MIN_RR_RATIO
    else:
        return entry_price - risk_dist * cfg.MIN_RR_RATIO

# =====================================================================================
# توابع اصلی معاملاتی
# =====================================================================================
def create_exchange():
    return TrueTradeExchange(API_KEY, API_SECRET, BASE_URL)

def fetch_ohlcv_df(exchange, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    raw = exchange.fetch_ohlcv(symbol, timeframe, limit)
    if not raw:
        return pd.DataFrame()
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df

def get_max_leverage_for_notional(exchange, symbol: str, notional_usd: float) -> int:
    return 10

def compute_qty_and_leverage(exchange, symbol: str, entry_price: float, stop_price: float, cfg: Config):
    risk_dist = abs(entry_price - stop_price)
    if risk_dist <= 0:
        return None, None

    qty = cfg.MAX_LOSS_USD / risk_dist
    notional_usd = qty * entry_price

    max_leverage = get_max_leverage_for_notional(exchange, symbol, notional_usd)
    return qty, max_leverage

def place_market_order_with_sl_tp(exchange, symbol: str, direction: str,
                                    qty: float, leverage: int, stop_price: float, target_price: float):
    side = "buy" if direction == "long" else "sell"
    params = {
        'leverage': leverage,
        'stopLoss': stop_price,
        'takeProfit': target_price
    }
    
    entry_order = exchange.create_order(symbol, "market", side, qty, None, params)
    return {"entry": entry_order, "stop": None, "tp": None}

# =====================================================================================
# تابع ارسال گزارش کامل (وعده‌های غذایی)
# =====================================================================================
def send_full_report(exchange, states, cfg):
    """ارسال گزارش کامل با وضعیت اتصال، داده، معاملات و موجودی"""
    try:
        meal_emoji, meal_name = get_meal_emoji()
        iran_time = format_iran_time()
        
        connection_status = "✅ متصل"
        try:
            exchange.fetch_positions()
        except:
            connection_status = "❌ قطع"
        
        data_status = "❌ داده دریافت نشد"
        try:
            test_data = fetch_ohlcv_df(exchange, cfg.SYMBOLS[0], cfg.TIMEFRAME, 10)
            if not test_data.empty:
                data_status = "✅ داده دریافت شد"
        except:
            data_status = "❌ خطا در دریافت داده"
        
        positions = exchange.fetch_positions()
        open_trades = len(positions)
        
        total_unrealized_pnl = 0.0
        for pos in positions:
            total_unrealized_pnl += float(pos.get('unrealizedPnL', 0))
        
        balance = exchange.fetch_balance()
        if balance:
            total_balance = balance['total']
            available_balance = balance['available']
        else:
            total_balance = 0.0
            available_balance = 0.0
        
        message = f"{meal_emoji} **گزارش {meal_name} - ربات DTM** {meal_emoji}\n"
        message += f"🕒 **زمان ایران:** {iran_time}\n"
        message += f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        message += f"📡 **وضعیت اتصال به صرافی:** {connection_status}\n"
        message += f"📊 **دریافت داده برای تحلیل:** {data_status}\n\n"
        
        if open_trades > 0:
            message += f"📈 **معاملات باز:** {open_trades} عدد\n"
            for pos in positions:
                sym = pos.get('symbol', 'N/A')
                side = pos.get('side', 'N/A')
                entry = float(pos.get('entryPrice', 0))
                mark = float(pos.get('markPrice', 0))
                pnl = float(pos.get('unrealizedPnL', 0))
                pnl_emoji = "🟢" if pnl >= 0 else "🔴"
                message += f"   • {sym} {side} | ورود: {entry:.4f} | فعلی: {mark:.4f} | {pnl_emoji} سود/ضرر: {pnl:.2f} USDT\n"
        else:
            message += f"📭 **هیچ معامله‌ای باز نیست.**\n"
        
        message += f"\n💰 **سود/ضرر تحقق‌نیافته کل:** {total_unrealized_pnl:.2f} USDT\n"
        message += f"🏦 **موجودی کیف پول (USDT):** {total_balance:.2f}\n"
        message += f"   • موجودی قابل استفاده: {available_balance:.2f}\n"
        
        send_telegram_message(message)
        print(f"[REPORT] گزارش {meal_name} ارسال شد - {iran_time}")
        
    except Exception as e:
        print(f"[REPORT ERROR] {e}")
        traceback.print_exc()

# =====================================================================================
# تابع پردازش نماد
# =====================================================================================
def process_symbol(exchange, symbol: str, state: SymbolState, cfg: Config):
    df = fetch_ohlcv_df(exchange, symbol, cfg.TIMEFRAME, cfg.CANDLE_LIMIT)
    if df.empty:
        print(f"[SKIP] {symbol}: داده‌ای برای تحلیل وجود ندارد")
        return
        
    closed_df = df.iloc[:-1].reset_index(drop=True)
    n = len(closed_df)
    if n < cfg.LEFT_BARS + cfg.RIGHT_BARS + cfg.TREND_LOOKBACK + 5:
        return

    close = closed_df["close"]
    high = closed_df["high"]
    low = closed_df["low"]

    rsi_val = calc_rsi(close, cfg.RSI_LEN)
    macd_line, signal_line, hist_line = calc_macd(close, cfg.MACD_FAST, cfg.MACD_SLOW, cfg.MACD_SIGNAL)
    atr14 = calc_atr(high, low, close, cfg.ATR_LEN)
    pivot_high = find_pivot_high(high, cfg.LEFT_BARS, cfg.RIGHT_BARS)
    pivot_low = find_pivot_low(low, cfg.LEFT_BARS, cfg.RIGHT_BARS)

    last_i = n - 1
    pivot_check_i = last_i - cfg.RIGHT_BARS
    if pivot_check_i < cfg.LEFT_BARS:
        return

    new_pivot_high = not pd.isna(pivot_high.iloc[pivot_check_i])
    new_pivot_low = not pd.isna(pivot_low.iloc[pivot_check_i])

    if new_pivot_high:
        state.ph_price_1, state.ph_bar_1 = state.ph_price_2, state.ph_bar_2
        state.ph_rsi_1, state.ph_macdline_1, state.ph_hist_1 = state.ph_rsi_2, state.ph_macdline_2, state.ph_hist_2
        state.ph_price_2 = pivot_high.iloc[pivot_check_i]
        state.ph_bar_2 = pivot_check_i
        state.ph_rsi_2 = rsi_val.iloc[pivot_check_i]
        state.ph_macdline_2 = macd_line.iloc[pivot_check_i]
        state.ph_hist_2 = hist_line.iloc[pivot_check_i]

    if new_pivot_low:
        state.pl_price_1, state.pl_bar_1 = state.pl_price_2, state.pl_bar_2
        state.pl_rsi_1, state.pl_macdline_1, state.pl_hist_1 = state.pl_rsi_2, state.pl_macdline_2, state.pl_hist_2
        state.pl_price_2 = pivot_low.iloc[pivot_check_i]
        state.pl_bar_2 = pivot_check_i
        state.pl_rsi_2 = rsi_val.iloc[pivot_check_i]
        state.pl_macdline_2 = macd_line.iloc[pivot_check_i]
        state.pl_hist_2 = hist_line.iloc[pivot_check_i]

    macd_color_changed_highs = check_color_change(hist_line, state.ph_bar_1, state.ph_bar_2, True) if new_pivot_high and state.ph_bar_1 is not None else False
    macd_color_changed_lows = check_color_change(hist_line, state.pl_bar_1, state.pl_bar_2, False) if new_pivot_low and state.pl_bar_1 is not None else False

    trend_ok_bearish = is_trending_up(close, state.ph_bar_1, cfg.TREND_LOOKBACK, cfg.TREND_SLOPE_MIN_PCT) if new_pivot_high and state.ph_bar_1 is not None else False
    trend_ok_bullish = is_trending_down(close, state.pl_bar_1, cfg.TREND_LOOKBACK, cfg.TREND_SLOPE_MIN_PCT) if new_pivot_low and state.pl_bar_1 is not None else False

    price_higher_high = new_pivot_high and state.ph_price_1 is not None and state.ph_price_2 > state.ph_price_1
    rsi_lower_high = new_pivot_high and state.ph_rsi_1 is not None and state.ph_rsi_2 < state.ph_rsi_1
    macdline_lower_high = new_pivot_high and state.ph_macdline_1 is not None and state.ph_macdline_2 < state.ph_macdline_1
    hist_lower_high = new_pivot_high and state.ph_hist_1 is not None and state.ph_hist_2 < state.ph_hist_1
    both_peaks_green = new_pivot_high and state.ph_hist_1 is not None and state.ph_hist_1 > 0 and state.ph_hist_2 > 0
    classic_bearish = price_higher_high and rsi_lower_high and macdline_lower_high and hist_lower_high and both_peaks_green and macd_color_changed_highs and trend_ok_bearish

    price_lower_low = new_pivot_low and state.pl_price_1 is not None and state.pl_price_2 < state.pl_price_1
    rsi_higher_low = new_pivot_low and state.pl_rsi_1 is not None and state.pl_rsi_2 > state.pl_rsi_1
    macdline_higher_low = new_pivot_low and state.pl_macdline_1 is not None and state.pl_macdline_2 > state.pl_macdline_1
    hist_higher_low = new_pivot_low and state.pl_hist_1 is not None and state.pl_hist_2 > state.pl_hist_1
    both_troughs_red = new_pivot_low and state.pl_hist_1 is not None and state.pl_hist_1 < 0 and state.pl_hist_2 < 0
    classic_bullish = price_lower_low and rsi_higher_low and macdline_higher_low and hist_higher_low and both_troughs_red and macd_color_changed_lows and trend_ok_bullish

    price_higher_low = new_pivot_low and state.pl_price_1 is not None and state.pl_price_2 > state.pl_price_1
    rsi_lower_low = new_pivot_low and state.pl_rsi_1 is not None and state.pl_rsi_2 < state.pl_rsi_1
    macdline_lower_low = new_pivot_low and state.pl_macdline_1 is not None and state.pl_macdline_2 < state.pl_macdline_1
    hist_lower_low = new_pivot_low and state.pl_hist_1 is not None and state.pl_hist_2 < state.pl_hist_1
    hidden_bullish = cfg.ENABLE_HIDDEN and price_higher_low and rsi_lower_low and macdline_lower_low and hist_lower_low and both_troughs_red and macd_color_changed_lows

    price_lower_high = new_pivot_high and state.ph_price_1 is not None and state.ph_price_2 < state.ph_price_1
    rsi_higher_high = new_pivot_high and state.ph_rsi_1 is not None and state.ph_rsi_2 > state.ph_rsi_1
    macdline_higher_high = new_pivot_high and state.ph_macdline_1 is not None and state.ph_macdline_2 > state.ph_macdline_1
    hist_higher_high = new_pivot_high and state.ph_hist_1 is not None and state.ph_hist_2 > state.ph_hist_1
    hidden_bearish = cfg.ENABLE_HIDDEN and price_lower_high and rsi_higher_high and macdline_higher_high and hist_higher_high and both_peaks_green and macd_color_changed_highs

    raw_long = classic_bullish or hidden_bullish
    raw_short = classic_bearish or hidden_bearish

    if not raw_long and not raw_short:
        return

    entry_price = close.iloc[last_i]

    open_long_count = sum(1 for p in state.open_positions if p["direction"] == "long")
    open_short_count = sum(1 for p in state.open_positions if p["direction"] == "short")

    if raw_long and open_long_count < cfg.PYRAMIDING_MAX:
        levels = compute_stop_and_targets(state, "long", closed_df, atr14.iloc[last_i], cfg)
        if levels:
            target = resolve_final_target(entry_price, levels["stop"], levels["tp1_raw"], "long", cfg)
            _execute_entry(exchange, symbol, "long", entry_price, levels["stop"], target, state, cfg)

    if raw_short and open_short_count < cfg.PYRAMIDING_MAX:
        levels = compute_stop_and_targets(state, "short", closed_df, atr14.iloc[last_i], cfg)
        if levels:
            target = resolve_final_target(entry_price, levels["stop"], levels["tp1_raw"], "short", cfg)
            _execute_entry(exchange, symbol, "short", entry_price, levels["stop"], target, state, cfg)

def _execute_entry(exchange, symbol, direction, entry_price, stop_price, target_price, state: SymbolState, cfg: Config):
    try:
        qty, leverage = compute_qty_and_leverage(exchange, symbol, entry_price, stop_price, cfg)
        if qty is None or qty <= 0:
            print(f"[SKIP] {symbol} {direction}: محاسبه حجم نامعتبر بود")
            return

        result = place_market_order_with_sl_tp(exchange, symbol, direction, qty, leverage, stop_price, target_price)

        state.open_positions.append({
            "symbol": symbol,
            "direction": direction, "entry": entry_price, "stop": stop_price,
            "target": target_price, "qty": qty, "leverage": leverage,
            "order_ids": result, "opened_at": datetime.now(timezone.utc),
        })
        
        iran_time = format_iran_time()
        message = f"✅ **معامله جدید باز شد**\n"
        message += f"🔹 **نماد:** {symbol}\n"
        message += f"🔸 **جهت:** {direction.upper()}\n"
        message += f"💰 **قیمت ورود:** {entry_price:.4f}\n"
        message += f"🛑 **حد ضرر:** {stop_price:.4f}\n"
        message += f"🎯 **حد سود:** {target_price:.4f}\n"
        message += f"📊 **اهرم:** {leverage}x\n"
        message += f"📦 **حجم:** {qty:.6f}\n"
        message += f"🕒 **زمان ایران:** {iran_time}"
        send_telegram_message(message)
        
        print(f"[ENTRY EXECUTED] {symbol} {direction.upper()} qty={qty:.6f} leverage={leverage}x "
              f"entry={entry_price} stop={stop_price} target={target_price}")

    except requests.exceptions.HTTPError as e:
        print(f"[HTTP ERROR] {symbol} {direction}: {e.response.status_code} - {e.response.text}")
    except requests.exceptions.RequestException as e:
        print(f"[REQUEST ERROR] {symbol} {direction}: {e}")
    except Exception as e:
        print(f"[UNEXPECTED ERROR during entry] {symbol} {direction}: {e}")
        traceback.print_exc()

def sync_closed_positions(exchange, symbol: str, state: SymbolState):
    try:
        positions = exchange.fetch_positions()
        live_symbols = [str(p.get('symbol', '')).upper() for p in positions]
        
        symbol_clean = symbol.upper()
        
        for pos in state.open_positions[:]:
            pos_sym = str(pos.get('symbol', symbol)).upper()
            if pos_sym == symbol_clean and pos_sym not in live_symbols:
                state.open_positions.remove(pos)
                if not hasattr(state, 'closed_positions'):
                    state.closed_positions = []
                state.closed_positions.append({
                    'symbol': symbol,
                    'closed_by': 'unknown',
                    'pnl': 0
                })
        
    except Exception as e:
        print(f"[SYNC ERROR] {symbol}: {e}")

# =====================================================================================
# راه‌اندازی Flask و حلقه اصلی
# =====================================================================================
app = Flask(__name__)

@app.route("/")
def health_check():
    return "DTM Divergence Trading Bot is running on TheTrueTrade.", 200

def run_flask():
    app.run(host="0.0.0.0", port=10000)

def trading_loop():
    cfg = Config()
    states = {symbol: SymbolState() for symbol in cfg.SYMBOLS}
    exchange = None
    consecutive_failures = 0
    max_backoff_seconds = 300
    
    last_meal_report = None
    last_meal_report_time = get_iran_time()

    while True:
        try:
            if exchange is None:
                print("[INIT] در حال اتصال به TheTrueTrade...")
                exchange = create_exchange()
                print("[INIT] اتصال برقرار شد.")
                consecutive_failures = 0

            for symbol in cfg.SYMBOLS:
                try:
                    sync_closed_positions(exchange, symbol, states[symbol])
                    process_symbol(exchange, symbol, states[symbol], cfg)
                except requests.exceptions.RequestException as e:
                    print(f"[REQUEST ERROR] {symbol}: {e} -- ادامه با نماد بعدی")
                except Exception as e:
                    print(f"[SYMBOL PROCESSING ERROR] {symbol}: {e}")
                    traceback.print_exc()
            
            now = get_iran_time()
            current_hour = now.hour
            
            meal_time = None
            if 6 <= current_hour <= 9:
                meal_time = "صبحانه"
            elif 12 <= current_hour <= 14:
                meal_time = "نهار"
            elif 19 <= current_hour <= 21:
                meal_time = "شام"
            
            if meal_time:
                if last_meal_report != meal_time or (now - last_meal_report_time).total_seconds() > 3600:
                    send_full_report(exchange, states, cfg)
                    last_meal_report = meal_time
                    last_meal_report_time = now

            time.sleep(cfg.POLL_INTERVAL_SECONDS)

        except requests.exceptions.RequestException as e:
            consecutive_failures += 1
            backoff = min(5 * (2 ** consecutive_failures), max_backoff_seconds)
            print(f"[CONNECTION LOST] {e} -- تلاش مجدد اتصال در {backoff} ثانیه (تلاش شماره {consecutive_failures})")
            exchange = None
            time.sleep(backoff)
        except Exception as e:
            consecutive_failures += 1
            backoff = min(10 * consecutive_failures, max_backoff_seconds)
            print(f"[UNEXPECTED FATAL ERROR] {e} -- تلاش مجدد در {backoff} ثانیه")
            traceback.print_exc()
            exchange = None
            time.sleep(backoff)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("[STARTUP] وب‌سرور Flask روی پورت 10000 راه‌اندازی شد.")
    
    iran_time = format_iran_time()
    send_telegram_message(f"🤖 **ربات معاملاتی DTM راه‌اندازی شد!**\n🕒 زمان ایران: {iran_time}\n📊 نمادها: {', '.join(Config.SYMBOLS)}")
    
    print("[STARTUP] شروع حلقه معاملاتی اصلی...")
    trading_loop()
