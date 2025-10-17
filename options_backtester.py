import pandas as pd
import numpy as np
from kiteconnect import KiteConnect
import os
import logging
from datetime import datetime, timedelta
import time

# --- Configuration ---
API_KEY = "YOUR_API_KEY"
API_SECRET = "YOUR_API_SECRET"
ACCESS_TOKEN = "YOUR_ACCESS_TOKEN"

INDEX_SYMBOL = "NIFTY BANK"
EXCHANGE_IND = "NSE"
EXCHANGE_OPT = "NFO"
INITIAL_CAPITAL = 300000
TRADE_LOG_FILE = 'options_trades_v2.csv'

TIMEFRAME = "5minute"
DONCHIAN_PERIOD = 20
TARGET_DELTA_PROXY_STRIKES = 3
STOP_LOSS_PCT = 3.0

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_instrument_token(kite, symbol, exchange):
    instruments = kite.instruments(exchange=exchange)
    for instrument in instruments:
        if instrument['tradingsymbol'] == symbol:
            return instrument['instrument_token']
    raise ValueError(f"Token for {symbol} on {exchange} not found.")

def download_historical_data(kite, instrument_token, timeframe, from_date, to_date):
    logging.info(f"Downloading data for instrument {instrument_token}...")
    try:
        records = kite.historical_data(instrument_token, from_date, to_date, timeframe, continuous=False)
        df = pd.DataFrame(records)
        if not df.empty:
            df['date'] = pd.to_datetime(df['date']).dt.tz_convert('Asia/Kolkata')
        return df
    except Exception as e:
        logging.error(f"Error downloading data for {instrument_token}: {e}")
        return pd.DataFrame()

def pre_fetch_all_data(kite, index_token, instruments_df, from_date, to_date):
    logging.info("--- Starting Data Pre-Fetch Process ---")
    index_data = download_historical_data(kite, index_token, TIMEFRAME, from_date, to_date)
    if index_data.empty: raise ValueError("Could not download index data.")

    monthly_expiries = sorted(list(instruments_df[(instruments_df['name'] == 'BANKNIFTY') & (instruments_df['expiry'] >= from_date.date()) & (instruments_df['expiry'] <= to_date.date() + timedelta(days=35)) & (instruments_df['expiry'].apply(lambda x: x.is_month_end))]['expiry'].unique()))
    relevant_options = instruments_df[(instruments_df['name'] == 'BANKNIFTY') & (instruments_df['expiry'].isin(monthly_expiries))]
    logging.info(f"Found {len(relevant_options)} relevant option contracts to pre-fetch.")

    options_data_cache = {}
    for _, option in relevant_options.iterrows():
        token = option['instrument_token']
        opt_from_date = from_date
        opt_to_date = to_date
        options_data_cache[token] = download_historical_data(kite, token, TIMEFRAME, opt_from_date, opt_to_date)
        time.sleep(0.4)
    logging.info("--- Data Pre-Fetch Complete ---")
    return index_data, options_data_cache

def calculate_donchian_channel(data, period):
    data['upper_band'] = data['high'].rolling(period).max()
    data['lower_band'] = data['low'].rolling(period).min()
    return data

def find_option_to_trade(underlying_spot, option_type, current_date, instruments_df):
    future_expiries = sorted([dt for dt in instruments_df['expiry'].unique() if dt >= current_date.date()])
    if not future_expiries: return None, None

    monthly_expiry = None
    for expiry in future_expiries:
        if expiry.is_month_end:
            monthly_expiry = expiry
            break
    if not monthly_expiry: return None, None

    filtered_options = instruments_df[(instruments_df['expiry'] == monthly_expiry) & (instruments_df['instrument_type'] == option_type)]

    if option_type == 'PE':
        itm_options = filtered_options[filtered_options['strike'] > underlying_spot].sort_values(by='strike', ascending=True)
    else:
        itm_options = filtered_options[filtered_options['strike'] < underlying_spot].sort_values(by='strike', ascending=False)

    if len(itm_options) < TARGET_DELTA_PROXY_STRIKES: return None, None
    selected_option = itm_options.iloc[TARGET_DELTA_PROXY_STRIKES - 1]
    return selected_option['tradingsymbol'], selected_option['instrument_token']

def get_option_price(token, timestamp, cache):
    if token in cache and not cache[token].empty:
        data = cache[token]
        price_row = data[data['date'] <= timestamp]
        if not price_row.empty:
            return price_row.iloc[-1]['close']
    return None

def run_backtest(index_data, instruments_df, options_cache):
    logging.info("--- Starting Backtest Engine ---")
    trade_log = []
    position = None

    for i in range(DONCHIAN_PERIOD, len(index_data)):
        row = index_data.iloc[i]

        if position:
            current_option_price = get_option_price(position['token'], row['date'], options_cache)
            if current_option_price is None: continue

            margin = position['margin']
            stop_loss_pnl = - (margin * (STOP_LOSS_PCT / 100))
            current_pnl = (position['entry_premium'] - current_option_price) * position['lot_size']

            if current_pnl <= stop_loss_pnl:
                position.update({'exit_time': row['date'], 'exit_price': current_option_price, 'pnl': current_pnl, 'exit_reason': 'Stop-Loss'})
                trade_log.append(position)
                logging.info(f"Closed {position['symbol']} on Stop-Loss. PnL: {current_pnl:.2f}")
                position = None
                continue

        is_reversal = False
        if (row['low'] <= row['lower_band'] and position and position['type'] == 'CALL') or \
           (row['high'] >= row['upper_band'] and position and position['type'] == 'PUT'):
            is_reversal = True
            exit_price = get_option_price(position['token'], row['date'], options_cache)
            if exit_price:
                pnl = (position['entry_premium'] - exit_price) * position['lot_size']
                position.update({'exit_time': row['date'], 'exit_price': exit_price, 'pnl': pnl, 'exit_reason': 'Reversal'})
                trade_log.append(position)
                logging.info(f"Reversed {position['symbol']}. PnL: {pnl:.2f}")
            position = None

        if not position:
            option_type = None
            if row['low'] <= row['lower_band']: option_type = 'PE'
            elif row['high'] >= row['upper_band']: option_type = 'CE'

            if option_type:
                option_symbol, token = find_option_to_trade(row['close'], option_type, row['date'], instruments_df)
                if option_symbol:
                    entry_premium = get_option_price(token, row['date'], options_cache)
                    if entry_premium:
                        lot_size = instruments_df[instruments_df['instrument_token'] == token].iloc[0]['lot_size']
                        margin = (row['close'] * 0.15 + entry_premium) * lot_size # Simplified margin
                        position = {'entry_time': row['date'], 'symbol': option_symbol, 'type': 'PUT' if option_type == 'PE' else 'CALL', 'entry_premium': entry_premium, 'token': token, 'lot_size': lot_size, 'margin': margin}
                        logging.info(f"Sold {option_symbol} at {entry_premium}")

    return pd.DataFrame(trade_log)

if __name__ == "__main__":
    if "YOUR_API_KEY" in [API_KEY, API_SECRET, ACCESS_TOKEN]:
        logging.error("Please fill in your API credentials.")
    else:
        kite = KiteConnect(api_key=API_KEY)
        kite.set_access_token(ACCESS_TOKEN)

        try:
            index_token = get_instrument_token(kite, INDEX_SYMBOL, EXCHANGE_IND)
            nfo_instruments = pd.DataFrame(kite.instruments(exchange=EXCHANGE_OPT))
            nfo_instruments['expiry'] = pd.to_datetime(nfo_instruments['expiry']).dt.date

            from_date = datetime.now() - timedelta(days=59)
            to_date = datetime.now()

            index_data, options_cache = pre_fetch_all_data(kite, index_token, nfo_instruments, from_date, to_date)

            index_with_indicator = calculate_donchian_channel(index_data, DONCHIAN_PERIOD)

            trade_log = run_backtest(index_with_indicator, nfo_instruments, options_cache)

            if not trade_log.empty:
                trade_log.to_csv(TRADE_LOG_FILE, index=False)
                logging.info(f"Trade log saved to {TRADE_LOG_FILE}")
                print("\n--- Backtest Summary ---")
                print(trade_log)
                print(f"\nTotal PnL: {trade_log['pnl'].sum():.2f}")
            else:
                logging.info("No trades were executed during the backtest.")

        except Exception as e:
            logging.error(f"An error occurred: {e}", exc_info=True)