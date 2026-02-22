import pandas as pd
import pandas_ta as ta
import yfinance as yf
import numpy as np
from tabulate import tabulate

def fetch_data(symbol, interval="30m", period="60d"):
    """
    Fetches historical data from yfinance. 60d is max for 30m interval.
    """
    print(f"Fetching data for {symbol}...")
    try:
        df = yf.download(symbol, interval=interval, period=period, progress=False)
        if df.empty:
            print(f"No data found for {symbol}")
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # Convert index to IST
        df.index = df.index.tz_convert('Asia/Kolkata')
        return df
    except Exception as e:
        print(f"Error fetching data for {symbol}: {e}")
        return None

def calculate_indicators(df):
    if len(df) < 50:
        return None

    df = df.copy()
    df['EMA_50'] = ta.ema(df['Close'], length=50)
    df['EMA_20'] = ta.ema(df['Close'], length=20)
    df['RSI'] = ta.rsi(df['Close'], length=14)

    macd = ta.macd(df['Close'], fast=12, slow=26, signal=9)
    if macd is not None:
        df['MACD'] = macd['MACD_12_26_9']
        df['MACD_Signal'] = macd['MACDs_12_26_9']
    else:
        return None

    df['ATR'] = ta.atr(df['High'], df['Low'], df['Close'], length=14)
    df['Vol_SMA'] = ta.sma(df['Volume'], length=20)

    return df

def generate_signals(df, reverse=True, use_volume_filter=False):
    df = df.copy()
    df['Prev_MACD'] = df['MACD'].shift(1)
    df['Prev_MACD_Signal'] = df['MACD_Signal'].shift(1)

    # Volume filter (optional as per user setup)
    vol_ok = (df['Volume'] > df['Vol_SMA']) if use_volume_filter and (df['Volume'].sum() > 0) else True

    # Original Conditions
    # Buy: Price > EMA50, RSI 50-65, MACD CrossUp > 0
    orig_buy = (
        (df['Close'] > df['EMA_50']) &
        (df['RSI'] >= 50) & (df['RSI'] <= 65) &
        (df['MACD'] > df['MACD_Signal']) & (df['Prev_MACD'] <= df['Prev_MACD_Signal']) &
        (df['MACD'] > 0) &
        vol_ok
    )

    # Sell: Price < EMA50, RSI 35-50, MACD CrossDown < 0
    orig_sell = (
        (df['Close'] < df['EMA_50']) &
        (df['RSI'] >= 35) & (df['RSI'] <= 50) &
        (df['MACD'] < df['MACD_Signal']) & (df['Prev_MACD'] >= df['Prev_MACD_Signal']) &
        (df['MACD'] < 0) &
        vol_ok
    )

    if reverse:
        df['Buy_Signal'] = orig_sell
        df['Sell_Signal'] = orig_buy
    else:
        df['Buy_Signal'] = orig_buy
        df['Sell_Signal'] = orig_sell

    return df

def backtest(df, symbol, use_trailing=True):
    trades = []
    in_position = False
    pos = None

    for i in range(1, len(df)):
        if in_position:
            current_high = df['High'].iloc[i]
            current_low = df['Low'].iloc[i]
            current_close = df['Close'].iloc[i]
            ema_20 = df['EMA_20'].iloc[i]
            current_time = df.index[i]

            if pos['type'] == 'Long':
                sl_hit = current_low <= pos['stop_loss']
                tp_hit = current_high >= pos['target']
                trail_hit = use_trailing and (current_close < ema_20)

                if sl_hit or tp_hit or trail_hit:
                    exit_price = pos['stop_loss'] if sl_hit else (pos['target'] if tp_hit else current_close)
                    result = 'SL' if sl_hit else ('TP' if tp_hit else 'Trail')
                    trades.append({
                        'Symbol': symbol, 'Type': 'Long', 'Entry Time': pos['entry_time'],
                        'Entry Price': pos['entry_price'], 'Exit Time': current_time,
                        'Exit Price': exit_price, 'PnL %': ((exit_price - pos['entry_price']) / pos['entry_price']) * 100,
                        'Result': result
                    })
                    in_position = False
            elif pos['type'] == 'Short':
                sl_hit = current_high >= pos['stop_loss']
                tp_hit = current_low <= pos['target']
                trail_hit = use_trailing and (current_close > ema_20)

                if sl_hit or tp_hit or trail_hit:
                    exit_price = pos['stop_loss'] if sl_hit else (pos['target'] if tp_hit else current_close)
                    result = 'SL' if sl_hit else ('TP' if tp_hit else 'Trail')
                    trades.append({
                        'Symbol': symbol, 'Type': 'Short', 'Entry Time': pos['entry_time'],
                        'Entry Price': pos['entry_price'], 'Exit Time': current_time,
                        'Exit Price': exit_price, 'PnL %': ((pos['entry_price'] - exit_price) / pos['entry_price']) * 100,
                        'Result': result
                    })
                    in_position = False

        if not in_position:
            if df['Buy_Signal'].iloc[i]:
                in_position = True
                entry_price = df['Close'].iloc[i]
                atr = df['ATR'].iloc[i]
                sl = entry_price - atr
                tp = entry_price + (2 * atr)
                pos = {'type': 'Long', 'entry_price': entry_price, 'stop_loss': sl, 'target': tp, 'entry_time': df.index[i]}
            elif df['Sell_Signal'].iloc[i]:
                in_position = True
                entry_price = df['Close'].iloc[i]
                atr = df['ATR'].iloc[i]
                sl = entry_price + atr
                tp = entry_price - (2 * atr)
                pos = {'type': 'Short', 'entry_price': entry_price, 'stop_loss': sl, 'target': tp, 'entry_time': df.index[i]}

    return trades

def analyze_performance(all_trades):
    if not all_trades:
        print("\nNo trades found. Try adjusting conditions or check data availability.")
        return

    df_trades = pd.DataFrame(all_trades)

    total_trades = len(df_trades)
    num_wins = len(df_trades[df_trades['PnL %'] > 0])
    num_losses = len(df_trades[df_trades['PnL %'] <= 0])
    win_rate = (num_wins / total_trades) * 100

    total_pnl = df_trades['PnL %'].sum()

    metrics = [
        ["Total Trades", total_trades],
        ["Wins", num_wins],
        ["Losses", num_losses],
        ["Win Rate (%)", f"{win_rate:.2f}%"],
        ["Total PnL (%)", f"{total_pnl:.2f}%"]
    ]

    print("\n--- Strategy Performance Report ---")
    print(tabulate(metrics, tablefmt="grid"))

    df_trades.to_csv("backtest_report.csv", index=False)
    print("\nSample Trades:")
    print(tabulate(df_trades.tail(10), headers='keys', tablefmt='psql', showindex=False))

if __name__ == "__main__":
    # Settings
    REVERSE_LOGIC = True
    USE_TRAILING_EMA = True
    USE_VOLUME_FILTER = False # Set to True to enable volume filter

    symbols = ["^NSEI", "^NSEBANK", "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "SBIN.NS", "ICICIBANK.NS"]
    all_results = []

    print(f"Starting Backtest (Reverse={REVERSE_LOGIC}, Trailing={USE_TRAILING_EMA})...")
    for symbol in symbols:
        df = fetch_data(symbol)
        if df is not None:
            df = calculate_indicators(df)
            if df is not None:
                df = generate_signals(df, reverse=REVERSE_LOGIC, use_volume_filter=USE_VOLUME_FILTER)
                trades = backtest(df, symbol, use_trailing=USE_TRAILING_EMA)
                all_results.extend(trades)

    analyze_performance(all_results)
