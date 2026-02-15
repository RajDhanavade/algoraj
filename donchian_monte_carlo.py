import pandas as pd
import numpy as np
import yfinance as yf
import os
from datetime import datetime
from tabulate import tabulate
import random

# --- Configuration ---
TICKERS = ["RELIANCE.NS", "HDFCBANK.NS", "WIPRO.NS", "TCS.NS", "SBIN.NS", "BPCL.NS", "COALINDIA.NS"]
TIMEFRAME = '15m'
DONCHIAN_PERIOD = 20
INITIAL_CAPITAL_PER_STOCK = 100000
CAPITAL_ALLOCATION = [0.4, 0.3, 0.3]
SYSTEM_STOP_LOSS_PCT = 5.0
ENTRY_DROP_RISE_PCT = 1.0
LEVERAGE = 4.0
BROKERAGE_FEE_PCT = 0.05
SLIPPAGE_PCT = 0.02
MC_ITERATIONS = 5000

def download_data(tickers, timeframe):
    """Downloads historical data for multiple tickers."""
    print(f"Downloading historical data for {len(tickers)} tickers...")
    data = yf.download(tickers=tickers, period="60d", interval=timeframe, group_by='ticker', progress=False)
    return data

def calculate_donchian_channel(data, period):
    """Calculates the Donchian Channel."""
    data['upper_band'] = data['High'].rolling(period).max()
    data['lower_band'] = data['Low'].rolling(period).min()
    return data

def run_backtest_for_ticker(ticker_data, ticker_name):
    """Runs the backtest for a single ticker and returns the trade log."""
    trade_log = []
    positions = []
    position_type = None

    data = ticker_data.copy()
    data.dropna(subset=['Open', 'High', 'Low', 'Close'], inplace=True)
    if len(data) < DONCHIAN_PERIOD:
        return []

    data = calculate_donchian_channel(data, DONCHIAN_PERIOD)

    for timestamp, row in data.iterrows():
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
                for p in list(positions):
                    exit_price = current_price * (1 - SLIPPAGE_PCT / 100) if position_type == 'long' else current_price * (1 + SLIPPAGE_PCT / 100)
                    brokerage = exit_price * p['shares'] * (BROKERAGE_FEE_PCT / 100)
                    net_pnl = ((exit_price * p['shares']) - p['cost_basis'] - brokerage) if position_type == 'long' else (p['cost_basis'] - (exit_price * p['shares']) - brokerage)
                    trade_log.append({'pnl': net_pnl, 'ticker': ticker_name})
                positions.clear()
                position_type = None
                continue

        # --- Reversal Logic ---
        if position_type == 'short' and row['Low'] <= row['lower_band']:
            for p in list(positions):
                exit_price = row['lower_band'] * (1 + SLIPPAGE_PCT / 100)
                brokerage = exit_price * p['shares'] * (BROKERAGE_FEE_PCT / 100)
                net_pnl = (p['cost_basis'] - (exit_price * p['shares']) - brokerage)
                trade_log.append({'pnl': net_pnl, 'ticker': ticker_name})
            positions.clear()
            position_type = None

        elif position_type == 'long' and row['High'] >= row['upper_band']:
            for p in list(positions):
                exit_price = row['upper_band'] * (1 - SLIPPAGE_PCT / 100)
                brokerage = exit_price * p['shares'] * (BROKERAGE_FEE_PCT / 100)
                net_pnl = ((exit_price * p['shares']) - p['cost_basis'] - brokerage)
                trade_log.append({'pnl': net_pnl, 'ticker': ticker_name})
            positions.clear()
            position_type = None

        # --- Entry Logic ---
        if len(positions) < 3:
            level = len(positions) + 1
            if position_type is None:
                if row['Low'] <= row['lower_band']:
                    position_type = 'long'
                elif row['High'] >= row['upper_band']:
                    position_type = 'short'
                else:
                    continue

            if position_type == 'long' and row['Low'] <= row['lower_band']:
                if level > 1:
                    last_entry_price = positions[-1]['entry_price']
                    if not (current_price <= last_entry_price * (1 - ENTRY_DROP_RISE_PCT / 100)):
                        continue
                capital_for_level = INITIAL_CAPITAL_PER_STOCK * CAPITAL_ALLOCATION[level - 1]
                leveraged_capital = capital_for_level * LEVERAGE
                entry_price = row['lower_band'] * (1 + SLIPPAGE_PCT / 100)
                shares = int(leveraged_capital / entry_price)
                if shares == 0: continue
                brokerage = entry_price * shares * (BROKERAGE_FEE_PCT / 100)
                cost_basis = (entry_price * shares) + brokerage
                positions.append({'entry_price': entry_price, 'shares': shares, 'cost_basis': cost_basis})

            elif position_type == 'short' and row['High'] >= row['upper_band']:
                if level > 1:
                    last_entry_price = positions[-1]['entry_price']
                    if not (current_price >= last_entry_price * (1 + ENTRY_DROP_RISE_PCT / 100)):
                        continue
                capital_for_level = INITIAL_CAPITAL_PER_STOCK * CAPITAL_ALLOCATION[level - 1]
                leveraged_capital = capital_for_level * LEVERAGE
                entry_price = row['upper_band'] * (1 - SLIPPAGE_PCT / 100)
                shares = int(leveraged_capital / entry_price)
                if shares == 0: continue
                brokerage = entry_price * shares * (BROKERAGE_FEE_PCT / 100)
                cost_basis = (entry_price * shares) - brokerage
                positions.append({'entry_price': entry_price, 'shares': shares, 'cost_basis': cost_basis})

    return trade_log

def perform_monte_carlo(all_trades, initial_capital, iterations):
    """Performs Monte Carlo simulation by shuffling trades."""
    if not all_trades:
        return None

    pnls = [t['pnl'] for t in all_trades]
    results = []

    print(f"Running {iterations} Monte Carlo iterations...")
    for _ in range(iterations):
        sim_pnls = random.choices(pnls, k=len(pnls))
        equity_curve = np.cumsum([initial_capital] + sim_pnls)

        final_pnl = equity_curve[-1] - initial_capital

        # Max Drawdown calculation
        running_max = np.maximum.accumulate(equity_curve)
        drawdowns = (running_max - equity_curve) / running_max
        max_dd = np.max(drawdowns) * 100

        results.append({
            'final_pnl': final_pnl,
            'return_pct': (final_pnl / initial_capital) * 100,
            'max_dd': max_dd
        })

    return pd.DataFrame(results)

def main():
    data_all = download_data(TICKERS, TIMEFRAME)
    all_trades = []

    for ticker in TICKERS:
        try:
            if isinstance(data_all.columns, pd.MultiIndex):
                ticker_data = data_all[ticker]
            else:
                ticker_data = data_all

            trades = run_backtest_for_ticker(ticker_data, ticker)
            all_trades.extend(trades)
        except Exception as e:
            print(f"Error processing {ticker}: {e}")

    print(f"Total trades collected from all tickers: {len(all_trades)}")

    total_initial_capital = INITIAL_CAPITAL_PER_STOCK * len(TICKERS)
    mc_results = perform_monte_carlo(all_trades, total_initial_capital, MC_ITERATIONS)

    if mc_results is not None:
        print("\n--- Monte Carlo Simulation Results (Stability Analysis) ---")
        stats = [
            ["Metric", "Value"],
            ["Mean Total PnL", f"{mc_results['final_pnl'].mean():.2f}"],
            ["Median Total PnL", f"{mc_results['final_pnl'].median():.2f}"],
            ["Mean Return %", f"{mc_results['return_pct'].mean():.2f}%"],
            ["Std Dev of Return", f"{mc_results['return_pct'].std():.2f}%"],
            ["95th Percentile Return", f"{mc_results['return_pct'].quantile(0.95):.2f}%"],
            ["5th Percentile Return (VaR)", f"{mc_results['return_pct'].quantile(0.05):.2f}%"],
            ["Mean Max Drawdown", f"{mc_results['max_dd'].mean():.2f}%"],
            ["95th Percentile Max DD", f"{mc_results['max_dd'].quantile(0.95):.2f}%"],
            ["Probability of Profit", f"{(mc_results['final_pnl'] > 0).sum() / MC_ITERATIONS * 100:.2f}%"]
        ]
        print(tabulate(stats, headers="firstrow", tablefmt="grid"))

        print("\nInterpretation:")
        print("1. Probability of Profit: Higher is better, indicates strategy consistency.")
        print("2. 5th Percentile Return: Represents the 'worst-case' scenario at 95% confidence.")
        print("3. 95th Percentile Max DD: Shows the potential risk under poor trade sequencing.")
    else:
        print("No trades were generated during the backtest to run Monte Carlo.")

if __name__ == "__main__":
    main()
