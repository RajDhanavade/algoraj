import time
import pandas as pd
from kiteconnect import KiteConnect, KiteTicker
import logging
import threading
import os
import requests

# --- 1. README: HOW TO USE THIS SCRIPT ---
#
# **SECURITY WARNING:**
# This script is a template. You MUST fill in your credentials yourself.
#
# **Setup Steps:**
# 1. Get your API Key and API Secret from https://developers.kite.trade/
# 2. Generate your daily Access Token.
# 3. Fill in your credentials.
# 4. Set TESTING_MODE to True for initial testing with 1 share and no leverage.
# 5. Once confident, set TESTING_MODE to False to use your full QUANTITY and LEVERAGE.
# 6. Run the script: python zerodha_bot_template.py
#
# --- 2. CONFIGURATION ---

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Credentials (FILL THESE IN) ---
API_KEY = "YOUR_API_KEY"
API_SECRET = "YOUR_API_SECRET"
# ACCESS_TOKEN is now passed automatically by `start_bot.py`

# --- Mode Configuration ---
TESTING_MODE = True # Set to True for safe testing, False for live trading with full capital.

# --- Telegram Configuration (FILL THESE IN) ---
TELEGRAM_BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_TELEGRAM_CHAT_ID"

# --- Trading Parameters ---
TRADING_SYMBOL = "RELIANCE"
EXCHANGE = "NSE"
QUANTITY = 10 # Full quantity for live mode
PRODUCT_TYPE = "MIS"
ORDER_TYPE = "MARKET"

# --- Strategy Parameters ---
TIMEFRAME = "5minute"
DONCHIAN_PERIOD = 20
SYSTEM_STOP_LOSS_PCT = 5.0
LEVERAGE = 2.0 # Full leverage for live mode

# --- 3. REAL-TIME TRADING BOT CLASS ---

class ZerodhaWebSocketBot:
    def __init__(self, api_key, api_secret, access_token, testing_mode):
        self.kite = KiteConnect(api_key=api_key)
        self.kite.set_access_token(access_token)
        self.kws = KiteTicker(api_key, access_token)

        self.instrument_token = self._get_instrument_token()
        self.position = None
        self.entry_price = 0
        self.upper_band = 0
        self.lower_band = 0
        self.last_band_update = None

        self.quantity = 1 if testing_mode else QUANTITY
        if testing_mode:
            logging.warning("TESTING MODE is ON. Trading with 1 share.")
            self.send_telegram_message("Bot started in TESTING MODE (1 share, no leverage).")
        else:
            self.send_telegram_message(f"Bot started in LIVE MODE ({self.quantity} shares).")

        self.kws.on_ticks = self.on_ticks
        self.kws.on_connect = self.on_connect
        self.kws.on_close = self.on_close

    def send_telegram_message(self, message):
        """Sends a message to a Telegram user or group."""
        if "YOUR_TELEGRAM" in [TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]:
            logging.warning("Telegram credentials not set. Skipping notification.")
            return

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message,
            'parse_mode': 'Markdown'
        }
        try:
            response = requests.post(url, json=payload)
            if response.status_code != 200:
                logging.error(f"Failed to send Telegram message: {response.text}")
        except Exception as e:
            logging.error(f"Exception while sending Telegram message: {e}")

    def _get_instrument_token(self):
        instruments = self.kite.instruments(exchange=EXCHANGE)
        for instrument in instruments:
            if instrument['tradingsymbol'] == TRADING_SYMBOL:
                return instrument['instrument_token']
        raise ValueError(f"Instrument token for {TRADING_SYMBOL} not found.")

    def place_order(self, transaction_type, ltp):
        try:
            order_id = self.kite.place_order(
                variety=self.kite.VARIETY_REGULAR, exchange=EXCHANGE,
                tradingsymbol=TRADING_SYMBOL, transaction_type=transaction_type,
                quantity=self.quantity, product=PRODUCT_TYPE, order_type=ORDER_TYPE
            )
            logging.info(f"Order placed: {transaction_type} {self.quantity} {TRADING_SYMBOL}, ID: {order_id}")

            # --- Send Notification ---
            trade_msg = f"*{transaction_type.upper()}* order placed for {self.quantity} {TRADING_SYMBOL} at approx. {ltp:.2f}."
            self.send_telegram_message(trade_msg)
            return order_id
        except Exception as e:
            logging.error(f"Order placement failed: {e}")
            self.send_telegram_message(f"ALERT: Order placement failed for {TRADING_SYMBOL}. Error: {e}")
            return None

    def update_donchian_bands(self):
        try:
            from_date = pd.Timestamp.now() - pd.Timedelta(days=5)
            to_date = pd.Timestamp.now()
            records = self.kite.historical_data(self.instrument_token, from_date, to_date, TIMEFRAME)
            df = pd.DataFrame(records)

            if len(df) < DONCHIAN_PERIOD:
                logging.warning("Not enough data for Donchian calculation.")
                return

            self.upper_band = df['high'].rolling(DONCHIAN_PERIOD).max().iloc[-1]
            self.lower_band = df['low'].rolling(DONCHIAN_PERIOD).min().iloc[-1]
            self.last_band_update = time.time()
            logging.info(f"Donchian bands updated: Lower={self.lower_band:.2f}, Upper={self.upper_band:.2f}")
        except Exception as e:
            logging.error(f"Error updating Donchian bands: {e}")

    def on_ticks(self, ws, ticks):
        if not ticks: return
        ltp = ticks[0]['last_price']

        if self.position == 'LONG':
            pnl_pct = ((ltp - self.entry_price) / self.entry_price) * 100

            # Stop-Loss Check
            if pnl_pct <= -SYSTEM_STOP_LOSS_PCT:
                logging.warning(f"STOP-LOSS triggered! PnL: {pnl_pct:.2f}%")
                self.send_telegram_message(f"🛑 *STOP-LOSS* triggered for {TRADING_SYMBOL} at {ltp:.2f}. PnL: {pnl_pct:.2f}%")
                self.place_order(self.kite.TRANSACTION_TYPE_SELL, ltp)
                self.position = None
                return

            # Take-Profit Check
            if ltp >= self.upper_band:
                logging.info("TAKE-PROFIT signal. Closing LONG position.")
                self.send_telegram_message(f"✅ *TAKE-PROFIT* for {TRADING_SYMBOL} at {ltp:.2f}. PnL: {pnl_pct:.2f}%")
                self.place_order(self.kite.TRANSACTION_TYPE_SELL, ltp)
                self.position = None
                return

        # Entry Check
        if self.position is None and ltp <= self.lower_band:
            logging.info("BUY signal. Opening LONG position.")
            self.place_order(self.kite.TRANSACTION_TYPE_BUY, ltp)
            self.position = 'LONG'
            self.entry_price = ltp

    def on_connect(self, ws, response):
        logging.info("WebSocket connected. Subscribing to ticks.")
        self.kite.subscribe([self.instrument_token])
        self.kite.set_mode(self.kite.MODE_FULL, [self.instrument_token])
        self.update_donchian_bands()

    def on_close(self, ws, code, reason):
        logging.warning(f"WebSocket closed: {code} - {reason}")

    def start(self):
        logging.info("Starting bot...")
        ws_thread = threading.Thread(target=self.kws.connect)
        ws_thread.daemon = True
        ws_thread.start()

        while True:
            try:
                time.sleep(5)
                if self.last_band_update is None or (time.time() - self.last_band_update) >= 300:
                    self.update_donchian_bands()
            except KeyboardInterrupt:
                logging.info("Stopping bot...")
                self.kws.close()
                break

if __name__ == "__main__":
    access_token = os.getenv("ZERODHA_ACCESS_TOKEN")
    if "YOUR_API_KEY" in [API_KEY, API_SECRET] or not access_token:
        logging.error("Please fill in your API_KEY and API_SECRET in the `start_bot.py` script and run it to generate the access token.")
    else:
        bot = ZerodhaWebSocketBot(API_KEY, API_SECRET, access_token, TESTING_MODE)
        bot.start()