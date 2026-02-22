import pandas as pd
import numpy as np
import yfinance as yf
from datetime import time

def download_data(symbol, period="60d", interval="5m"):
    print(f"Downloading {interval} data for {symbol}...")
    data = yf.download(symbol, period=period, interval=interval)
    return data

def run_backtest(df):
    # Ensure timezone is Asia/Kolkata
    if df.index.tz is None:
        df.index = df.index.tz_localize('UTC').tz_convert('Asia/Kolkata')
    else:
        df.index = df.index.tz_convert('Asia/Kolkata')

    # Calculate 50 EMA
    df['EMA50'] = df['Close'].ewm(span=50, adjust=False).mean()

    # Initialize variables
    active_buys = {} # level: (entry_price, entry_time)
    active_shorts = {} # level: (entry_price, entry_time)
    trades = []

    # Costs
    brokerage_pct = 0.0005 # 0.05%
    slippage_pct = 0.0002 # 0.02%

    for timestamp, row in df.iterrows():
        current_time = timestamp.time()

        # Intraday: Close all positions at 3:25 PM IST
        if current_time >= time(15, 25):
            for level, (entry_price, entry_time) in list(active_buys.items()):
                exit_price = row['Close']
                pnl = (exit_price - entry_price) - (exit_price + entry_price) * (brokerage_pct + slippage_pct)
                trades.append({
                    'entry_time': entry_time,
                    'exit_time': timestamp,
                    'type': 'BUY',
                    'entry_level': level,
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'pnl': pnl,
                    'reason': 'EOD'
                })
            active_buys.clear()

            for level, (entry_price, entry_time) in list(active_shorts.items()):
                exit_price = row['Close']
                pnl = (entry_price - exit_price) - (exit_price + entry_price) * (brokerage_pct + slippage_pct)
                trades.append({
                    'entry_time': entry_time,
                    'exit_time': timestamp,
                    'type': 'SHORT',
                    'entry_level': level,
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'pnl': pnl,
                    'reason': 'EOD'
                })
            active_shorts.clear()
            continue

        # Strategy starts at 9:15 AM IST
        if current_time < time(9, 15):
            continue

        price_above_ema = row['Close'] > row['EMA50']

        # Mode switch: close opposite positions
        if price_above_ema:
            # Close all shorts
            for level, (entry_price, entry_time) in list(active_shorts.items()):
                exit_price = row['Close']
                pnl = (entry_price - exit_price) - (exit_price + entry_price) * (brokerage_pct + slippage_pct)
                trades.append({
                    'entry_time': entry_time,
                    'exit_time': timestamp,
                    'type': 'SHORT',
                    'entry_level': level,
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'pnl': pnl,
                    'reason': 'EMA_SWITCH'
                })
            active_shorts.clear()
        else:
            # Close all buys
            for level, (entry_price, entry_time) in list(active_buys.items()):
                exit_price = row['Close']
                pnl = (exit_price - entry_price) - (exit_price + entry_price) * (brokerage_pct + slippage_pct)
                trades.append({
                    'entry_time': entry_time,
                    'exit_time': timestamp,
                    'type': 'BUY',
                    'entry_level': level,
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'pnl': pnl,
                    'reason': 'EMA_SWITCH'
                })
            active_buys.clear()

        # Grid Logic
        low = row['Low']
        high = row['High']

        # Determine levels to check
        # For buy: we check if we crossed level L upwards
        # But user said "buy each level", so if price is in range [L, L+1), we should have Buy(L)

        # To be precise, let's look at all integer levels in [low, high]
        for L in range(int(np.floor(low)), int(np.ceil(high)) + 1):
            if L < low or L > high:
                continue

            if price_above_ema:
                # If target L is hit for L-1
                if (L-1) in active_buys:
                    entry_price, entry_time = active_buys.pop(L-1)
                    exit_price = L
                    pnl = (exit_price - entry_price) - (exit_price + entry_price) * (brokerage_pct + slippage_pct)
                    trades.append({
                        'entry_time': entry_time,
                        'exit_time': timestamp,
                        'type': 'BUY',
                        'entry_level': L-1,
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'pnl': pnl,
                        'reason': 'TARGET'
                    })

                # Buy at level L
                if L not in active_buys:
                    active_buys[L] = (L, timestamp)
            else:
                # If target L is hit for L+1
                if (L+1) in active_shorts:
                    entry_price, entry_time = active_shorts.pop(L+1)
                    exit_price = L
                    pnl = (entry_price - exit_price) - (exit_price + entry_price) * (brokerage_pct + slippage_pct)
                    trades.append({
                        'entry_time': entry_time,
                        'exit_time': timestamp,
                        'type': 'SHORT',
                        'entry_level': L+1,
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'pnl': pnl,
                        'reason': 'TARGET'
                    })

                # Short at level L
                if L not in active_shorts:
                    active_shorts[L] = (L, timestamp)

    return pd.DataFrame(trades)

if __name__ == "__main__":
    symbol = "ITC.NS"
    data = download_data(symbol)
    if data.empty:
        print("No data found.")
    else:
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)

        trade_log = run_backtest(data)

        if not trade_log.empty:
            total_pnl = trade_log['pnl'].sum()
            win_rate = (trade_log['pnl'] > 0).mean() * 100

            print(f"\nBacktest Results for {symbol}:")
            print(f"Total Trades: {len(trade_log)}")
            print(f"Total PnL: {total_pnl:.2f}")
            print(f"Win Rate: {win_rate:.2f}%")

            print("\nLast 10 trades:")
            pd.set_option('display.max_columns', None)
            pd.set_option('display.width', 1000)
            print(trade_log.tail(10))

            trade_log.to_csv("itc_grid_trades.csv", index=False)
            print("\nTrade log saved to itc_grid_trades.csv")
        else:
            print("No trades executed.")
