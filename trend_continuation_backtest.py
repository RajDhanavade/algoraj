import pandas as pd
import pandas_ta as ta
import yfinance as yf
import numpy as np
from tabulate import tabulate

def fetch_data(symbol, interval="30m", period="max"):
    """
    Fetches historical data from yfinance.
    """
    print(f"Fetching data for {symbol}...")
    try:
        df = yf.download(symbol, interval=interval, period=period)
        if df.empty:
            print(f"No data found for {symbol}")
            return None
        # Handle MultiIndex columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception as e:
        print(f"Error fetching data for {symbol}: {e}")
        return None

def calculate_indicators(df):
    """
    Calculates required indicators: EMA 50, RSI 14, MACD, ATR 14, Volume SMA 20.
    """
    # Ensure we have enough data
    if len(df) < 50:
        return None

    df = df.copy()
    df['EMA_50'] = ta.ema(df['Close'], length=50)
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

def generate_signals(df):
    """
    Generates Buy/Sell signals based on strategy conditions.
    """
    df = df.copy()
    df['Prev_MACD'] = df['MACD'].shift(1)
    df['Prev_MACD_Signal'] = df['MACD_Signal'].shift(1)

    # Buy Conditions
    # Price above 50 EMA
    # RSI between 50–65
    # MACD bullish crossover above zero line
    # Volume above average
    df['Buy_Signal'] = (
        (df['Close'] > df['EMA_50']) &
        (df['RSI'] >= 50) & (df['RSI'] <= 65) &
        (df['MACD'] > df['MACD_Signal']) & (df['Prev_MACD'] <= df['Prev_MACD_Signal']) &
        (df['MACD'] > 0) &
        (df['Volume'] > df['Vol_SMA'])
    )

    # Sell Conditions
    # Price below 50 EMA
    # RSI between 35–50
    # MACD bearish crossover below zero
    # Volume confirmation
    df['Sell_Signal'] = (
        (df['Close'] < df['EMA_50']) &
        (df['RSI'] >= 35) & (df['RSI'] <= 50) &
        (df['MACD'] < df['MACD_Signal']) & (df['Prev_MACD'] >= df['Prev_MACD_Signal']) &
        (df['MACD'] < 0) &
        (df['Volume'] > df['Vol_SMA'])
    )

    return df

def backtest(df, symbol):
    """
    Simulates the backtest with SL (1 ATR) and Target (1:2 RR).
    """
    trades = []
    in_position = False
    pos = None # {type, entry_price, stop_loss, target, entry_time}

    for i in range(2, len(df)):
        if in_position:
            current_high = df['High'].iloc[i]
            current_low = df['Low'].iloc[i]
            current_close = df['Close'].iloc[i]
            current_time = df.index[i]

            if pos['type'] == 'Long':
                if current_low <= pos['stop_loss']:
                    trades.append({
                        'Symbol': symbol,
                        'Type': 'Long',
                        'Entry Time': pos['entry_time'],
                        'Entry Price': pos['entry_price'],
                        'Exit Time': current_time,
                        'Exit Price': pos['stop_loss'],
                        'PnL %': ((pos['stop_loss'] - pos['entry_price']) / pos['entry_price']) * 100,
                        'Result': 'SL'
                    })
                    in_position = False
                elif current_high >= pos['target']:
                    trades.append({
                        'Symbol': symbol,
                        'Type': 'Long',
                        'Entry Time': pos['entry_time'],
                        'Entry Price': pos['entry_price'],
                        'Exit Time': current_time,
                        'Exit Price': pos['target'],
                        'PnL %': ((pos['target'] - pos['entry_price']) / pos['entry_price']) * 100,
                        'Result': 'TP'
                    })
                    in_position = False
            elif pos['type'] == 'Short':
                if current_high >= pos['stop_loss']:
                    trades.append({
                        'Symbol': symbol,
                        'Type': 'Short',
                        'Entry Time': pos['entry_time'],
                        'Entry Price': pos['entry_price'],
                        'Exit Time': current_time,
                        'Exit Price': pos['stop_loss'],
                        'PnL %': ((pos['entry_price'] - pos['stop_loss']) / pos['entry_price']) * 100,
                        'Result': 'SL'
                    })
                    in_position = False
                elif current_low <= pos['target']:
                    trades.append({
                        'Symbol': symbol,
                        'Type': 'Short',
                        'Entry Time': pos['entry_time'],
                        'Entry Price': pos['entry_price'],
                        'Exit Time': current_time,
                        'Exit Price': pos['target'],
                        'PnL %': ((pos['entry_price'] - pos['target']) / pos['entry_price']) * 100,
                        'Result': 'TP'
                    })
                    in_position = False

        if not in_position:
            # Check for signals on the previous candle
            if df['Buy_Signal'].iloc[i-1]:
                in_position = True
                entry_price = df['Close'].iloc[i]
                atr = df['ATR'].iloc[i-1] # Use ATR of signal candle
                sl = entry_price - atr
                tp = entry_price + (2 * atr)
                pos = {
                    'type': 'Long',
                    'entry_price': entry_price,
                    'stop_loss': sl,
                    'target': tp,
                    'entry_time': df.index[i]
                }
            elif df['Sell_Signal'].iloc[i-1]:
                in_position = True
                entry_price = df['Close'].iloc[i]
                atr = df['ATR'].iloc[i-1]
                sl = entry_price + atr
                tp = entry_price - (2 * atr)
                pos = {
                    'type': 'Short',
                    'entry_price': entry_price,
                    'stop_loss': sl,
                    'target': tp,
                    'entry_time': df.index[i]
                }

    return trades

def analyze_performance(all_trades):
    if not all_trades:
        print("No trades found.")
        return

    df_trades = pd.DataFrame(all_trades)
    df_trades['Entry Time'] = pd.to_datetime(df_trades['Entry Time'])
    df_trades['Exit Time'] = pd.to_datetime(df_trades['Exit Time'])

    total_trades = len(df_trades)
    win_trades = df_trades[df_trades['Result'] == 'TP']
    loss_trades = df_trades[df_trades['Result'] == 'SL']

    num_wins = len(win_trades)
    num_losses = len(loss_trades)
    win_rate = (num_wins / total_trades) * 100 if total_trades > 0 else 0

    total_pnl = df_trades['PnL %'].sum()
    avg_pnl = df_trades['PnL %'].mean()

    gross_profit = win_trades['PnL %'].sum()
    gross_loss = abs(loss_trades['PnL %'].sum())
    profit_factor = gross_profit / gross_loss if gross_loss != 0 else float('inf')

    # Calculate Max Drawdown from PnL % sequence
    cumulative_pnl = df_trades['PnL %'].cumsum()
    peak = cumulative_pnl.expanding(min_periods=1).max()
    drawdown = cumulative_pnl - peak
    max_drawdown = drawdown.min()

    # Sharpe Ratio (simplified)
    sharpe_ratio = (avg_pnl / df_trades['PnL %'].std() * np.sqrt(252)) if df_trades['PnL %'].std() != 0 else 0

    metrics = [
        ["Total Trades", total_trades],
        ["Win Trades", num_wins],
        ["Loss Trades", num_losses],
        ["Win Rate (%)", f"{win_rate:.2f}%"],
        ["Gross Profit (%)", f"{gross_profit:.2f}%"],
        ["Gross Loss (%)", f"{gross_loss:.2f}%"],
        ["Profit Factor", f"{profit_factor:.2f}"],
        ["Total PnL (%)", f"{total_pnl:.2f}%"],
        ["Avg PnL per Trade (%)", f"{avg_pnl:.2f}%"],
        ["Max Drawdown (%)", f"{max_drawdown:.2f}%"],
        ["Sharpe Ratio (Approx)", f"{sharpe_ratio:.2f}"]
    ]

    print("\n--- Strategy Performance Report ---")
    print(tabulate(metrics, tablefmt="grid"))

    # Export to CSV
    csv_file = "trend_continuation_trades.csv"
    df_trades.to_csv(csv_file, index=False)
    print(f"\nTrade log saved to {csv_file}")

    print("\n--- Last 10 Trades ---")
    print(tabulate(df_trades.tail(10), headers='keys', tablefmt='psql', showindex=False))

if __name__ == "__main__":
    symbols = ["^NSEI", "^NSEBANK", "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS"]
    all_results = []

    print("Starting Trend Continuation Strategy Backtest...")
    for symbol in symbols:
        df = fetch_data(symbol)
        if df is not None:
            df = calculate_indicators(df)
            if df is not None:
                df = generate_signals(df)
                trades = backtest(df, symbol)
                all_results.extend(trades)

    analyze_performance(all_results)
