import pandas as pd
import numpy as np
import yfinance as yf
import os

# --- Configuration ---
TICKER = 'RELIANCE.NS'
TIMEFRAME = '5m'
DONCHIAN_PERIOD = 20
INITIAL_CAPITAL = 100000
CAPITAL_ALLOCATION = [0.4, 0.3, 0.3]
SYSTEM_STOP_LOSS_PCT = 5.0
ENTRY_DROP_RISE_PCT = 1.0 # 1% drop/rise for next entry
TRADE_LOG_FILE = 'multilevel_trades.csv'

# --- Advanced Features ---
LEVERAGE = 2.0
BROKERAGE_FEE_PCT = 0.05
SLIPPAGE_PCT = 0.02

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
    Runs the backtest for the multi-level long/short Donchian Channel strategy.
    """
    print("Running multi-level long/short backtest...")
    trade_log = []
    positions = [] # To store active trades {entry_price, shares, level, type}
    position_type = None # 'long' or 'short'

    timestamp_col = 'Datetime' if 'Datetime' in data.columns else 'index'

    for index, row in data.iterrows():
        if pd.isna(row['lower_band']):
            continue

        current_price = row['Close']

        # --- System-wide Stop-Loss Check ---
        if positions:
            deployed_capital_at_cost = sum(p['cost_basis'] for p in positions)
            current_value = 0
            if position_type == 'long':
                current_value = sum(current_price * p['shares'] for p in positions)
            else: # short
                current_value = sum(p['cost_basis'] - (current_price - p['entry_price']) * p['shares'] for p in positions)

            pnl = current_value - deployed_capital_at_cost
            if deployed_capital_at_cost > 0 and (pnl / deployed_capital_at_cost) * 100 <= -SYSTEM_STOP_LOSS_PCT:
                print(f"{row[timestamp_col]} - SYSTEM STOP-LOSS triggered on {position_type} position. Closing all {len(positions)} levels.")
                for p in list(positions):
                    exit_price = current_price * (1 - SLIPPAGE_PCT / 100) if position_type == 'long' else current_price * (1 + SLIPPAGE_PCT / 100)
                    brokerage = exit_price * p['shares'] * (BROKERAGE_FEE_PCT / 100)
                    net_pnl = ((exit_price * p['shares']) - p['cost_basis'] - brokerage) if position_type == 'long' else (p['cost_basis'] - (exit_price * p['shares']) - brokerage)

                    trade_log.append({
                        'entry_time': p['entry_time'], 'entry_price': p['entry_price'], 'position_type': position_type,
                        'exit_time': row[timestamp_col], 'exit_price': exit_price, 'pnl': net_pnl, 'shares': p['shares'],
                        'exit_reason': f"System SL L{p['level']}"
                    })
                positions.clear()
                position_type = None
                continue

        # --- Reversal Logic ---
        # Close Short, Open Long
        if position_type == 'short' and row['Low'] <= row['lower_band']:
            print(f"{row[timestamp_col]} - REVERSAL signal from SHORT to LONG.")
            for p in list(positions): # Flatten position
                exit_price = row['lower_band'] * (1 + SLIPPAGE_PCT / 100)
                brokerage = exit_price * p['shares'] * (BROKERAGE_FEE_PCT / 100)
                net_pnl = (p['cost_basis'] - (exit_price * p['shares']) - brokerage)
                trade_log.append({
                    'entry_time': p['entry_time'], 'entry_price': p['entry_price'], 'position_type': 'short',
                    'exit_time': row[timestamp_col], 'exit_price': exit_price, 'pnl': net_pnl, 'shares': p['shares'],
                    'exit_reason': f"Reverse to Long L{p['level']}"
                })
            positions.clear()
            position_type = None

        # Close Long, Open Short
        elif position_type == 'long' and row['High'] >= row['upper_band']:
            print(f"{row[timestamp_col]} - REVERSAL signal from LONG to SHORT.")
            for p in list(positions): # Flatten position
                exit_price = row['upper_band'] * (1 - SLIPPAGE_PCT / 100)
                brokerage = exit_price * p['shares'] * (BROKERAGE_FEE_PCT / 100)
                net_pnl = ((exit_price * p['shares']) - p['cost_basis'] - brokerage)
                trade_log.append({
                    'entry_time': p['entry_time'], 'entry_price': p['entry_price'], 'position_type': 'long',
                    'exit_time': row[timestamp_col], 'exit_price': exit_price, 'pnl': net_pnl, 'shares': p['shares'],
                    'exit_reason': f"Reverse to Short L{p['level']}"
                })
            positions.clear()
            position_type = None

        # --- Entry Logic ---
        if len(positions) < 3:
            level = len(positions) + 1

            # Decide on entry type
            if position_type is None:
                if row['Low'] <= row['lower_band']:
                    position_type = 'long'
                elif row['High'] >= row['upper_band']:
                    position_type = 'short'
                else:
                    continue # No entry signal

            # LONG ENTRY
            if position_type == 'long' and row['Low'] <= row['lower_band']:
                if level > 1:
                    last_entry_price = positions[-1]['entry_price']
                    if not (current_price <= last_entry_price * (1 - ENTRY_DROP_RISE_PCT / 100)):
                        continue

                capital_for_level = initial_capital * CAPITAL_ALLOCATION[level - 1]
                leveraged_capital = capital_for_level * LEVERAGE
                entry_price = row['lower_band'] * (1 + SLIPPAGE_PCT / 100)
                shares = int(leveraged_capital / entry_price)
                if shares == 0: continue
                brokerage = entry_price * shares * (BROKERAGE_FEE_PCT / 100)
                cost_basis = (entry_price * shares) + brokerage
                positions.append({'entry_time': row[timestamp_col], 'entry_price': entry_price, 'shares': shares, 'level': level, 'cost_basis': cost_basis})
                print(f"{row[timestamp_col]} - BUY LEVEL {level} at {entry_price:.2f}, Shares: {shares}")

            # SHORT ENTRY
            elif position_type == 'short' and row['High'] >= row['upper_band']:
                if level > 1:
                    last_entry_price = positions[-1]['entry_price']
                    if not (current_price >= last_entry_price * (1 + ENTRY_DROP_RISE_PCT / 100)):
                        continue

                capital_for_level = initial_capital * CAPITAL_ALLOCATION[level - 1]
                leveraged_capital = capital_for_level * LEVERAGE
                entry_price = row['upper_band'] * (1 - SLIPPAGE_PCT / 100)
                shares = int(leveraged_capital / entry_price)
                if shares == 0: continue
                brokerage = entry_price * shares * (BROKERAGE_FEE_PCT / 100)
                cost_basis = (entry_price * shares) - brokerage # For shorts, cost_basis is what we get
                positions.append({'entry_time': row[timestamp_col], 'entry_price': entry_price, 'shares': shares, 'level': level, 'cost_basis': cost_basis})
                print(f"{row[timestamp_col]} - SHORT LEVEL {level} at {entry_price:.2f}, Shares: {shares}")

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