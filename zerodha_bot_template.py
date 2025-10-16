import time
import pandas as pd
from kiteconnect import KiteConnect
import logging

# --- 1. README: HOW TO USE THIS SCRIPT ---
#
# **SECURITY WARNING:**
# NEVER share your API Key, API Secret, or Access Token with anyone.
# NEVER commit them to a public or private repository.
# This script is a template. You MUST fill in your credentials yourself.
#
# **Prerequisites:**
# 1. A Zerodha Kite developer account.
# 2. Python installed on your machine.
# 3. Install the necessary libraries by running:
#    pip install kiteconnect pandas
#
# **Setup Steps:**
#
# Step 1: Get your API Key and API Secret
# - Go to https://developers.kite.trade/ and create an app to get your `api_key` and `api_secret`.
# - Add them to the configuration section below.
#
# Step 2: Generate your daily Access Token
# - The Access Token is valid for one trading day. You must generate a new one each morning.
# - You can generate it manually using the KiteConnect login flow. A simple way to do this is
#   by running a separate Python script once a day to print the access token.
#   (A sample script for this is available in the KiteConnect documentation).
# - Once you have the `access_token`, add it to the configuration section below.
#
# Step 3: Configure the Bot
# - Set the `TRADING_SYMBOL` to the instrument you want to trade (e.g., "RELIANCE").
# - Set the `QUANTITY` to the number of shares you want to trade in each transaction.
# - Adjust the `DONCHIAN_PERIOD` and `SYSTEM_STOP_LOSS_PCT` as needed.
#
# Step 4: Run the Bot
# - Open your terminal or command prompt.
# - Navigate to the directory where you saved this file.
# - Run the script: python zerodha_bot_template.py
# - The bot will now run continuously, checking for signals every 5 minutes.
#
# --- 2. CONFIGURATION ---

logging.basicConfig(level=logging.INFO)

# --- Credentials (FILL THESE IN) ---
API_KEY = "YOUR_API_KEY"
API_SECRET = "YOUR_API_SECRET"
ACCESS_TOKEN = "YOUR_ACCESS_TOKEN"

# --- Trading Parameters ---
TRADING_SYMBOL = "RELIANCE"  # Example: "RELIANCE" for NSE
EXCHANGE = "NSE"
QUANTITY = 1 # The number of shares to trade
PRODUCT_TYPE = "MIS" # Use "MIS" for intraday, "CNC" for delivery
ORDER_TYPE = "MARKET" # Using MARKET orders for simplicity

# --- Strategy Parameters ---
TIMEFRAME = "5minute"
DONCHIAN_PERIOD = 20
SYSTEM_STOP_LOSS_PCT = 5.0

# --- 3. KITE API AND TRADING LOGIC ---

class ZerodhaBot:
    def __init__(self, api_key, api_secret, access_token):
        self.kite = KiteConnect(api_key=api_key)
        try:
            self.kite.set_access_token(access_token)
            logging.info("Successfully authenticated with Kite.")
        except Exception as e:
            logging.error(f"Authentication failed: {e}")
            raise

        self.instrument_token = self._get_instrument_token()
        self.position = None # 'LONG', 'SHORT', or None
        self.entry_price = 0

    def _get_instrument_token(self):
        """Fetches the instrument token for the trading symbol."""
        try:
            instruments = self.kite.instruments(exchange=EXCHANGE)
            for instrument in instruments:
                if instrument['tradingsymbol'] == TRADING_SYMBOL:
                    logging.info(f"Instrument token for {TRADING_SYMBOL}: {instrument['instrument_token']}")
                    return instrument['instrument_token']
            raise ValueError(f"Instrument token for {TRADING_SYMBOL} not found.")
        except Exception as e:
            logging.error(f"Error fetching instrument token: {e}")
            raise

    def place_order(self, transaction_type):
        """Places a market order on Zerodha."""
        try:
            order_id = self.kite.place_order(
                variety=self.kite.VARIETY_REGULAR,
                exchange=EXCHANGE,
                tradingsymbol=TRADING_SYMBOL,
                transaction_type=transaction_type,
                quantity=QUANTITY,
                product=PRODUCT_TYPE,
                order_type=ORDER_TYPE
            )
            logging.info(f"Order placed successfully: {transaction_type} {QUANTITY} {TRADING_SYMBOL}, Order ID: {order_id}")
            return order_id
        except Exception as e:
            logging.error(f"Order placement failed: {e}")
            return None

    def get_historical_data(self):
        """Fetches the last N candles of historical data."""
        try:
            # We need DONCHIAN_PERIOD + a few extra candles for stability
            from_date = pd.Timestamp.now() - pd.Timedelta(days=5)
            to_date = pd.Timestamp.now()

            records = self.kite.historical_data(self.instrument_token, from_date, to_date, TIMEFRAME)
            df = pd.DataFrame(records)
            df['date'] = pd.to_datetime(df['date'])
            logging.info(f"Fetched {len(df)} candles for {TRADING_SYMBOL}")
            return df
        except Exception as e:
            logging.error(f"Error fetching historical data: {e}")
            return pd.DataFrame()

    def run_strategy_check(self):
        """Runs the Donchian Channel reversal strategy logic."""
        df = self.get_historical_data()
        if df.empty or len(df) < DONCHIAN_PERIOD:
            logging.warning("Not enough data to calculate Donchian Channel. Skipping check.")
            return

        # Calculate Donchian Channel on the latest data
        df['upper_band'] = df['high'].rolling(DONCHIAN_PERIOD).max()
        df['lower_band'] = df['low'].rolling(DONCHIAN_PERIOD).min()

        latest_candle = df.iloc[-1]
        logging.info(f"Latest Candle: Close={latest_candle['close']}, Lower={latest_candle['lower_band']:.2f}, Upper={latest_candle['upper_band']:.2f}")

        # --- Stop-Loss Check ---
        if self.position:
            pnl_pct = 0
            if self.position == 'LONG':
                pnl_pct = ((latest_candle['close'] - self.entry_price) / self.entry_price) * 100
            elif self.position == 'SHORT':
                pnl_pct = ((self.entry_price - latest_candle['close']) / self.entry_price) * 100

            if pnl_pct <= -SYSTEM_STOP_LOSS_PCT:
                logging.warning(f"STOP-LOSS triggered on {self.position} position! PnL: {pnl_pct:.2f}%")
                if self.position == 'LONG':
                    self.place_order(self.kite.TRANSACTION_TYPE_SELL)
                elif self.position == 'SHORT':
                    self.place_order(self.kite.TRANSACTION_TYPE_BUY)
                self.position = None
                self.entry_price = 0
                return # Exit after stop-loss

        # --- Reversal Logic ---
        # Close Short, Open Long
        if latest_candle['low'] <= latest_candle['lower_band']:
            if self.position == 'SHORT':
                logging.info("Signal: Closing SHORT position.")
                self.place_order(self.kite.TRANSACTION_TYPE_BUY)
                self.position = None

            if self.position is None:
                logging.info("Signal: Opening LONG position.")
                self.place_order(self.kite.TRANSACTION_TYPE_BUY)
                self.position = 'LONG'
                self.entry_price = latest_candle['close']

        # Close Long, Open Short
        elif latest_candle['high'] >= latest_candle['upper_band']:
            if self.position == 'LONG':
                logging.info("Signal: Closing LONG position.")
                self.place_order(self.kite.TRANSACTION_TYPE_SELL)
                self.position = None

            if self.position is None:
                logging.info("Signal: Opening SHORT position.")
                self.place_order(self.kite.TRANSACTION_TYPE_SELL)
                self.position = 'SHORT'
                self.entry_price = latest_candle['close']
        else:
            logging.info("No signal. Holding position.")


# --- 4. MAIN EXECUTION LOOP ---

if __name__ == "__main__":
    if API_KEY == "YOUR_API_KEY" or API_SECRET == "YOUR_API_SECRET" or ACCESS_TOKEN == "YOUR_ACCESS_TOKEN":
        logging.error("Please fill in your API_KEY, API_SECRET, and ACCESS_TOKEN in the script.")
    else:
        bot = ZerodhaBot(API_KEY, API_SECRET, ACCESS_TOKEN)

        # The main loop runs indefinitely.
        # It checks for a signal every 5 minutes (300 seconds).
        while True:
            try:
                # We run the check at the start of each 5-minute interval.
                # This ensures we are acting on the most recently completed candle.
                current_minute = time.localtime().tm_min
                if current_minute % 5 == 0:
                    logging.info("--- New 5-minute interval. Running strategy check. ---")
                    bot.run_strategy_check()
                    # Sleep for a little over a minute to ensure we don't run twice in the same minute
                    time.sleep(61)
                else:
                    # Sleep until the next 5-minute mark
                    sleep_time = (5 - (current_minute % 5)) * 60 - time.localtime().tm_sec
                    logging.info(f"Sleeping for {sleep_time:.2f} seconds until the next 5-min candle.")
                    time.sleep(max(1, sleep_time))

            except KeyboardInterrupt:
                logging.info("Bot stopped by user.")
                break
            except Exception as e:
                logging.error(f"An unexpected error occurred: {e}")
                time.sleep(60) # Wait a minute before retrying