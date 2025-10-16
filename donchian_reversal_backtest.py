import pandas as pd
import numpy as np
from kiteconnect import KiteConnect
import os
import logging

# --- Configuration ---
# --- Credentials (FILL THESE IN) ---
API_KEY = "YOUR_API_KEY"
API_SECRET = "YOUR_API_SECRET"
ACCESS_TOKEN = "YOUR_ACCESS_TOKEN"

# --- Trading Parameters ---
TRADING_SYMBOL = "RELIANCE"
EXCHANGE = "NSE"
TIMEFRAME = "5minute"
INITIAL_CAPITAL = 100000
TRADE_LOG_FILE = 'reversal_trades.csv'

# --- Strategy Parameters ---
DONCHIAN_PERIOD = 20
SYSTEM_STOP_LOSS_PCT = 5.0

# --- Advanced Features ---
LEVERAGE = 2.0
BROKERAGE_FEE_PCT = 0.05
SLIPPAGE_PCT = 0.02

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_instrument_token(kite, symbol):
    """Fetches the instrument token for the trading symbol."""
    try:
        instruments = kite.instruments(exchange=EXCHANGE)
        for instrument in instruments:
            if instrument['tradingsymbol'] == symbol:
                logging.info(f"Instrument token for {symbol}: {instrument['instrument_token']}")
                return instrument['instrument_token']
        raise ValueError(f"Instrument token for {symbol} not found.")
    except Exception as e:
        logging.error(f"Error fetching instrument token: {e}")
        raise

def download_data_from_zerodha(kite, instrument_token, timeframe):
    """Downloads historical data from Zerodha Kite API."""
    print(f"Downloading data for instrument {instrument_token} with timeframe {timeframe}...")
    try:
        from_date = pd.Timestamp.now() - pd.Timedelta(days=59) # Max 60 days for 5-min data
        to_date = pd.Timestamp.now()
        records = kite.historical_data(instrument_token, from_date, to_date, timeframe)
        df = pd.DataFrame(records)
        df['date'] = pd.to_datetime(df['date'])

        # Convert timestamps to Indian Standard Time (IST)
        if df['date'].dt.tz is None:
            df['date'] = df['date'].dt.tz_localize('UTC')
        df['date'] = df['date'].dt.tz_convert('Asia/Kolkata')

        print("Data downloaded successfully from Zerodha.")
        return df
    except Exception as e:
        logging.error(f"Error downloading data from Zerodha: {e}")
        return pd.DataFrame()

def calculate_donchian_channel(data, period):
    """Calculates the Donchian Channel."""
    print(f"Calculating Donchian Channel with period {period}...")
    data['upper_band'] = data['high'].rolling(period).max()
    data['lower_band'] = data['low'].rolling(period).min()
    return data

def run_backtest(data, initial_capital):
    """
    Runs the backtest for a simple stop-and-reverse Donchian Channel strategy.
    """
    print("Running stop-and-reverse backtest...")
    trade_log = []
    position = None
    entry_price = 0
    cost_basis = 0
    shares = 0
    entry_time = None

    for index, row in data.iterrows():
        if pd.isna(row['lower_band']):
            continue

        if position is not None:
            pnl_pct = 0
            if position == 'long':
                pnl_pct = ((row['close'] - entry_price) / entry_price) * 100
            elif position == 'short':
                pnl_pct = ((entry_price - row['close']) / entry_price) * 100

            if pnl_pct <= -SYSTEM_STOP_LOSS_PCT:
                exit_price = row['close'] * (1 - SLIPPAGE_PCT / 100) if position == 'long' else row['close'] * (1 + SLIPPAGE_PCT / 100)
                brokerage = exit_price * shares * (BROKERAGE_FEE_PCT / 100)
                net_pnl = ((exit_price * shares) - cost_basis - brokerage) if position == 'long' else (cost_basis - (exit_price * shares) - brokerage)

                trade_log.append({
                    'entry_time': entry_time, 'entry_price': entry_price, 'position_type': position,
                    'exit_time': row['date'], 'exit_price': exit_price, 'pnl': net_pnl, 'shares': shares,
                    'exit_reason': 'Stop-Loss'
                })
                print(f"{row['date']} - STOP-LOSS triggered on {position} position. PnL: {net_pnl:.2f}")
                position, entry_price, cost_basis, shares, entry_time = None, 0, 0, 0, None
                continue

        if row['low'] <= row['lower_band']:
            if position == 'short':
                exit_price = row['lower_band'] * (1 + SLIPPAGE_PCT / 100)
                brokerage = exit_price * shares * (BROKERAGE_FEE_PCT / 100)
                net_pnl = (cost_basis - (exit_price * shares) - brokerage)
                trade_log.append({
                    'entry_time': entry_time, 'entry_price': entry_price, 'position_type': 'short',
                    'exit_time': row['date'], 'exit_price': exit_price, 'pnl': net_pnl, 'shares': shares,
                    'exit_reason': 'Reverse to Long'
                })
                print(f"{row['date']} - Closing SHORT, opening LONG. PnL: {net_pnl:.2f}")

            if position != 'long':
                position = 'long'
                entry_price = row['lower_band'] * (1 + SLIPPAGE_PCT / 100)
                shares = int((initial_capital * LEVERAGE) / entry_price)
                brokerage = entry_price * shares * (BROKERAGE_FEE_PCT / 100)
                cost_basis = (entry_price * shares) + brokerage
                entry_time = row['date']

        elif row['high'] >= row['upper_band']:
            if position == 'long':
                exit_price = row['upper_band'] * (1 - SLIPPAGE_PCT / 100)
                brokerage = exit_price * shares * (BROKERAGE_FEE_PCT / 100)
                net_pnl = (exit_price * shares) - cost_basis - brokerage
                trade_log.append({
                    'entry_time': entry_time, 'entry_price': entry_price, 'position_type': 'long',
                    'exit_time': row['date'], 'exit_price': exit_price, 'pnl': net_pnl, 'shares': shares,
                    'exit_reason': 'Reverse to Short'
                })
                print(f"{row['date']} - Closing LONG, opening SHORT. PnL: {net_pnl:.2f}")

            if position != 'short':
                position = 'short'
                entry_price = row['upper_band'] * (1 - SLIPPAGE_PCT / 100)
                shares = int((initial_capital * LEVERAGE) / entry_price)
                brokerage = entry_price * shares * (BROKERAGE_FEE_PCT / 100)
                cost_basis = (entry_price * shares) + brokerage
                entry_time = row['date']

    print("Backtest complete.")
    return pd.DataFrame(trade_log)

def display_and_save_results(trade_log, initial_capital, data_start_date, filename):
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
    if "YOUR_API_KEY" in [API_KEY, API_SECRET, ACCESS_TOKEN]:
        logging.error("Please fill in your API credentials to run the backtest with Zerodha data.")
    else:
        kite = KiteConnect(api_key=API_KEY)
        kite.set_access_token(ACCESS_TOKEN)

        instrument_token = get_instrument_token(kite, TRADING_SYMBOL)
        stock_data = download_data_from_zerodha(kite, instrument_token, TIMEFRAME)

        if not stock_data.empty:
            stock_data_with_indicator = calculate_donchian_channel(stock_data, DONCHIAN_PERIOD)
            trade_log = run_backtest(stock_data_with_indicator, INITIAL_CAPITAL)
            display_and_save_results(trade_log, INITIAL_CAPITAL, stock_data['date'].iloc[0], TRADE_LOG_FILE)