# Final Backtesting Script for the IBS Strategy

import pandas as pd
import numpy as np
import pickle
import yfinance as yf
import os

# --- Configuration ---
DATA_FILE = 'nifty50_data.pkl'
NIFTY50_SYMBOLS = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL", "CIPLA",
    "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL", "GRASIM", "HCLTECH",
    "HDFCBANK", "HDFCLIFE", "HEROMOTOCO", "HINDALCO", "HINDUNILVR",
    "ICICIBANK", "INDUSINDBK", "INFY", "ITC", "JIOFIN", "JSWSTEEL",
    "KOTAKBANK", "LT", "M&M", "MARUTI", "NESTLEIND", "NTPC", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SHRIRAMFIN", "SBIN", "SUNPHARMA",
    "TCS", "TATACONSUM", "TATAMOTORS", "TATASTEEL", "TECHM", "TITAN",
    "TRENT", "ULTRACEMCO", "WIPRO"
]

# --- Data Handling ---

def download_nifty50_data(symbols, filepath):
    """
    Downloads historical data for Nifty 50 stocks for the last 5 years
    and saves it to a pickle file.
    """
    print("Downloading Nifty 50 historical data (last 5 years)...")
    tickers = [symbol + ".NS" for symbol in symbols]
    data = yf.download(tickers, period="5y", group_by='ticker')

    with open(filepath, 'wb') as f:
        pickle.dump(data, f)
    print(f"Data downloaded and saved to {filepath}")
    return data

def load_data(filepath, symbols):
    """
    Loads the pickled stock data. If the file doesn't exist, it triggers the download.
    """
    if os.path.exists(filepath):
        print(f"Loading data from {filepath}...")
        with open(filepath, 'rb') as f:
            data = pickle.load(f)
        return data
    else:
        print(f"{filepath} not found.")
        return download_nifty50_data(symbols, filepath)

# --- Strategy and Backtesting ---

def calculate_ibs(data):
    """
    Calculates the Internal Bar Strength (IBS) for each stock in the dataset.
    IBS = (Close - Low) / (High - Low)
    """
    print("Calculating Internal Bar Strength (IBS)...")
    close_prices = data.xs('Close', level='Price', axis=1)
    low_prices = data.xs('Low', level='Price', axis=1)
    high_prices = data.xs('High', level='Price', axis=1)

    ibs = (close_prices - low_prices) / (high_prices - low_prices)
    return ibs

def run_backtest(data, ibs_data):
    """
    Runs the backtest for the IBS strategy.
    - Longs the stock with the minimum IBS.
    - Shorts the stock with the maximum IBS.
    - Enters at the close of the current day and exits at the close of the next day.
    """
    print("Running backtest...")
    close_prices = data.xs('Close', level='Price', axis=1)
    daily_returns = []

    for i in range(len(ibs_data) - 1):
        current_day_ibs = ibs_data.iloc[i].dropna()

        if len(current_day_ibs) < 2:
            daily_returns.append(0)
            continue

        min_ibs_stock = current_day_ibs.idxmin()
        max_ibs_stock = current_day_ibs.idxmax()

        current_close_long = close_prices.at[ibs_data.index[i], min_ibs_stock]
        next_close_long = close_prices.at[ibs_data.index[i+1], min_ibs_stock]

        current_close_short = close_prices.at[ibs_data.index[i], max_ibs_stock]
        next_close_short = close_prices.at[ibs_data.index[i+1], max_ibs_stock]

        if pd.isna(current_close_long) or pd.isna(next_close_long) or \
           pd.isna(current_close_short) or pd.isna(next_close_short):
            daily_returns.append(0)
            continue

        long_return = (next_close_long - current_close_long) / current_close_long
        short_return = -(next_close_short - current_close_short) / current_close_short

        total_daily_return = (long_return + short_return) / 2
        daily_returns.append(total_daily_return)

    print("Backtest complete.")
    return pd.Series(daily_returns, index=ibs_data.index[:-1])

# --- Performance Analysis ---

def calculate_performance_metrics(returns):
    """
    Calculates and prints key performance metrics for the strategy.
    """
    if returns is None or len(returns) == 0:
        print("No returns to analyze.")
        return

    cumulative_returns = (1 + returns).cumprod()
    total_return = (cumulative_returns.iloc[-1] - 1) * 100
    n_days = len(returns)
    annualized_return = ((1 + returns.mean()) ** 252 - 1) * 100
    annualized_volatility = (returns.std() * np.sqrt(252)) * 100
    sharpe_ratio = (annualized_return / annualized_volatility) if annualized_volatility != 0 else 0
    win_rate = (returns > 0).sum() / n_days * 100

    running_max = np.maximum.accumulate(cumulative_returns)
    drawdown = (cumulative_returns - running_max) / running_max
    max_drawdown = np.min(drawdown) * 100

    print("\n--- Strategy Performance Metrics ---")
    print(f"Backtest Period: {returns.index[0].date()} to {returns.index[-1].date()}")
    print(f"Total Return: {total_return:.2f}%")
    print(f"Annualized Return: {annualized_return:.2f}%")
    print(f"Annualized Volatility: {annualized_volatility:.2f}%")
    print(f"Sharpe Ratio: {sharpe_ratio:.2f}")
    print(f"Win Rate: {win_rate:.2f}%")
    print(f"Maximum Drawdown: {max_drawdown:.2f}%")
    print("------------------------------------")

# --- Main Execution ---

if __name__ == "__main__":
    """
    This script runs a backtest of a trading strategy based on the
    Internal Bar Strength (IBS) indicator on Nifty 50 stocks.

    The strategy is as follows:
    - Each day, calculate the IBS for all Nifty 50 stocks.
    - Go long on the stock with the lowest IBS.
    - Go short on the stock with the highest IBS.
    - Positions are entered at the close of the day and exited at the close of the next day.

    The script will automatically download the required historical data if it's not
    found locally.
    """

    # 1. Load data (or download if it doesn't exist)
    nifty_data = load_data(DATA_FILE, NIFTY50_SYMBOLS)

    if nifty_data is not None and not nifty_data.empty:
        # 2. Calculate IBS
        ibs_data = calculate_ibs(nifty_data)

        # 3. Run the backtest
        strategy_returns = run_backtest(nifty_data, ibs_data)

        # 4. Calculate and display performance
        calculate_performance_metrics(strategy_returns)
    else:
        print("Could not load or download data. Exiting.")
