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
# **Prerequisites:**
# 1. A Zerodha Kite developer account.
# 2. Python installed on your machine.
# 3. Install libraries: pip install kiteconnect pandas
#
# **Setup Steps:**
# 1. Get your API Key and API Secret from https://developers.kite.trade/
# 2. Generate your daily Access Token.
# 3. Fill in your credentials in the configuration section.
# 4. Configure the trading and strategy parameters.
# 5. Run the script: python zerodha_bot_template.py
#
# --- 2. CONFIGURATION ---

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Credentials (FILL THESE IN) ---
API_KEY = "YOUR_API_KEY"
API_SECRET = "YOUR_API_SECRET"
ACCESS_TOKEN = "YOUR_ACCESS_TOKEN"

# --- Trading Parameters ---
TRADING_SYMBOL = "RELIANCE"
EXCHANGE = "NSE"
QUANTITY = 1
PRODUCT_TYPE = "MIS"
ORDER_TYPE = "MARKET"

# --- Strategy Parameters ---
TIMEFRAME = "5minute"
DONCHIAN_PERIOD = 20
SYSTEM_STOP_LOSS_PCT = 5.0

# --- 3. REAL-TIME TRADING BOT CLASS ---

class ZerodhaWebSocketBot:
    def __init__(self, api_key, api_secret, access_token):
        # --- Initialize Connections ---
        self.kite = KiteConnect(api_key=api_key)
        self.kite.set_access_token(access_token)
        self.kws = KiteTicker(api_key, access_token)

        # --- Bot State ---
        self.instrument_token = self._get_instrument_token()
        self.position = None  # 'LONG', 'SHORT', or None
        self.entry_price = 0
        self.upper_band = 0
        self.lower_band = 0
        self.last_band_update = None

        # --- WebSocket Callbacks ---
        self.kws.on_ticks = self.on_ticks
        self.kws.on_connect = self.on_connect
        self.kws.on_close = self.on_close

    def _get_instrument_token(self):
        """Fetches the instrument token for the trading symbol."""
        instruments = self.kite.instruments(exchange=EXCHANGE)
        for instrument in instruments:
            if instrument['tradingsymbol'] == TRADING_SYMBOL:
                return instrument['instrument_token']
        raise ValueError(f"Instrument token for {TRADING_SYMBOL} not found.")

    def place_order(self, transaction_type):
        """Places a market order."""
        try:
            order_id = self.kite.place_order(
                variety=self.kite.VARIETY_REGULAR, exchange=EXCHANGE,
                tradingsymbol=TRADING_SYMBOL, transaction_type=transaction_type,
                quantity=QUANTITY, product=PRODUCT_TYPE, order_type=ORDER_TYPE
            )
            logging.info(f"Order placed: {transaction_type} {QUANTITY} {TRADING_SYMBOL}, ID: {order_id}")
            return order_id
        except Exception as e:
            logging.error(f"Order placement failed: {e}")
            return None

    def update_donchian_bands(self):
        """Fetches historical data to calculate and update Donchian bands."""
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

    # --- WebSocket Event Handlers ---
    def on_ticks(self, ws, ticks):
        """This function is called for every tick received."""
        if not ticks:
            return

        ltp = ticks[0]['last_price']

        # --- Stop-Loss Check (on every tick) ---
        if self.position:
            pnl_pct = 0
            if self.position == 'LONG':
                pnl_pct = ((ltp - self.entry_price) / self.entry_price) * 100
            elif self.position == 'SHORT':
                pnl_pct = ((self.entry_price - ltp) / self.entry_price) * 100

            if pnl_pct <= -SYSTEM_STOP_LOSS_PCT:
                logging.warning(f"STOP-LOSS on {self.position} position! PnL: {pnl_pct:.2f}%")
                if self.position == 'LONG': self.place_order(self.kite.TRANSACTION_TYPE_SELL)
                else: self.place_order(self.kite.TRANSACTION_TYPE_BUY)
                self.position = None
                return

        # --- Reversal Logic (on every tick) ---
        # Close Short, Open Long
        if ltp <= self.lower_band:
            if self.position == 'SHORT':
                logging.info("Signal: Closing SHORT position.")
                self.place_order(self.kite.TRANSACTION_TYPE_BUY)
                self.position = None
            if self.position is None:
                logging.info("Signal: Opening LONG position.")
                self.place_order(self.kite.TRANSACTION_TYPE_BUY)
                self.position = 'LONG'
                self.entry_price = ltp

        # Close Long, Open Short
        elif ltp >= self.upper_band:
            if self.position == 'LONG':
                logging.info("Signal: Closing LONG position.")
                self.place_order(self.kite.TRANSACTION_TYPE_SELL)
                self.position = None
            if self.position is None:
                logging.info("Signal: Opening SHORT position.")
                self.place_order(self.kite.TRANSACTION_TYPE_SELL)
                self.position = 'SHORT'
                self.entry_price = ltp

    def on_connect(self, ws, response):
        """Called upon a successful WebSocket connection."""
        logging.info("WebSocket connected. Subscribing to ticks.")
        self.kite.subscribe([self.instrument_token])
        self.kite.set_mode(self.kite.MODE_FULL, [self.instrument_token])
        # Update bands immediately on connection
        self.update_donchian_bands()

    def on_close(self, ws, code, reason):
        """Called when the WebSocket connection is closed."""
        logging.warning(f"WebSocket closed: {code} - {reason}")

    def start(self):
        """Starts the WebSocket connection and the periodic band update."""
        logging.info("Starting bot...")
        # Run WebSocket in a separate thread
        ws_thread = threading.Thread(target=self.kws.connect)
        ws_thread.daemon = True
        ws_thread.start()

        # Main thread will handle periodic updates
        while True:
            try:
                time.sleep(5) # Main loop check interval
                # Update Donchian bands every 5 minutes
                if self.last_band_update is None or (time.time() - self.last_band_update) >= 300:
                    self.update_donchian_bands()
            except KeyboardInterrupt:
                logging.info("Stopping bot...")
                self.kws.close()
                break

# --- 4. MAIN EXECUTION ---

if __name__ == "__main__":
    if "YOUR_API_KEY" in [API_KEY, API_SECRET, ACCESS_TOKEN]:
        logging.error("Please fill in your API credentials in the script.")
    else:
        bot = ZerodhaWebSocketBot(API_KEY, API_SECRET, ACCESS_TOKEN)
        bot.start()