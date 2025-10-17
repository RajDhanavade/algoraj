import time
import pandas as pd
from kiteconnect import KiteConnect, KiteTicker
import logging
import threading

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
ACCESS_TOKEN = "YOUR_ACCESS_TOKEN"

# --- Mode Configuration ---
TESTING_MODE = True # Set to True for safe testing, False for live trading with full capital.

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

        # --- Safety Override for Testing Mode ---
        self.quantity = 1 if testing_mode else QUANTITY
        self.leverage = 1.0 if testing_mode else LEVERAGE
        if testing_mode:
            logging.warning("TESTING MODE is ON. Trading with 1 share and no leverage.")

        self.kws.on_ticks = self.on_ticks
        self.kws.on_connect = self.on_connect
        self.kws.on_close = self.on_close

    def _get_instrument_token(self):
        instruments = self.kite.instruments(exchange=EXCHANGE)
        for instrument in instruments:
            if instrument['tradingsymbol'] == TRADING_SYMBOL:
                return instrument['instrument_token']
        raise ValueError(f"Instrument token for {TRADING_SYMBOL} not found.")

    def place_order(self, transaction_type):
        try:
            order_id = self.kite.place_order(
                variety=self.kite.VARIETY_REGULAR, exchange=EXCHANGE,
                tradingsymbol=TRADING_SYMBOL, transaction_type=transaction_type,
                quantity=self.quantity, product=PRODUCT_TYPE, order_type=ORDER_TYPE
            )
            logging.info(f"Order placed: {transaction_type} {self.quantity} {TRADING_SYMBOL}, ID: {order_id}")
            return order_id
        except Exception as e:
            logging.error(f"Order placement failed: {e}")
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
                self.place_order(self.kite.TRANSACTION_TYPE_SELL)
                self.position = None
                return

            # Take-Profit Check
            if ltp >= self.upper_band:
                logging.info("TAKE-PROFIT signal. Closing LONG position.")
                self.place_order(self.kite.TRANSACTION_TYPE_SELL)
                self.position = None
                return

        # Entry Check
        if self.position is None and ltp <= self.lower_band:
            logging.info("BUY signal. Opening LONG position.")
            self.place_order(self.kite.TRANSACTION_TYPE_BUY)
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
    if "YOUR_API_KEY" in [API_KEY, API_SECRET, ACCESS_TOKEN]:
        logging.error("Please fill in your API credentials in the script.")
    else:
        bot = ZerodhaWebSocketBot(API_KEY, API_SECRET, ACCESS_TOKEN, TESTING_MODE)
        bot.start()