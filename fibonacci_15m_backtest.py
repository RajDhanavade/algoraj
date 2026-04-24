import pandas as pd
import numpy as np
import yfinance as yf
import datetime
import os
import pickle
import sys

# Try to import tabulate, but handle if it's missing
try:
    from tabulate import tabulate
except ImportError:
    tabulate = None

# List of Nifty 50 stocks (using .NS for Yahoo Finance)
NIFTY50_SYMBOLS = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL", "CIPLA",
    "COALINDIA", "DRREDDY", "EICHERMOT", "GRASIM", "HCLTECH",
    "HDFCBANK", "HDFCLIFE", "HEROMOTOCO", "HINDALCO", "HINDUNILVR",
    "ICICIBANK", "INDUSINDBK", "INFY", "ITC", "JIOFIN", "JSWSTEEL",
    "KOTAKBANK", "LT", "M&M", "MARUTI", "NESTLEIND", "NTPC", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SHRIRAMFIN", "SBIN", "SUNPHARMA",
    "TCS", "TATACONSUM", "TATAMOTORS", "TATASTEEL", "TECHM", "TITAN",
    "TRENT", "ULTRACEMCO", "WIPRO"
]

DATA_FILE = 'fibonacci_data.pkl'

def download_data():
    """Download 15-minute data for the last month for Nifty 50 stocks."""
    tickers = [s + ".NS" for s in NIFTY50_SYMBOLS]
    # TATAMTRDVR.NS is often used as a proxy for TATAMOTORS.NS in intraday contexts
    # but here we use the user's implicit intent for top liquid Nifty 50 stocks.
    print(f"Downloading 15m data for {len(tickers)} stocks for the last 1 month...")
    try:
        data = yf.download(tickers, period="1mo", interval="15m", group_by='ticker', progress=False)
        if data.empty:
            print("Error: Downloaded data is empty.")
            return None
        with open(DATA_FILE, 'wb') as f:
            pickle.dump(data, f)
        return data
    except Exception as e:
        print(f"Error downloading data: {e}")
        return None

def load_data():
    """Load data from local file or download if not exists."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'rb') as f:
                data = pickle.load(f)
            return data
        except Exception:
            return download_data()
    else:
        return download_data()

def run_backtest(data):
    """
    Run the Fibonacci 15m strategy backtest.
    Strategy:
    1. Mark the High and Low of the 1st 15-min candle (09:15 - 09:30 IST).
    2. Range = High - Low.
    3. Sell Order at 1.618 level: High + 0.618 * Range. Target = High (Level 1).
    4. Buy Order at 1.618 level: Low - 0.618 * Range. Target = Low (Level 1).
    5. Quantity = 100 shares.
    6. Exit any open positions by 15:15 IST.
    """
    trades = []
    tickers = [s + ".NS" for s in NIFTY50_SYMBOLS]

    # Identify tickers in columns
    available_tickers = data.columns.get_level_values(0).unique()

    for ticker in tickers:
        if ticker not in available_tickers:
            continue

        df = data[ticker].dropna()
        if df.empty:
            continue

        # Convert to IST timezone
        if df.index.tz is None:
            df.index = df.index.tz_localize('UTC').tz_convert('Asia/Kolkata')
        else:
            df.index = df.index.tz_convert('Asia/Kolkata')

        unique_dates = np.unique(df.index.date)

        for date in unique_dates:
            day_data = df[df.index.date == date]
            if day_data.empty:
                continue

            # First candle 09:15 to 09:30
            first_candle = day_data[day_data.index.time == datetime.time(9, 15)]
            if first_candle.empty:
                continue

            h1 = first_candle['High'].iloc[0]
            l1 = first_candle['Low'].iloc[0]
            range1 = h1 - l1

            if range1 <= 0:
                continue

            # Fibonacci Levels
            upper_entry = h1 + 0.618 * range1
            upper_target = h1

            lower_entry = l1 - 0.618 * range1
            lower_target = l1

            # Monitoring after 1st candle
            monitoring_data = day_data[day_data.index.time > datetime.time(9, 15)]

            sell_pos = False
            sell_entry_price = 0
            sell_entry_time = None
            sell_order_done = False

            buy_pos = False
            buy_entry_price = 0
            buy_entry_time = None
            buy_order_done = False

            for timestamp, row in monitoring_data.iterrows():
                # EOD Exit
                if timestamp.time() >= datetime.time(15, 15):
                    if sell_pos:
                        exit_price = row['Close']
                        pnl = (sell_entry_price - exit_price) * 100
                        trades.append({
                            'Date': date, 'Ticker': ticker, 'Side': 'SELL',
                            'Entry Time': sell_entry_time, 'Entry Price': sell_entry_price,
                            'Exit Time': timestamp, 'Exit Price': exit_price, 'PnL': pnl, 'Status': 'EOD'
                        })
                        sell_pos = False
                    if buy_pos:
                        exit_price = row['Close']
                        pnl = (exit_price - buy_entry_price) * 100
                        trades.append({
                            'Date': date, 'Ticker': ticker, 'Side': 'BUY',
                            'Entry Time': buy_entry_time, 'Entry Price': buy_entry_price,
                            'Exit Time': timestamp, 'Exit Price': exit_price, 'PnL': pnl, 'Status': 'EOD'
                        })
                        buy_pos = False
                    break

                # Sell Logic (Mean Reversion from Upper Extension)
                if not sell_pos and not sell_order_done:
                    if row['High'] >= upper_entry:
                        sell_pos = True
                        sell_entry_price = upper_entry
                        sell_entry_time = timestamp
                        sell_order_done = True

                elif sell_pos:
                    if row['Low'] <= upper_target:
                        exit_price = upper_target
                        pnl = (sell_entry_price - exit_price) * 100
                        trades.append({
                            'Date': date, 'Ticker': ticker, 'Side': 'SELL',
                            'Entry Time': sell_entry_time, 'Entry Price': sell_entry_price,
                            'Exit Time': timestamp, 'Exit Price': exit_price, 'PnL': pnl, 'Status': 'TARGET'
                        })
                        sell_pos = False

                # Buy Logic (Mean Reversion from Lower Extension)
                if not buy_pos and not buy_order_done:
                    if row['Low'] <= lower_entry:
                        buy_pos = True
                        buy_entry_price = lower_entry
                        buy_entry_time = timestamp
                        buy_order_done = True

                elif buy_pos:
                    if row['High'] >= lower_target:
                        exit_price = lower_target
                        pnl = (exit_price - buy_entry_price) * 100
                        trades.append({
                            'Date': date, 'Ticker': ticker, 'Side': 'BUY',
                            'Entry Time': buy_entry_time, 'Entry Price': buy_entry_price,
                            'Exit Time': timestamp, 'Exit Price': exit_price, 'PnL': pnl, 'Status': 'TARGET'
                        })
                        buy_pos = False

    return pd.DataFrame(trades)

def report_performance(trades_df):
    if trades_df.empty:
        print("No trades executed during the backtest period.")
        return

    # Calculate Metrics
    total_pnl = trades_df['PnL'].sum()
    winning_trades = trades_df[trades_df['PnL'] > 0]
    losing_trades = trades_df[trades_df['PnL'] < 0]

    win_rate = (len(winning_trades) / len(trades_df)) * 100
    avg_pnl = trades_df['PnL'].mean()

    target_hits = len(trades_df[trades_df['Status'] == 'TARGET'])
    eod_exits = len(trades_df[trades_df['Status'] == 'EOD'])

    # Daily PnL for Max Drawdown
    daily_pnl = trades_df.groupby('Date')['PnL'].sum()
    cumulative_pnl = daily_pnl.cumsum()
    peak = cumulative_pnl.cummax()
    drawdown = cumulative_pnl - peak
    max_drawdown = drawdown.min()

    summary_data = [
        ["Total Trades", len(trades_df)],
        ["Winning Trades", len(winning_trades)],
        ["Losing Trades", len(losing_trades)],
        ["Win Rate", f"{win_rate:.2f}%"],
        ["Total PnL", f"{total_pnl:.2f}"],
        ["Max Daily Drawdown", f"{max_drawdown:.2f}"],
        ["Avg PnL per Trade", f"{avg_pnl:.2f}"],
        ["Target Hits", target_hits],
        ["EOD Exits", eod_exits]
    ]

    print("\n" + "="*60)
    print("      FIBONACCI 15M STRATEGY BACKTEST REPORT (NIFTY 50)")
    print("="*60)

    if tabulate:
        print(tabulate(summary_data, tablefmt="grid"))
    else:
        for row in summary_data:
            print(f"{row[0]:<20}: {row[1]}")

    # Stock-wise Performance
    stock_pnl = trades_df.groupby('Ticker')['PnL'].sum().sort_values(ascending=False)
    print("\nTop 5 Performing Stocks:")
    print(stock_pnl.head(5))

    print("\nBottom 5 Performing Stocks:")
    print(stock_pnl.tail(5))

    # Save results
    trades_df.to_csv("fibonacci_backtest_results.csv", index=False)
    print(f"\nDetailed trade log saved to 'fibonacci_backtest_results.csv'")

if __name__ == "__main__":
    data = load_data()
    if data is not None:
        trades_df = run_backtest(data)
        report_performance(trades_df)
    else:
        print("Failed to load or download data. Please check your internet connection or Yahoo Finance status.")
