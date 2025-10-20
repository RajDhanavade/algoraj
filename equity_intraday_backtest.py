import pandas as pd
import numpy as np
from kiteconnect import KiteConnect
import os
import logging
from datetime import datetime, time as datetime_time
import time
import webbrowser
import threading
from flask import Flask, request

# --- Configuration ---
API_KEY = "YOUR_API_KEY"
API_SECRET = "YOUR_API_SECRET"

# -- Backtest Parameters --
FROM_DATE = "2024-01-01"
TO_DATE = "2024-03-31"
EQUITY_SYMBOL = "RELIANCE"  # Change to the stock you want to test
EXCHANGE = "NSE"
TIMEFRAME = "5minute"
DONCHIAN_PERIOD = 20
QUANTITY = 10         # Number of shares to trade
STOP_LOSS_PCT = 2.0   # Stop-loss percentage

# -- Script Settings --
TRADE_LOG_FILE = 'equity_intraday_trades.csv'
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Web Server for Authentication ---
app = Flask(__name__)
access_token_global = None
server_started = threading.Event()
kite = None
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

@app.route("/redirect")
def redirect_url_handler():
    global access_token_global
    request_token = request.args.get("request_token")
    if not request_token: return "<h1>Login failed.</h1>"
    try:
        data = kite.generate_session(request_token, api_secret=API_SECRET)
        access_token_global = data["access_token"]
        logging.info("Access token generated successfully!")
        return "<h1>Login Successful!</h1><p>You can close this tab.</p>"
    except Exception as e:
        return f"<h1>Error: {e}</h1>"

def run_server():
    server_started.set()
    app.run(port=5000)

def get_access_token_automated(api_key):
    global kite
    kite = KiteConnect(api_key=api_key)
    print("--- Zerodha Login ---")
    print("Redirect URL: http://127.0.0.1:5000/redirect")
    server_thread = threading.Thread(target=run_server)
    server_thread.daemon = True
    server_thread.start()
    server_started.wait(timeout=5)
    webbrowser.open(kite.login_url())
    logging.info("Please complete login in your browser...")
    while access_token_global is None: time.sleep(1)
    return access_token_global

# --- Equity Backtesting Logic ---

def get_instrument_token(kite, symbol, exchange):
    """Fetches the instrument token for a given stock symbol."""
    instruments = kite.instruments(exchange=exchange)
    for instrument in instruments:
        if instrument['tradingsymbol'] == symbol:
            return instrument['instrument_token']
    raise ValueError(f"Token for {symbol} on {exchange} not found.")

def download_historical_data(kite, instrument_token, from_date, to_date, timeframe):
    """Downloads historical data for a given instrument token."""
    logging.info(f"Downloading data for {instrument_token} from {from_date} to {to_date}...")
    try:
        records = kite.historical_data(instrument_token, from_date, to_date, timeframe)
        df = pd.DataFrame(records)
        if not df.empty:
            df['date'] = pd.to_datetime(df['date']).dt.tz_convert('Asia/Kolkata')
        return df
    except Exception as e:
        logging.error(f"Could not download data for {instrument_token}: {e}")
        return pd.DataFrame()

def calculate_donchian_channel(data, period):
    """Calculates the Donchian Channel for the given data."""
    data['upper_band'] = data['high'].rolling(period).max()
    data['lower_band'] = data['low'].rolling(period).min()
    return data

def calculate_ema(data, period):
    """Calculates the Exponential Moving Average."""
    data[f'ema_{period}'] = data['close'].ewm(span=period, adjust=False).mean()
    return data

def run_equity_backtest(data, quantity, stop_loss_pct):
    """Executes the intraday backtest logic with corrected time-based rules and state handling."""
    logging.info("--- Starting Intraday Backtest Engine ---")
    trade_log = []
    position = None
    position_details = {}

    entry_start_time = datetime_time(9, 20)
    entry_end_time = datetime_time(15, 20)
    exit_time = datetime_time(15, 25)

    for i in range(DONCHIAN_PERIOD, len(data)):
        row = data.iloc[i]
        current_time = row['date'].time()

        # --- Step 1: Handle Exits ---
        just_exited_reason = None
        if position is not None:
            exit_price = None

            # Determine exit reason
            if current_time >= exit_time:
                just_exited_reason = 'End-of-Day'
                exit_price = row['open']
            else:
                entry_price = position_details['entry_price']
                if position == 'LONG' and row['low'] <= entry_price * (1 - stop_loss_pct / 100):
                    just_exited_reason = 'Stop-Loss'
                    exit_price = entry_price * (1 - stop_loss_pct / 100)
                elif position == 'SHORT' and row['high'] >= entry_price * (1 + stop_loss_pct / 100):
                    just_exited_reason = 'Stop-Loss'
                    exit_price = entry_price * (1 + stop_loss_pct / 100)

            reversal_signal = None
            if row['low'] <= row['lower_band']: reversal_signal = 'LONG'
            elif row['high'] >= row['upper_band']: reversal_signal = 'SHORT'

            if reversal_signal and reversal_signal != position and not just_exited_reason:
                just_exited_reason = 'Reversal'
                exit_price = row['close']

            # Process the exit if a reason was found
            if just_exited_reason:
                entry_price = position_details['entry_price']
                pnl = (exit_price - entry_price) * quantity if position == 'LONG' else (entry_price - exit_price) * quantity

                trade_log.append({
                    'entry_time': position_details['entry_time'], 'exit_time': row['date'],
                    'entry_price': entry_price, 'exit_price': exit_price,
                    'type': position, 'quantity': quantity, 'pnl': pnl, 'exit_reason': just_exited_reason
                })
                logging.info(f"{just_exited_reason}: Closed {position} position at {exit_price:.2f}. PnL: {pnl:.2f}")
                position = None
                position_details = {}

        # --- Step 2: Handle Entries ---
        if position is None and entry_start_time <= current_time <= entry_end_time:
            entry_signal = None
            if row['low'] <= row['lower_band'] and row['close'] > row['ema_100']:
                entry_signal = 'LONG'
            elif row['high'] >= row['upper_band'] and row['close'] < row['ema_100']:
                entry_signal = 'SHORT'

            # Only enter if there was no exit on this candle OR the exit was a reversal
            if entry_signal and (just_exited_reason is None or just_exited_reason == 'Reversal'):
                position = entry_signal
                position_details = {'entry_price': row['close'], 'entry_time': row['date']}
                logging.info(f"Entered {position} position at {row['close']}")

    return pd.DataFrame(trade_log)

def calculate_performance_metrics(trade_log):
    """Calculates and prints key performance metrics."""
    logging.info("--- Calculating Performance Metrics ---")
    if trade_log.empty:
        logging.warning("Trade log is empty. No performance metrics to calculate.")
        return

    total_pnl = trade_log['pnl'].sum()
    total_trades = len(trade_log)
    winning_trades = trade_log[trade_log['pnl'] > 0]
    losing_trades = trade_log[trade_log['pnl'] <= 0]

    win_rate = (len(winning_trades) / total_trades) * 100 if total_trades > 0 else 0

    avg_win_pnl = winning_trades['pnl'].mean() if not winning_trades.empty else 0
    avg_loss_pnl = losing_trades['pnl'].mean() if not losing_trades.empty else 0
    max_win = winning_trades['pnl'].max() if not winning_trades.empty else 0
    max_loss = losing_trades['pnl'].min() if not losing_trades.empty else 0

    print("\n--- Strategy Performance Metrics ---")
    print(f"Total Trades: {total_trades}")
    print(f"Total PnL: {total_pnl:.2f}")
    print(f"Win Rate: {win_rate:.2f}%")
    print(f"Winning Trades: {len(winning_trades)}")
    print(f"Losing Trades: {len(losing_trades)}")
    print(f"Average Winning PnL: {avg_win_pnl:.2f}")
    print(f"Average Losing PnL: {avg_loss_pnl:.2f}")
    print(f"Maximum Profit on a Single Trade: {max_win:.2f}")
    print(f"Maximum Loss on a Single Trade: {max_loss:.2f}")
    print("------------------------------------\n")


if __name__ == "__main__":
    if "YOUR_API_KEY" in [API_KEY, API_SECRET]:
        logging.error("Please fill in your API_KEY and API_SECRET.")
    else:
        access_token = get_access_token_automated(API_KEY)
        if access_token:
            kite = KiteConnect(api_key=API_KEY, access_token=access_token)

            try:
                # 1. Get instrument token
                instrument_token = get_instrument_token(kite, EQUITY_SYMBOL, EXCHANGE)

                # 2. Download data
                from_date_dt = datetime.strptime(FROM_DATE, '%Y-%m-%d')
                to_date_dt = datetime.strptime(TO_DATE, '%Y-%m-%d')
                equity_data = download_historical_data(kite, instrument_token, from_date_dt, to_date_dt, TIMEFRAME)

                if equity_data.empty:
                    raise ValueError("Could not download historical data. Please check symbol and date range.")

                # 3. Calculate indicators
                data_with_indicators = calculate_donchian_channel(equity_data, DONCHIAN_PERIOD)
                data_with_indicators = calculate_ema(data_with_indicators, 100)

                # 4. Run the backtest
                trade_log = run_equity_backtest(data_with_indicators, QUANTITY, STOP_LOSS_PCT)

                # 5. Save trade log and calculate performance
                if not trade_log.empty:
                    trade_log.to_csv(TRADE_LOG_FILE, index=False)
                    logging.info(f"Trade log saved to {TRADE_LOG_FILE}")
                    print("\n--- Trade Log ---")
                    print(trade_log)
                else:
                    logging.info("No trades were executed.")

                calculate_performance_metrics(trade_log)

            except Exception as e:
                logging.error(f"An error occurred: {e}", exc_info=True)
        else:
            logging.error("Could not obtain access token.")