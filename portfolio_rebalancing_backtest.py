import pandas as pd
from kiteconnect import KiteConnect
import logging
from datetime import datetime, timedelta
import os
import numpy as np
import webbrowser
from flask import Flask, request
import threading

try:
    from tabulate import tabulate
except ImportError:
    print("Please install 'tabulate' library: pip install tabulate")
    exit()

# --- Logging Configuration ---
logging.basicConfig(level=logging.INFO)

# --- Zerodha API Configuration ---
# It's recommended to use environment variables for API key and secret
API_KEY = os.environ.get("KITE_API_KEY", "YOUR_API_KEY")
API_SECRET = os.environ.get("KITE_API_SECRET", "YOUR_API_SECRET")
ACCESS_TOKEN = "YOUR_ACCESS_TOKEN"  # Generate this manually and paste here

# --- Backtest Configuration ---
INITIAL_CAPITAL = 100000
START_DATE = "2020-01-01"
END_DATE = "2024-01-01"
ASSETS = {
    "GOLDBEES": {
        "instrument_token": 35024,
        "exchange": "NSE"
    },
    "NIFTYBEES": {
        "instrument_token": 159233,
        "exchange": "NSE"
    }
}

# --- Automated Token Generation ---
ACCESS_TOKEN_FILE = "access_token.txt"

app = Flask(__name__)
kite = KiteConnect(api_key=API_KEY)
access_token_container = {"token": None}

@app.route("/callback")
def callback():
    request_token = request.args.get("request_token")
    if request_token:
        try:
            session = kite.generate_session(request_token, api_secret=API_SECRET)
            access_token = session["access_token"]
            access_token_container["token"] = access_token
            with open(ACCESS_TOKEN_FILE, "w") as f:
                f.write(access_token)
            logging.info("Access token generated and saved successfully.")
            return "Access token generated successfully! You can close this tab."
        except Exception as e:
            logging.error(f"Error generating session: {e}")
            return "Error generating access token. Please check the logs."
    return "No request token found."

def run_flask_app():
    app.run(port=5000)

def automated_login():
    """Automates the Kite Connect login process."""
    print("Attempting automated login...")
    login_url = kite.login_url()
    webbrowser.open(login_url)

    flask_thread = threading.Thread(target=run_flask_app)
    flask_thread.daemon = True
    flask_thread.start()

    while access_token_container["token"] is None:
        pass  # Wait for the token to be generated

    return access_token_container["token"]

# --- Kite Connect Initialization ---
def initialize_kiteconnect():
    """Initializes the KiteConnect client, automating login if necessary."""
    global ACCESS_TOKEN
    if os.path.exists(ACCESS_TOKEN_FILE):
        with open(ACCESS_TOKEN_FILE, "r") as f:
            ACCESS_TOKEN = f.read().strip()
        logging.info("Loaded access token from file.")
    else:
        ACCESS_TOKEN = automated_login()

    try:
        kite.set_access_token(ACCESS_TOKEN)
        logging.info("Kite Connect session initialized successfully.")
        return kite
    except Exception as e:
        logging.error(f"Error setting access token: {e}")
        # If token is invalid, try to log in again
        ACCESS_TOKEN = automated_login()
        kite.set_access_token(ACCESS_TOKEN)
        logging.info("Kite Connect session initialized successfully after re-login.")
        return kite

# --- Data Fetching ---
def fetch_historical_data(kite, instrument_token, start_date, end_date, interval="day"):
    """
    Fetches historical data from Kite API, handling the 3-month limit by chunking.
    """
    from_date = datetime.strptime(start_date, '%Y-%m-%d')
    to_date = datetime.strptime(end_date, '%Y-%m-%d')
    all_data = []

    logging.info(f"Fetching data for instrument {instrument_token} from {start_date} to {end_date}")

    while from_date <= to_date:
        # Fetch data in 90-day chunks
        chunk_to_date = from_date + timedelta(days=90)
        if chunk_to_date > to_date:
            chunk_to_date = to_date

        try:
            records = kite.historical_data(instrument_token, from_date, chunk_to_date, interval)
            if records:
                all_data.extend(records)
            from_date = chunk_to_date + timedelta(days=1)
        except Exception as e:
            logging.error(f"Error fetching data for instrument {instrument_token}: {e}")
            return None

    if not all_data:
        logging.warning(f"No data received for instrument {instrument_token}")
        return pd.DataFrame()

    df = pd.DataFrame(all_data)
    df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
    df.set_index('date', inplace=True)
    return df

def get_all_asset_data(kite, assets, start_date, end_date):
    """
    Fetches historical data for all assets and combines their 'close' prices into a single DataFrame.
    """
    combined_data = {}
    for asset_name, details in assets.items():
        data = fetch_historical_data(
            kite,
            details["instrument_token"],
            start_date,
            end_date
        )
        if data is not None and not data.empty:
            combined_data[asset_name] = data['close']
        else:
            logging.error(f"Could not fetch data for {asset_name}. Backtest cannot proceed.")
            return None

    # Combine into a single DataFrame and align dates
    df = pd.DataFrame(combined_data)
    df.dropna(inplace=True)
    logging.info("Successfully fetched and combined data for all assets.")
    return df


# --- Backtesting Engine ---
def run_rebalancing_backtest(data, initial_capital):
    """
    Runs the backtest for the monthly rebalancing strategy.
    """
    logging.info("Running monthly rebalancing backtest...")
    assets = data.columns.tolist()
    cash = initial_capital
    positions = {asset: 0 for asset in assets}
    portfolio_values = []
    trade_log = []

    last_rebalance_month = -1

    for i, (date, row) in enumerate(data.iterrows()):
        current_prices = row

        # Initial investment on the first day
        if i == 0:
            investment_per_asset = initial_capital / len(assets)
            for asset in assets:
                price = current_prices[asset]
                shares_to_buy = investment_per_asset / price
                positions[asset] = shares_to_buy
                cash -= shares_to_buy * price
                trade_log.append({
                    "date": date, "asset": asset, "action": "BUY",
                    "quantity": shares_to_buy, "price": price,
                    "reason": "Initial allocation"
                })
            logging.info(f"Initial portfolio allocation done on {date.date()}.")

        # Monthly rebalancing check (skip the first day)
        if i > 0 and date.month != last_rebalance_month:
            last_rebalance_month = date.month

            # Calculate current portfolio value and asset weights
            current_portfolio_value = cash
            asset_values = {}
            for asset in assets:
                value = positions[asset] * current_prices[asset]
                asset_values[asset] = value
                current_portfolio_value += value

            # Determine target value for each asset
            target_value_per_asset = current_portfolio_value / len(assets)

            # Rebalance
            logging.info(f"Rebalancing portfolio on {date.date()}...")
            for asset in assets:
                current_value = asset_values[asset]
                price = current_prices[asset]

                if current_value > target_value_per_asset:
                    # Sell overweight asset
                    amount_to_sell = current_value - target_value_per_asset
                    shares_to_sell = amount_to_sell / price
                    positions[asset] -= shares_to_sell
                    cash += shares_to_sell * price
                    trade_log.append({
                        "date": date, "asset": asset, "action": "SELL",
                        "quantity": shares_to_sell, "price": price,
                        "reason": "Rebalancing"
                    })
                else:
                    # Buy underweight asset
                    amount_to_buy = target_value_per_asset - current_value
                    shares_to_buy = amount_to_buy / price
                    positions[asset] += shares_to_buy
                    cash -= shares_to_buy * price
                    trade_log.append({
                        "date": date, "asset": asset, "action": "BUY",
                        "quantity": shares_to_buy, "price": price,
                        "reason": "Rebalancing"
                    })

        # Calculate and record daily portfolio value
        daily_portfolio_value = cash
        for asset in assets:
            daily_portfolio_value += positions[asset] * current_prices[asset]
        portfolio_values.append({'date': date, 'portfolio_value': daily_portfolio_value})

    portfolio_df = pd.DataFrame(portfolio_values).set_index('date')
    trade_log_df = pd.DataFrame(trade_log)

    logging.info("Rebalancing backtest complete.")
    return portfolio_df, trade_log_df

def run_buy_and_hold_strategy(data, initial_capital):
    """
    Simulates a buy-and-hold strategy for comparison.
    """
    logging.info("Running buy-and-hold strategy simulation...")
    assets = data.columns.tolist()

    # Initial investment
    first_day_prices = data.iloc[0]
    investment_per_asset = initial_capital / len(assets)
    positions = {asset: investment_per_asset / first_day_prices[asset] for asset in assets}

    # Calculate portfolio value over time
    portfolio_values = []
    for date, row in data.iterrows():
        daily_value = sum(positions[asset] * row[asset] for asset in assets)
        portfolio_values.append({'date': date, 'portfolio_value': daily_value})

    portfolio_df = pd.DataFrame(portfolio_values).set_index('date')
    logging.info("Buy-and-hold simulation complete.")
    return portfolio_df


# --- Performance Analysis ---
def calculate_and_display_metrics(strategy_results, trade_log, strategy_name):
    """
    Calculates and displays performance metrics for a given strategy.
    """
    print(f"\n--- Performance Metrics: {strategy_name} ---")

    # Overall PnL
    initial_value = strategy_results['portfolio_value'].iloc[0]
    final_value = strategy_results['portfolio_value'].iloc[-1]
    total_pnl = final_value - initial_value
    total_return_pct = (total_pnl / initial_value) * 100

    # Trade Analysis (only for rebalancing strategy)
    if trade_log is not None and not trade_log.empty:
        total_trades = len(trade_log)
        # Note: A simple win/loss calculation is tricky for rebalancing.
        # We'll focus on overall portfolio performance.
    else:
        total_trades = 0

    # Sharpe Ratio and Volatility
    daily_returns = strategy_results['portfolio_value'].pct_change().dropna()
    annualized_return = daily_returns.mean() * 252
    annualized_volatility = daily_returns.std() * np.sqrt(252)
    sharpe_ratio = annualized_return / annualized_volatility if annualized_volatility != 0 else 0

    # Max Drawdown
    cumulative_returns = (1 + daily_returns).cumprod()
    running_max = np.maximum.accumulate(cumulative_returns)
    drawdown = (cumulative_returns - running_max) / running_max
    max_drawdown = np.min(drawdown) * 100 if not drawdown.empty else 0

    metrics = [
        ["Initial Portfolio Value", f"{initial_value:,.2f}"],
        ["Final Portfolio Value", f"{final_value:,.2f}"],
        ["Total PnL", f"{total_pnl:,.2f}"],
        ["Total Return (%)", f"{total_return_pct:.2f}%"],
        ["Annualized Return (%)", f"{annualized_return * 100:.2f}%"],
        ["Annualized Volatility (%)", f"{annualized_volatility * 100:.2f}%"],
        ["Sharpe Ratio", f"{sharpe_ratio:.2f}"],
        ["Maximum Drawdown (%)", f"{max_drawdown:.2f}%"],
        ["Total Trades", total_trades]
    ]

    print(tabulate(metrics, headers=["Metric", "Value"], tablefmt="grid"))

def save_trade_log(trade_log, filename="trade_log.csv"):
    """Saves the trade log to a CSV file."""
    if trade_log is not None and not trade_log.empty:
        trade_log.to_csv(filename, index=False)
        logging.info(f"Trade log saved to {filename}")


# --- Main Execution ---
if __name__ == "__main__":
    # 1. Initialize Kite Connect
    kite = initialize_kiteconnect()

    if kite:
        # 2. Fetch historical data
        historical_data = get_all_asset_data(kite, ASSETS, START_DATE, END_DATE)

        if historical_data is not None and not historical_data.empty:
            logging.info("Historical data fetched successfully.")

            # 3. Run the rebalancing strategy backtest
            rebalancing_results, trade_log = run_rebalancing_backtest(historical_data, INITIAL_CAPITAL)

            # 4. Run the buy-and-hold comparison
            buy_and_hold_results = run_buy_and_hold_strategy(historical_data, INITIAL_CAPITAL)

            # 5. Display performance metrics
            calculate_and_display_metrics(rebalancing_results, trade_log, "Monthly Rebalancing Strategy")
            calculate_and_display_metrics(buy_and_hold_results, None, "Buy and Hold Strategy")

            # 6. Save the trade log
            save_trade_log(trade_log)
        else:
            logging.error("Failed to fetch historical data. Exiting.")
    else:
        logging.error("Failed to initialize Kite Connect. Exiting.")
