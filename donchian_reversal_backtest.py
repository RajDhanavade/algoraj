import pandas as pd
import numpy as np
import yfinance as yf
import os

# --- Configuration ---
TICKER = 'RELIANCE.NS'
TIMEFRAME = '5m'
DONCHIAN_PERIOD = 20
INITIAL_CAPITAL = 100000
TRADE_LOG_FILE = 'reversal_trades.csv'

# --- Advanced Features ---
LEVERAGE = 2.0
BROKERAGE_FEE_PCT = 0.05
SLIPPAGE_PCT = 0.02
SYSTEM_STOP_LOSS_PCT = 5.0 # 5% of entry capital for the position

def download_data(ticker, timeframe):
    """Downloads historical data for a given ticker and timeframe."""
    print(f"Downloading data for {ticker} with timeframe {timeframe}...")
    data = yf.download(tickers=ticker, period="60d", interval=timeframe)
    if data.empty:
        print(f"No data found for {ticker}. Exiting.")
        return None

    if isinstance(data.columns, pd.MultiIndex):
        data = data.droplevel(1, axis=1)
    data = data[~data.index.duplicated(keep='first')]
    data = data.reset_index()
    timestamp_col = 'Datetime' if 'Datetime' in data.columns else 'index'
    data[timestamp_col] = pd.to_datetime(data[timestamp_col])

    # Convert timestamps to Indian Standard Time (IST)
    if data[timestamp_col].dt.tz is None:
        data[timestamp_col] = data[timestamp_col].dt.tz_localize('UTC')
    data[timestamp_col] = data[timestamp_col].dt.tz_convert('Asia/Kolkata')

    print("Data downloaded successfully.")
    return data

def calculate_donchian_channel(data, period):
    """Calculates the Donchian Channel."""
    print(f"Calculating Donchian Channel with period {period}...")
    data['upper_band'] = data['High'].rolling(period).max()
    data['lower_band'] = data['Low'].rolling(period).min()
    return data

def run_backtest(data, initial_capital):
    """
    Runs the backtest for a simple stop-and-reverse Donchian Channel strategy.
    """
    print("Running stop-and-reverse backtest...")
    trade_log = []
    position = None  # Can be 'long', 'short', or None
    entry_price = 0
    cost_basis = 0
    shares = 0
    entry_time = None

    timestamp_col = 'Datetime' if 'Datetime' in data.columns else 'index'

    for index, row in data.iterrows():
        if pd.isna(row['lower_band']):
            continue

        # --- Stop-Loss Check ---
        if position is not None:
            pnl_pct = 0
            if position == 'long':
                pnl_pct = ((row['Close'] - entry_price) / entry_price) * 100
            elif position == 'short':
                pnl_pct = ((entry_price - row['Close']) / entry_price) * 100

            if pnl_pct <= -SYSTEM_STOP_LOSS_PCT:
                exit_price = row['Close'] * (1 - SLIPPAGE_PCT / 100) if position == 'long' else row['Close'] * (1 + SLIPPAGE_PCT / 100)
                brokerage = exit_price * shares * (BROKERAGE_FEE_PCT / 100)
                net_pnl = ((exit_price * shares) - cost_basis - brokerage) if position == 'long' else (cost_basis - (exit_price * shares) - brokerage)

                trade_log.append({
                    'entry_time': entry_time, 'entry_price': entry_price, 'position_type': position,
                    'exit_time': row[timestamp_col], 'exit_price': exit_price, 'pnl': net_pnl, 'shares': shares,
                    'exit_reason': 'Stop-Loss'
                })
                print(f"{row[timestamp_col]} - STOP-LOSS triggered on {position} position. PnL: {net_pnl:.2f}")
                position, entry_price, cost_basis, shares, entry_time = None, 0, 0, 0, None
                continue

        # --- Reversal and Entry Logic ---
        # Close Short, Open Long
        if row['Low'] <= row['lower_band']:
            if position == 'short':
                exit_price = row['lower_band'] * (1 + SLIPPAGE_PCT / 100)
                brokerage = exit_price * shares * (BROKERAGE_FEE_PCT / 100)
                net_pnl = (cost_basis - (exit_price * shares) - brokerage)
                trade_log.append({
                    'entry_time': entry_time, 'entry_price': entry_price, 'position_type': 'short',
                    'exit_time': row[timestamp_col], 'exit_price': exit_price, 'pnl': net_pnl, 'shares': shares,
                    'exit_reason': 'Reverse to Long'
                })
                print(f"{row[timestamp_col]} - Closing SHORT, opening LONG. PnL: {net_pnl:.2f}")

            if position != 'long':
                position = 'long'
                entry_price = row['lower_band'] * (1 + SLIPPAGE_PCT / 100)
                shares = int((initial_capital * LEVERAGE) / entry_price)
                brokerage = entry_price * shares * (BROKERAGE_FEE_PCT / 100)
                cost_basis = (entry_price * shares) + brokerage
                entry_time = row[timestamp_col]

        # Close Long, Open Short
        elif row['High'] >= row['upper_band']:
            if position == 'long':
                exit_price = row['upper_band'] * (1 - SLIPPAGE_PCT / 100)
                brokerage = exit_price * shares * (BROKERAGE_FEE_PCT / 100)
                net_pnl = (exit_price * shares) - cost_basis - brokerage
                trade_log.append({
                    'entry_time': entry_time, 'entry_price': entry_price, 'position_type': 'long',
                    'exit_time': row[timestamp_col], 'exit_price': exit_price, 'pnl': net_pnl, 'shares': shares,
                    'exit_reason': 'Reverse to Short'
                })
                print(f"{row[timestamp_col]} - Closing LONG, opening SHORT. PnL: {net_pnl:.2f}")

            if position != 'short':
                position = 'short'
                entry_price = row['upper_band'] * (1 - SLIPPAGE_PCT / 100)
                shares = int((initial_capital * LEVERAGE) / entry_price)
                brokerage = entry_price * shares * (BROKERAGE_FEE_PCT / 100)
                cost_basis = (entry_price * shares) + brokerage # For shorts, cost_basis is what we get
                entry_time = row[timestamp_col]

    print("Backtest complete.")
    return pd.DataFrame(trade_log)

def display_and_save_results(trade_log, initial_capital, data_start_date, filename):
    """Displays results and saves the trade log to a CSV file."""
    if trade_log.empty:
        print("\nNo trades were executed.")
        return

    pd.set_option('display.max_rows', 100)
    print("\n--- Trade Log ---")
    print(trade_log)

    # Save to CSV
    if filename:
        trade_log.to_csv(filename, index=False)
        print(f"\nTrade log saved to {os.path.abspath(filename)}")

    print("\n--- Performance Summary ---")
    total_pnl = trade_log['pnl'].sum()
    total_return_pct = (total_pnl / initial_capital) * 100

    print(f"Total Trades Logged: {len(trade_log)}")
    print(f"Initial Capital: {initial_capital:.2f}")
    print(f"Total Net PnL: {total_pnl:.2f}")
    print(f"Total Return: {total_return_pct:.2f}%")
    if len(trade_log) > 0:
        print(f"Win Rate: {(trade_log['pnl'] > 0).sum() / len(trade_log) * 100:.2f}%")

    # Advanced Metrics
    trade_log_sorted = trade_log.sort_values(by='exit_time').reset_index(drop=True)
    equity_curve = [initial_capital] + list(initial_capital + trade_log_sorted['pnl'].cumsum())
    equity_series = pd.Series(data=equity_curve, index=pd.to_datetime([data_start_date] + list(trade_log_sorted['exit_time'])))

    running_max = equity_series.cummax()
    drawdown = (equity_series - running_max) / running_max
    max_drawdown = abs(drawdown.min() * 100)

    daily_equity = equity_series.resample('D').last().ffill()
    daily_returns = daily_equity.pct_change().dropna()
    negative_returns = daily_returns[daily_returns < 0]
    downside_std = negative_returns.std()

    if pd.isna(downside_std) or downside_std == 0:
        sortino_ratio = np.inf
    else:
        annualized_return = daily_returns.mean() * 252
        annualized_downside_std = downside_std * np.sqrt(252)
        sortino_ratio = annualized_return / annualized_downside_std if annualized_downside_std != 0 else np.inf

    print("\n--- Advanced Metrics ---")
    print(f"Maximum Drawdown: {max_drawdown:.2f}%")
    print(f"Sortino Ratio: {sortino_ratio:.2f}")

    if 'exit_reason' in trade_log.columns:
        print("\n--- Exit Reasons ---")
        print(trade_log['exit_reason'].value_counts())

if __name__ == "__main__":
    stock_data = download_data(TICKER, TIMEFRAME)
    if stock_data is not None:
        stock_data_with_indicator = calculate_donchian_channel(stock_data, DONCHIAN_PERIOD)
        trade_log = run_backtest(stock_data_with_indicator, INITIAL_CAPITAL)

        timestamp_col = 'Datetime' if 'Datetime' in stock_data.columns else 'index'
        display_and_save_results(trade_log, INITIAL_CAPITAL, stock_data[timestamp_col].iloc[0], TRADE_LOG_FILE)