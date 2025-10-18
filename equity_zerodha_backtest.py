import pandas as pd
import numpy as np
from kiteconnect import KiteConnect
import os
import logging
from datetime import datetime
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
QUANTITY = 10  # Number of shares to trade

# -- Script Settings --
TRADE_LOG_FILE = 'equity_trades.csv'
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

def run_equity_backtest(data, quantity):
    """Executes the backtest logic for the equity mean-reversion strategy."""
    logging.info("--- Starting Backtest Engine ---")
    trade_log = []
    position = None  # Can be 'LONG', 'SHORT', or None
    position_details = {}

    for i in range(DONCHIAN_PERIOD, len(data)):
        row = data.iloc[i]

        # Determine signal based on Donchian Channel
        signal = None
        if row['low'] <= row['lower_band']:
            signal = 'LONG'  # Buy signal
        elif row['high'] >= row['upper_band']:
            signal = 'SHORT' # Sell signal

        # Process reversal exits first
        if position is not None and signal is not None and signal != position:
            entry_price = position_details['entry_price']
            pnl = 0

            if position == 'LONG':
                pnl = (row['close'] - entry_price) * quantity
            elif position == 'SHORT':
                pnl = (entry_price - row['close']) * quantity

            trade_log.append({
                'entry_time': position_details['entry_time'],
                'exit_time': row['date'],
                'entry_price': entry_price,
                'exit_price': row['close'],
                'type': position,
                'quantity': quantity,
                'pnl': pnl
            })
            logging.info(f"Reversal: Closed {position} position at {row['close']}. PnL: {pnl:.2f}")
            position = None
            position_details = {}

        # Process entries
        if position is None and signal is not None:
            position = signal
            position_details = {'entry_price': row['close'], 'entry_time': row['date']}
            logging.info(f"Entered {signal} position at {row['close']}")

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

    print("\n--- Strategy Performance Metrics ---")
    print(f"Total Trades: {total_trades}")
    print(f"Winning Trades: {len(winning_trades)}")
    print(f"Losing Trades: {len(losing_trades)}")
    print(f"Win Rate: {win_rate:.2f}%")
    print(f"Total PnL: {total_pnl:.2f}")
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

                # 4. Run the backtest
                trade_log = run_equity_backtest(data_with_indicators, QUANTITY)

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