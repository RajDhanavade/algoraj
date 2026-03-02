import pandas as pd
import numpy as np
import yfinance as yf
from datetime import time
import os
import pickle

# --- Configuration ---
SYMBOLS = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL", "CIPLA",
    "COALINDIA", "DRREDDY", "EICHERMOT", "GRASIM", "HCLTECH",
    "HDFCBANK", "HDFCLIFE", "HEROMOTOCO", "HINDALCO", "HINDUNILVR",
    "ICICIBANK", "INDUSINDBK", "INFY", "ITC", "JIOFIN", "JSWSTEEL",
    "KOTAKBANK", "LT", "M&M", "MARUTI", "NESTLEIND", "NTPC", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SHRIRAMFIN", "SBIN", "SUNPHARMA",
    "TCS", "TATACONSUM", "TATASTEEL", "TECHM", "TITAN",
    "TRENT", "ULTRACEMCO", "WIPRO"
]

# Indian Market Timings
MARKET_OPEN = time(9, 15)
MARKET_CLOSE_EXIT = time(15, 25)
FIRST_CANDLE_END = time(9, 20)

# Filters (Adapted for Indian Market)
MIN_PRICE = 100
MIN_AVG_VOLUME = 1_000_000
MIN_ATR = 1.0
TOP_N_STOCKS = 5
INITIAL_CAPITAL = 500_000
ALLOCATION_PER_TRADE = 100_000

DATA_FILE = 'orb_data.pkl'

def download_data(symbols):
    print("Downloading historical data...")
    daily_data_frames = []
    intraday_data_frames = []

    for symbol in symbols:
        ticker = symbol + ".NS"
        print(f"Downloading {ticker}...")

        # Daily data
        d_df = yf.download(ticker, period="6mo", interval="1d", progress=False)
        if not d_df.empty:
            d_df.columns = pd.MultiIndex.from_product([[ticker], d_df.columns])
            daily_data_frames.append(d_df)

        # Intraday data
        i_df = yf.download(ticker, period="60d", interval="5m", progress=False)
        if not i_df.empty:
            i_df.columns = pd.MultiIndex.from_product([[ticker], i_df.columns])
            intraday_data_frames.append(i_df)

    daily_data = pd.concat(daily_data_frames, axis=1)
    intraday_data = pd.concat(intraday_data_frames, axis=1)

    # Localize to IST
    if intraday_data.index.tz is None:
        intraday_data.index = intraday_data.index.tz_localize('UTC').tz_convert('Asia/Kolkata')
    else:
        intraday_data.index = intraday_data.index.tz_convert('Asia/Kolkata')

    data = {'daily': daily_data, 'intraday': intraday_data}
    with open(DATA_FILE, 'wb') as f:
        pickle.dump(data, f)
    print(f"Data saved to {DATA_FILE}")
    return data

def load_cached_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'rb') as f:
            return pickle.load(f)
    return download_data(SYMBOLS)

def calculate_daily_metrics(daily_df):
    metrics = {}
    for ticker in daily_df.columns.get_level_values(0).unique():
        df = daily_df[ticker].copy()
        # Ensure data is 1D and has enough points
        if len(df) < 15: continue

        # Extract values correctly from MultiIndex if necessary
        high = df['High'].values.flatten().astype(float)
        low = df['Low'].values.flatten().astype(float)
        close = df['Close'].values.flatten().astype(float)
        volume = df['Volume'].values.flatten().astype(float)

        # Basic True Range calculation manually to avoid pandas-ta issue if it persists
        tr = np.maximum((high[1:] - low[1:]),
                        np.maximum(np.abs(high[1:] - close[:-1]),
                                   np.abs(low[1:] - close[:-1])))
        tr = np.insert(tr, 0, high[0] - low[0])

        atr = pd.Series(tr).rolling(window=14).mean()

        df['ATR'] = atr.values
        df['AvgVol'] = pd.Series(volume).rolling(window=14).mean().values
        metrics[ticker] = df[['Close', 'ATR', 'AvgVol']]
    return metrics

def calculate_relative_volume(intraday_df, ticker, current_date):
    # Filter for first 5-min candles of the last 14 trading days
    # This is a bit complex with MultiIndex, easier to handle per ticker
    df = intraday_df[ticker].copy()
    first_candles = df[df.index.time == MARKET_OPEN]

    # Get previous 14 instances before current_date
    past_first_candles = first_candles[first_candles.index.date < current_date].tail(14)

    if len(past_first_candles) < 14:
        return 0

    avg_first_5m_vol = past_first_candles['Volume'].mean()
    current_first_5m_vol = first_candles[first_candles.index.date == current_date]['Volume'].iloc[0] if not first_candles[first_candles.index.date == current_date].empty else 0

    if avg_first_5m_vol == 0:
        return 0

    return current_first_5m_vol / avg_first_5m_vol

def run_backtest(data):
    daily_metrics = calculate_daily_metrics(data['daily'])
    intraday_df = data['intraday']

    unique_dates = np.unique(intraday_df.index.date)
    all_dates = sorted(unique_dates)[15:] # Start after we have enough for RV
    trade_log = []

    for current_date in all_dates:
        print(f"Processing {current_date}...")
        daily_candidates = []

        for ticker in SYMBOLS:
            t_ns = ticker + ".NS"
            m = daily_metrics[t_ns]

            # Use data from previous day for filtering
            past_data = m[m.index.date < current_date].tail(1)
            if past_data.empty: continue

            prev_close = past_data['Close'].iloc[0]
            prev_atr = past_data['ATR'].iloc[0]
            prev_avg_vol = past_data['AvgVol'].iloc[0]

            if prev_close > MIN_PRICE and prev_avg_vol > MIN_AVG_VOLUME and prev_atr > MIN_ATR:
                rv = calculate_relative_volume(intraday_df, t_ns, current_date)
                if rv >= 1.0:
                    daily_candidates.append({'ticker': t_ns, 'rv': rv, 'atr': prev_atr})

        # Sort by RV and take top 20
        daily_candidates = sorted(daily_candidates, key=lambda x: x['rv'], reverse=True)[:TOP_N_STOCKS]

        # Execute Trades
        for candidate in daily_candidates:
            ticker = candidate['ticker']
            atr_14 = candidate['atr']

            day_data = intraday_df[ticker][intraday_df.index.date == current_date]
            if day_data.empty: continue

            first_candle = day_data[day_data.index.time == MARKET_OPEN]
            if first_candle.empty: continue

            open_p = first_candle['Open'].iloc[0]
            close_p = first_candle['Close'].iloc[0]
            high_range = first_candle['High'].iloc[0]
            low_range = first_candle['Low'].iloc[0]

            # Directional bias
            side = 'LONG' if close_p > open_p else 'SHORT'
            entry_price = high_range if side == 'LONG' else low_range
            stop_loss = entry_price - (0.1 * atr_14) if side == 'LONG' else entry_price + (0.1 * atr_14)

            # Check for breakout after 9:20
            remaining_day = day_data[day_data.index.time > MARKET_OPEN]

            trade_entered = False
            exit_reason = 'EOD'
            exit_price = None

            for timestamp, row in remaining_day.iterrows():
                if not trade_entered:
                    if side == 'LONG' and row['High'] > entry_price:
                        trade_entered = True
                        # Assume entry at high_range (ORB break)
                    elif side == 'SHORT' and row['Low'] < entry_price:
                        trade_entered = True

                if trade_entered:
                    # Check SL
                    if side == 'LONG' and row['Low'] < stop_loss:
                        exit_price = stop_loss
                        exit_reason = 'SL'
                        break
                    elif side == 'SHORT' and row['High'] > stop_loss:
                        exit_price = stop_loss
                        exit_reason = 'SL'
                        break

                    # Exit at EOD
                    if timestamp.time() >= MARKET_CLOSE_EXIT:
                        exit_price = row['Close']
                        exit_reason = 'EOD'
                        break

            if trade_entered:
                pnl_pct = (exit_price - entry_price) / entry_price if side == 'LONG' else (entry_price - exit_price) / entry_price
                pnl_value = pnl_pct * ALLOCATION_PER_TRADE
                trade_log.append({
                    'date': current_date,
                    'ticker': ticker,
                    'side': side,
                    'entry': entry_price,
                    'exit': exit_price,
                    'pnl_pct': pnl_pct,
                    'pnl_val': pnl_value,
                    'reason': exit_reason
                })

    return pd.DataFrame(trade_log)

def analyze_results(trades):
    if trades.empty:
        print("No trades executed.")
        return

    print("\n--- Backtest Results ---")
    print(f"Initial Capital: ₹{INITIAL_CAPITAL}")
    print(f"Allocation per Trade: ₹{ALLOCATION_PER_TRADE}")
    print(f"Top Quality Trades per Day: {TOP_N_STOCKS}")

    total_trades = len(trades)
    win_rate = (trades['pnl_pct'] > 0).sum() / total_trades * 100
    total_pnl_val = trades['pnl_val'].sum()
    roi = (total_pnl_val / INITIAL_CAPITAL) * 100

    # Daily PnL value sum
    daily_pnl_val = trades.groupby('date')['pnl_val'].sum()
    # Daily return on total capital
    daily_returns = daily_pnl_val / INITIAL_CAPITAL
    cumulative_returns = (1 + daily_returns).cumprod()

    sharpe = (daily_returns.mean() / daily_returns.std()) * np.sqrt(252) if daily_returns.std() != 0 else 0

    max_drawdown = (cumulative_returns / cumulative_returns.cummax() - 1).min() * 100

    print(f"Total Trades: {total_trades}")
    print(f"Win Rate: {win_rate:.2f}%")
    print(f"Total Profit/Loss: ₹{total_pnl_val:.2f}")
    print(f"Total ROI: {roi:.2f}%")
    print(f"Sharpe Ratio: {sharpe:.2f}")
    print(f"Max Drawdown: {max_drawdown:.2f}%")

    print("\n--- Best Trades ---")
    print(trades.sort_values(by='pnl_pct', ascending=False).head(5))

    print("\n--- Worst Trades ---")
    print(trades.sort_values(by='pnl_pct', ascending=True).head(5))

if __name__ == "__main__":
    data = load_cached_data()
    trades = run_backtest(data)
    analyze_results(trades)
