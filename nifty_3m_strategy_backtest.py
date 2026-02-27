import pandas as pd
import numpy as np
import yfinance as yf
import pandas_ta as ta
from tabulate import tabulate
from datetime import time, datetime, timedelta
import os

# --- Configuration ---
SYMBOLS = [
    "ADANIENT.NS", "ADANIPORTS.NS", "APOLLOHOSP.NS", "ASIANPAINT.NS", "AXISBANK.NS",
    "BAJAJ-AUTO.NS", "BAJFINANCE.NS", "BAJAJFINSV.NS", "BEL.NS", "BHARTIARTL.NS", "CIPLA.NS",
    "COALINDIA.NS", "DRREDDY.NS", "EICHERMOT.NS", "GRASIM.NS", "HCLTECH.NS",
    "HDFCBANK.NS", "HDFCLIFE.NS", "HEROMOTOCO.NS", "HINDALCO.NS", "HINDUNILVR.NS",
    "ICICIBANK.NS", "INDUSINDBK.NS", "INFY.NS", "ITC.NS", "JSWSTEEL.NS",
    "KOTAKBANK.NS", "LT.NS", "M&M.NS", "MARUTI.NS", "NESTLEIND.NS", "NTPC.NS", "ONGC.NS",
    "POWERGRID.NS", "RELIANCE.NS", "SBILIFE.NS", "SHRIRAMFIN.NS", "SBIN.NS", "SUNPHARMA.NS",
    "TCS.NS", "TATACONSUM.NS", "TATAMTRDVR.NS", "TATASTEEL.NS", "TECHM.NS", "TITAN.NS",
    "TRENT.NS", "ULTRACEMCO.NS", "WIPRO.NS", "^NSEI", "^NSEBANK"
]

# Strategy Parameters
EMA_LONG_PERIOD = 200
EMA_SHORT_PERIOD = 20
TIMEFRAME = '3min'
GAP_THRESHOLD = 0.005 # 0.5% for large gap
MARUBOZU_THRESHOLD = 0.8 # Body is 80% of total candle range
CASH = 100000 # Initial capital per stock for ROI calculation

def download_and_resample(symbol, period='7d'):
    print(f"Downloading data for {symbol}...")
    try:
        data = yf.download(symbol, period=period, interval='1m', progress=False)
        if data.empty:
            return None

        # Flatten columns if MultiIndex
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)

        # Ensure column names are standard strings
        data.columns = [str(col) for col in data.columns]

        # Resample to 3 minutes
        resampled = data.resample('3min').agg({
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last',
            'Volume': 'sum'
        }).dropna()

        # Convert timezone to IST
        resampled.index = resampled.index.tz_convert('Asia/Kolkata')
        return resampled
    except Exception as e:
        print(f"Error downloading {symbol}: {e}")
        return None

def calculate_indicators(df):
    df['EMA200'] = ta.ema(df['Close'], length=EMA_LONG_PERIOD)
    df['EMA20'] = ta.ema(df['Close'], length=EMA_SHORT_PERIOD)
    df['EMA20_Rising'] = df['EMA20'] > df['EMA20'].shift(1)
    df['EMA20_Falling'] = df['EMA20'] < df['EMA20'].shift(1)

    # Marubozu Detection
    candle_range = df['High'] - df['Low']
    body_range = (df['Close'] - df['Open']).abs()
    df['Is_Marubozu'] = (body_range / candle_range) > MARUBOZU_THRESHOLD
    df['Is_Bullish'] = df['Close'] > df['Open']
    df['Is_Bearish'] = df['Close'] < df['Open']

    return df

def backtest_symbol(symbol):
    df = download_and_resample(symbol)
    if df is None or len(df) < EMA_LONG_PERIOD:
        return []

    df = calculate_indicators(df)
    trades = []
    in_position = None
    stop_loss = 0
    entry_price = 0
    entry_time = None

    # Daily gap detection
    df['Date'] = df.index.date
    daily_groups = df.groupby('Date')

    for date, group in daily_groups:
        # Check opening gap
        prev_days = df[df.index.date < date]
        if prev_days.empty:
            continue
        prev_close = prev_days.iloc[-1]['Close']
        open_price = group.iloc[0]['Open']
        gap = (open_price - prev_close) / prev_close

        large_gap_up = gap > GAP_THRESHOLD
        large_gap_down = gap < -GAP_THRESHOLD

        for i in range(1, len(group)):
            current_time = group.index[i]
            row = group.iloc[i]
            prev_row = group.iloc[i-1]

            # EOD Exit at 3:25 PM
            if in_position and current_time.time() >= time(15, 25):
                exit_price = float(row['Close'])
                pnl = (exit_price - entry_price) if in_position == 'long' else (entry_price - exit_price)
                trades.append({
                    'Symbol': symbol,
                    'Type': in_position,
                    'Entry Time': entry_time,
                    'Entry Price': entry_price,
                    'Exit Time': current_time,
                    'Exit Price': exit_price,
                    'PnL': pnl,
                    'Return %': (pnl / entry_price) * 100,
                    'Exit Reason': 'EOD'
                })
                in_position = None
                continue

            if in_position == 'long':
                # Trailing SL with 20 EMA
                stop_loss = max(float(stop_loss), float(row['EMA20']))

                # Exit conditions: Close below EMA20 or Hit SL
                if row['Close'] < row['EMA20'] or row['Low'] <= stop_loss:
                    exit_price = min(float(row['Close']), float(stop_loss)) if row['Low'] <= stop_loss else float(row['Close'])
                    pnl = exit_price - entry_price
                    trades.append({
                        'Symbol': symbol,
                        'Type': 'long',
                        'Entry Time': entry_time,
                        'Entry Price': entry_price,
                        'Exit Time': current_time,
                        'Exit Price': exit_price,
                        'PnL': pnl,
                        'Return %': (pnl / entry_price) * 100,
                        'Exit Reason': 'EMA20/SL'
                    })
                    in_position = None

            elif in_position == 'short':
                # Trailing SL with 20 EMA
                stop_loss = min(float(stop_loss), float(row['EMA20']))

                # Exit conditions: Close above EMA20 or Hit SL
                if row['Close'] > row['EMA20'] or row['High'] >= stop_loss:
                    exit_price = max(float(row['Close']), float(stop_loss)) if row['High'] >= stop_loss else float(row['Close'])
                    pnl = entry_price - exit_price
                    trades.append({
                        'Symbol': symbol,
                        'Type': 'short',
                        'Entry Time': entry_time,
                        'Entry Price': entry_price,
                        'Exit Time': current_time,
                        'Exit Price': exit_price,
                        'PnL': pnl,
                        'Return %': (pnl / entry_price) * 100,
                        'Exit Reason': 'EMA20/SL'
                    })
                    in_position = None

            else:
                # Buy Signal
                if (prev_row['Close'] <= prev_row['EMA200'] and row['Close'] > row['EMA200']) and \
                   row['Is_Marubozu'] and row['Is_Bullish'] and \
                   row['EMA20_Rising'] and not large_gap_up:

                    in_position = 'long'
                    entry_price = float(row['Close'])
                    entry_time = current_time
                    stop_loss = min(float(row['Low']), float(prev_row['Low']))

                # Sell Signal
                elif (prev_row['Close'] >= prev_row['EMA200'] and row['Close'] < row['EMA200']) and \
                     row['Is_Marubozu'] and row['Is_Bearish'] and \
                     row['EMA20_Falling'] and not large_gap_down:

                    in_position = 'short'
                    entry_price = float(row['Close'])
                    entry_time = current_time
                    stop_loss = max(float(row['High']), float(prev_row['High']))

    return trades

def run_full_backtest():
    all_trades = []
    for symbol in SYMBOLS:
        symbol_trades = backtest_symbol(symbol)
        all_trades.extend(symbol_trades)

    if not all_trades:
        print("No trades found.")
        return

    df_trades = pd.DataFrame(all_trades)

    # Calculate Metrics
    total_trades = len(df_trades)
    winning_trades = df_trades[df_trades['PnL'] > 0]
    losing_trades = df_trades[df_trades['PnL'] <= 0]

    win_rate = (len(winning_trades) / total_trades) * 100 if total_trades > 0 else 0
    total_pnl_pct = df_trades['Return %'].sum()
    avg_return = df_trades['Return %'].mean()

    gross_profit = winning_trades['PnL'].sum()
    gross_loss = abs(losing_trades['PnL'].sum())
    profit_factor = gross_profit / gross_loss if gross_loss != 0 else float('inf')

    # ROI calculation assuming fixed capital per trade
    # If we allocate 100k per stock, and we sum up the Return % * 100k
    total_pnl_value = (df_trades['Return %'] / 100 * CASH).sum()
    total_capital = len(SYMBOLS) * CASH
    roi = (total_pnl_value / total_capital) * 100

    # Simple Max Drawdown calculation on cumulative returns
    # Note: This is a simplified version based on sequential trade returns
    df_trades = df_trades.sort_values('Entry Time')
    df_trades['CumReturn'] = (1 + df_trades['Return %'] / 100).cumprod()
    df_trades['RollingMax'] = df_trades['CumReturn'].cummax()
    df_trades['Drawdown'] = (df_trades['CumReturn'] - df_trades['RollingMax']) / df_trades['RollingMax']
    max_drawdown = df_trades['Drawdown'].min() * 100

    print("\n" + "="*50)
    print("BACKTEST REPORT: 3-MINUTE EMA STRATEGY")
    print("="*50)

    summary = [
        ["Total Trades", total_trades],
        ["Winning Trades", len(winning_trades)],
        ["Losing Trades", len(losing_trades)],
        ["Win Rate", f"{win_rate:.2f}%"],
        ["Gross Profit (Value)", f"{gross_profit:.2f}"],
        ["Gross Loss (Value)", f"{gross_loss:.2f}"],
        ["Profit Factor", f"{profit_factor:.2f}"],
        ["Total Return (Sum of %)", f"{total_pnl_pct:.2f}%"],
        ["Average Return per Trade", f"{avg_return:.2f}%"],
        ["ROI (on Total Capital)", f"{roi:.2f}%"],
        ["Max Drawdown (Trades)", f"{max_drawdown:.2f}%"],
    ]
    print(tabulate(summary, tablefmt="grid"))

    # Top 10 Trades
    print("\nTOP 10 TRADES:")
    print(tabulate(df_trades.nlargest(10, 'Return %')[['Symbol', 'Type', 'Entry Time', 'Return %']], headers='keys', tablefmt='psql'))

    # Performance by Symbol
    print("\nPERFORMANCE BY SYMBOL (Top 10):")
    symbol_perf = df_trades.groupby('Symbol')['Return %'].sum().sort_values(ascending=False).head(10)
    print(symbol_perf)

if __name__ == "__main__":
    run_full_backtest()
