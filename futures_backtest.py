import pandas as pd
import numpy as np
from kiteconnect import KiteConnect
import os
import logging
from datetime import datetime, timedelta, date
import time
import webbrowser
import threading
from flask import Flask, request

# --- Configuration ---
API_KEY = "YOUR_API_KEY"
API_SECRET = "YOUR_API_SECRET"

FROM_DATE = "2023-01-01"
TO_DATE = "2023-03-31"

FUTURES_SYMBOL_PREFIX = "BANKNIFTY"
EXCHANGE = "NFO"

TIMEFRAME = "5minute"
DONCHIAN_PERIOD = 20

TRADE_LOG_FILE = 'futures_trades.csv'
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

# --- Futures Backtesting Logic ---

def get_futures_contracts(kite, symbol_prefix):
    """
    Fetches all futures contracts for a given symbol prefix.
    """
    instruments = kite.instruments(exchange=EXCHANGE)
    futures = [
        ins for ins in instruments
        if ins['name'] == symbol_prefix and ins['instrument_type'] == 'FUT'
    ]
    return pd.DataFrame(futures)

def download_historical_data(kite, instrument_token, from_date, to_date, timeframe):
    """
    Downloads historical data for a given instrument token.
    """
    logging.info(f"Downloading data for token: {instrument_token}...")
    try:
        records = kite.historical_data(instrument_token, from_date, to_date, timeframe)
        df = pd.DataFrame(records)
        if not df.empty:
            df['date'] = pd.to_datetime(df['date']).dt.tz_convert('Asia/Kolkata')
        return df
    except Exception as e:
        logging.error(f"Could not download data for {instrument_token}: {e}")
        return pd.DataFrame()

def get_monthly_futures_data(kite, futures_df, from_date, to_date):
    """
    Finds the correct monthly futures contract for each day, downloads data efficiently,
    and constructs a continuous price series with rollover handling. Includes validation
    to prevent backtesting on dates where historical contract data is unavailable.
    """
    logging.info("--- Starting Futures Data Pre-Fetch Process ---")
    futures_df['expiry'] = pd.to_datetime(futures_df['expiry']).dt.date
    futures_df = futures_df.sort_values(by='expiry').reset_index(drop=True)

    # --- API Limitation Sanity Check ---
    first_day_of_backtest = from_date.date()
    first_relevant_contracts = futures_df[futures_df['expiry'] >= first_day_of_backtest]

    if first_relevant_contracts.empty:
        raise ValueError(f"No available futures contracts found for the selected start date ({first_day_of_backtest}). The date might be too far in the past or a trading holiday.")

    first_contract_to_be_used = first_relevant_contracts.iloc[0]

    # Calculate the difference in months. If it's greater than 1, it implies
    # the contract for the starting month is unavailable.
    month_diff = (first_contract_to_be_used['expiry'].year - from_date.year) * 12 + (first_contract_to_be_used['expiry'].month - from_date.month)

    if month_diff > 1:
        raise ValueError(
            f"Historical contract data mismatch. The earliest available contract ({first_contract_to_be_used['tradingsymbol']}) "
            f"expires much later than the backtest start date ({from_date.strftime('%Y-%m')}). "
            "The Kite API does not provide instrument details for long-expired contracts. Please choose a more recent backtest period."
        )
    # --- End Sanity Check ---

    all_dates = pd.date_range(start=from_date, end=to_date, freq='B')
    continuous_data_list = []

    data_cache = {}
    active_contract_token = None

    for day in all_dates:
        day = day.date()

        # Find the front-month contract for the current day
        front_month_contracts = futures_df[futures_df['expiry'] >= day]
        if front_month_contracts.empty:
            continue

        current_contract = front_month_contracts.iloc[0]

        # If the active contract changes, we have a rollover
        if active_contract_token != current_contract['instrument_token']:
            active_contract_token = current_contract['instrument_token']
            logging.info(f"Active contract set to: {current_contract['tradingsymbol']} (Token: {active_contract_token})")

            # Download data for the new contract only if it's not already cached
            if active_contract_token not in data_cache:
                logging.info(f"Downloading data for new contract...")
                data_cache[active_contract_token] = download_historical_data(
                    kite,
                    active_contract_token,
                    from_date, # Fetch for the whole period to simplify
                    to_date,
                    TIMEFRAME
                )

        # Retrieve the day's data from the cached dataframe for the active contract
        contract_data = data_cache.get(active_contract_token)
        if contract_data is not None and not contract_data.empty:
            day_data = contract_data[contract_data['date'].dt.date == day].copy()
            day_data['tradingsymbol'] = current_contract['tradingsymbol']
            continuous_data_list.append(day_data)

    if not continuous_data_list:
        raise ValueError("Could not construct continuous futures data. Check date range and symbol.")

    continuous_data = pd.concat(continuous_data_list).sort_values(by='date').reset_index(drop=True)
    logging.info("--- Futures Data Pre-Fetch Complete ---")
    return continuous_data

def calculate_donchian_channel(data, period):
    """
    Calculates the Donchian Channel for the given data.
    """
    data['upper_band'] = data['high'].rolling(period).max()
    data['lower_band'] = data['low'].rolling(period).min()
    return data

def run_futures_backtest(data, kite):
    """
    Executes the backtest logic for the futures strategy with corrected state management.
    """
    logging.info("--- Starting Backtest Engine ---")
    trade_log = []
    position = None  # Can be 'LONG', 'SHORT', or None
    position_details = {}

    # Get lot size from the first instrument (assuming it's constant)
    try:
        instrument_details = kite.instruments(exchange=EXCHANGE, tradingsymbol=data['tradingsymbol'].iloc[0])
        lot_size = instrument_details[0]['lot_size']
        logging.info(f"Using Lot Size: {lot_size}")
    except Exception as e:
        logging.error(f"Could not fetch lot size. Defaulting to 1. Error: {e}")
        lot_size = 1

    for i in range(DONCHIAN_PERIOD, len(data)):
        row = data.iloc[i]

        # 1. Generate Signal based on Donchian Channel
        signal = None
        if row['low'] <= row['lower_band']:
            signal = 'LONG'
        elif row['high'] >= row['upper_band']:
            signal = 'SHORT'

        # 2. Process Exits: A position is closed if a reversal signal is generated.
        if position is not None and signal is not None and signal != position:
            entry_price = position_details['entry_price']
            pnl = 0

            if position == 'LONG':
                pnl = (row['close'] - entry_price) * lot_size
                logging.info(f"Reversal Signal: Closing LONG position at {row['close']}. PnL: {pnl:.2f}")
            elif position == 'SHORT':
                pnl = (entry_price - row['close']) * lot_size
                logging.info(f"Reversal Signal: Closing SHORT position at {row['close']}. PnL: {pnl:.2f}")

            trade_log.append({
                'entry_time': position_details['entry_time'],
                'exit_time': row['date'],
                'entry_price': entry_price,
                'exit_price': row['close'],
                'type': position,
                'pnl': pnl
            })
            position = None
            position_details = {}

        # 3. Process Entries: A new position is opened if there is no active position and a signal is generated.
        if position is None and signal is not None:
            position = signal
            position_details = {'entry_price': row['close'], 'entry_time': row['date']}
            logging.info(f"Entered {signal} position at {row['close']}")

    return pd.DataFrame(trade_log)

def calculate_performance_metrics(trade_log, initial_capital=1000000):
    """
    Calculates and prints key performance metrics for the futures strategy.
    """
    logging.info("--- Calculating Performance Metrics ---")
    if trade_log.empty:
        logging.warning("Trade log is empty. No performance metrics to calculate.")
        return

    total_pnl = trade_log['pnl'].sum()
    total_trades = len(trade_log)
    winning_trades = trade_log[trade_log['pnl'] > 0]
    losing_trades = trade_log[trade_log['pnl'] <= 0]

    win_rate = (len(winning_trades) / total_trades) * 100 if total_trades > 0 else 0

    # --- Drawdown Calculation ---
    trade_log['cumulative_pnl'] = trade_log['pnl'].cumsum()
    trade_log['portfolio_value'] = initial_capital + trade_log['cumulative_pnl']
    trade_log['running_max'] = trade_log['portfolio_value'].cummax()
    trade_log['drawdown'] = trade_log['running_max'] - trade_log['portfolio_value']
    max_drawdown = trade_log['drawdown'].max()

    # --- Sortino Ratio Calculation ---
    trade_log['daily_return'] = trade_log['pnl'] / initial_capital # Simplified daily return
    negative_returns = trade_log[trade_log['daily_return'] < 0]['daily_return']
    downside_deviation = negative_returns.std()

    average_return = trade_log['daily_return'].mean()

    sortino_ratio = (average_return / downside_deviation) * np.sqrt(252) if downside_deviation != 0 else 0 # Annualized


    print("\n--- Strategy Performance Metrics ---")
    print(f"Total Trades: {total_trades}")
    print(f"Winning Trades: {len(winning_trades)}")
    print(f"Losing Trades: {len(losing_trades)}")
    print(f"Win Rate: {win_rate:.2f}%")
    print(f"Total PnL: {total_pnl:.2f}")
    print(f"Maximum Drawdown: {max_drawdown:.2f}")
    print(f"Sortino Ratio: {sortino_ratio:.2f}")
    print("------------------------------------\n")


if __name__ == "__main__":
    if "YOUR_API_KEY" in [API_KEY, API_SECRET]:
        logging.error("Please fill in your API_KEY and API_SECRET.")
    else:
        access_token = get_access_token_automated(API_KEY)
        if access_token:
            kite = KiteConnect(api_key=API_KEY)
            kite.set_access_token(access_token)

            try:
                from_date_dt = datetime.strptime(FROM_DATE, '%Y-%m-%d')
                to_date_dt = datetime.strptime(TO_DATE, '%Y-%m-%d')

                # 1. Get all available futures contracts for the symbol
                futures_contracts_df = get_futures_contracts(kite, FUTURES_SYMBOL_PREFIX)
                if futures_contracts_df.empty:
                    raise ValueError(f"No futures contracts found for {FUTURES_SYMBOL_PREFIX}.")

                # 2. Get continuous historical data with rollover handling
                futures_data = get_monthly_futures_data(kite, futures_contracts_df, from_date_dt, to_date_dt)

                # 3. Calculate indicators
                data_with_indicators = calculate_donchian_channel(futures_data, DONCHIAN_PERIOD)

                # 4. Run the backtest
                trade_log = run_futures_backtest(data_with_indicators, kite)

                # 5. Save trade log and calculate performance
                if not trade_log.empty:
                    trade_log.to_csv(TRADE_LOG_FILE, index=False)
                    logging.info(f"Trade log saved to {TRADE_LOG_FILE}")
                else:
                    logging.info("No trades were executed.")

                calculate_performance_metrics(trade_log)

            except Exception as e:
                logging.error(f"An error occurred: {e}", exc_info=True)
        else:
            logging.error("Could not obtain access token.")