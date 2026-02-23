import pandas as pd
import numpy as np
import yfinance as yf
import pandas_ta as ta
from datetime import time, timedelta

def download_data(symbols, period="60d", interval="5m"):
    print(f"Downloading data for {symbols}...")
    data = {}
    for symbol in symbols:
        try:
            df = yf.download(symbol, period=period, interval=interval, progress=False)
            if not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                if df.index.tz is None:
                    df.index = df.index.tz_localize('UTC').tz_convert('Asia/Kolkata')
                else:
                    df.index = df.index.tz_convert('Asia/Kolkata')
                data[symbol] = df
            else:
                print(f"Warning: No data for {symbol}")
        except Exception as e:
            print(f"Error downloading {symbol}: {e}")
    return data

def calculate_vwap(df):
    # Group by date and calculate VWAP
    df['date'] = df.index.date
    v = df['Volume']
    tp = (df['High'] + df['Low'] + df['Close']) / 3
    df['VWAP'] = (tp * v).groupby(df['date']).cumsum() / v.groupby(df['date']).cumsum()
    return df

def get_pdh_pdl(df):
    # Calculate Previous Day High and Low using groupby date to avoid weekend gaps
    daily = df.groupby(df.index.date).agg({'High': 'max', 'Low': 'min', 'Close': 'last'})
    daily['PDH'] = daily['High'].shift(1)
    daily['PDL'] = daily['Low'].shift(1)
    daily['PDC'] = daily['Close'].shift(1)

    # Map back to 5m df
    df['PDH'] = df.index.date
    df['PDH'] = df['PDH'].map(daily['PDH'])
    df['PDL'] = df.index.date
    df['PDL'] = df['PDL'].map(daily['PDL'])
    df['PDC'] = df.index.date
    df['PDC'] = df['PDC'].map(daily['PDC'])
    return df

def run_liquidity_sweep_backtest(data_dict, nifty_df, symbols):
    trades = []

    # Prepare Nifty returns for Relative Strength
    nifty_df['nifty_ret'] = nifty_df['Close'].pct_change()
    nifty_df['date'] = nifty_df.index.date

    for symbol in symbols:
        if symbol not in data_dict: continue
        df = data_dict[symbol].copy()

        # Indicators
        df = calculate_vwap(df)
        df = get_pdh_pdl(df)
        df['RSI'] = ta.rsi(df['Close'], length=14)
        df['EMA9'] = ta.ema(df['Close'], length=9)
        df['VolAvg5'] = df['Volume'].shift(1).rolling(window=5).mean()

        # ADX on 15m resampled
        df_15 = df.resample('15min').agg({'High': 'max', 'Low': 'min', 'Close': 'last'}).dropna()
        if len(df_15) > 14:
            adx_15 = ta.adx(df_15['High'], df_15['Low'], df_15['Close'], length=14)
            if adx_15 is not None:
                df_15['ADX15'] = adx_15['ADX_14']
                df['ADX15'] = pd.Series(df.index.map(df_15['ADX15']), index=df.index).ffill()
            else:
                df['ADX15'] = np.nan
        else:
            df['ADX15'] = np.nan

        days = df['date'].unique()
        print(f"Analyzing {symbol} over {len(days)} days...")

        for day in days:
            day_data = df[df['date'] == day].copy()
            if len(day_data) < 20: continue

            nifty_day = nifty_df[nifty_df['date'] == day].copy()
            if nifty_day.empty: continue

            pdh = day_data['PDH'].iloc[0]
            pdl = day_data['PDL'].iloc[0]
            pdc = day_data['PDC'].iloc[0]

            if pd.isna(pdh) or pd.isna(pdl): continue

            # Filter 1: Open inside PD range
            open_price = day_data.iloc[0]['Open']
            if not (pdl <= open_price <= pdh):
                continue

            # Filter 2: Gap < 1%
            gap_pct = abs(open_price - pdc) / pdc * 100
            if gap_pct > 1.0:
                continue

            # Filter 3: First hour range > 0.7%
            first_hour = day_data.between_time('09:15', '10:15')
            if len(first_hour) < 12: continue
            fh_range_pct = (first_hour['High'].max() - first_hour['Low'].min()) / first_hour.iloc[0]['Open'] * 100
            if fh_range_pct <= 0.7:
                continue

            # Filter 4: ADX(15min) > 22 at end of first hour
            adx_val = first_hour.iloc[-1]['ADX15']
            if pd.isna(adx_val) or adx_val <= 22:
                continue

            # Strategy monitoring after 10:30
            # Need to track breakout and pullback
            post_1030 = day_data.between_time('10:30', '15:25')

            state = 'WAITING' # WAITING -> BROKEN -> PULLBACK -> ENTERED
            break_level = None # 'PDH' or 'PDL'
            pullback_extremity = None # Pullback low for PDH, pullback high for PDL

            active_trade = None

            for timestamp, row in post_1030.iterrows():
                # Check for "no breakout in first 5 mins" is implicitly handled by starting after 10:30
                # But we should also ensure it didn't break before 10:30
                if state == 'WAITING':
                    pre_1030 = day_data.between_time('09:15', '10:25')
                    if pre_1030['High'].max() > pdh or pre_1030['Low'].min() < pdl:
                        # User said "Break happens AFTER 10:30 AM" for the "Pro Upgrade"
                        # But also said "Direct breakout win rate ... Breakout + retest hold"
                        # I'll stick to the "Pro Upgrade" rule: Break happens after 10:30.
                        break # Skip this day

                    if row['Close'] > pdh:
                        state = 'BROKEN'
                        break_level = 'PDH'
                    elif row['Close'] < pdl:
                        state = 'BROKEN'
                        break_level = 'PDL'

                elif state == 'BROKEN':
                    if break_level == 'PDH':
                        if row['Low'] > pdh: # Holding above PDH
                            state = 'PULLBACK'
                            pullback_extremity = row['Low']
                        elif row['Close'] < pdl: # Failed completely and broke other side?
                            break # Skip
                    elif break_level == 'PDL':
                        if row['High'] < pdl: # Holding below PDL
                            state = 'PULLBACK'
                            pullback_extremity = row['High']
                        elif row['Close'] > pdh:
                            break

                elif state == 'PULLBACK':
                    # Looking for trigger: close above pullback high (for PDH)
                    # Pullback high is the max price reached during the 'BROKEN' phase?
                    # "Enter when 5-min close above pullback high"
                    # I need to track the peak reached after breakout.

                    # Let's refine state BROKEN to record the peak.
                    pass # Handled below by restructuring logic

            # Redoing the state machine logic more robustly
            state = 'WAITING'
            peak_after_break = None

            # Check if breakout happened before 10:30
            pre_1030 = day_data.between_time('09:15', '10:25')
            if (pre_1030['High'] > pdh).any() or (pre_1030['Low'] < pdl).any():
                continue

            for timestamp, row in post_1030.iterrows():
                if state == 'WAITING':
                    if row['Close'] > pdh:
                        state = 'BROKEN_PDH'
                        peak_after_break = row['High']
                    elif row['Close'] < pdl:
                        state = 'BROKEN_PDL'
                        peak_after_break = row['Low']

                elif state == 'BROKEN_PDH':
                    if row['Close'] < pdh: # Pullback inside?
                        # "Wait for pullback... Enter ONLY if pullback holds above PDH"
                        # This means the pullback should stay above PDH.
                        state = 'WAITING' # Reset if it dips back below PDH?
                        # Actually "pullback holds above PDH" usually means it stays above.
                        continue

                    if row['Low'] > pdh: # We have some air
                        state = 'RETEST_PDH'
                        pullback_low = row['Low']
                        pullback_high = row['High']

                    peak_after_break = max(peak_after_break, row['High'])

                elif state == 'RETEST_PDH':
                    # Entry Conditions:
                    # 5-min close above pullback high
                    # Volume > previous 5 candle avg
                    # Price above VWAP
                    # RSI > 55
                    # Stock relative strength > Nifty

                    pullback_low = min(pullback_low, row['Low'])

                    # Relative Strength check
                    stock_cum_ret = (row['Close'] - open_price) / open_price
                    nifty_row = nifty_day[nifty_day.index <= timestamp].iloc[-1]
                    nifty_open = nifty_day.iloc[0]['Open']
                    nifty_cum_ret = (nifty_row['Close'] - nifty_open) / nifty_open
                    rs_ok = stock_cum_ret > nifty_cum_ret

                    if row['Close'] > peak_after_break:
                        v_ok = row['Volume'] > row['VolAvg5']
                        vw_ok = row['Close'] > row['VWAP']
                        r_ok = row['RSI'] > 55

                        if v_ok and vw_ok and r_ok and rs_ok:
                            entry_price = row['Close']
                            sl = pullback_low
                            risk = entry_price - sl
                            if risk <= 0: risk = entry_price * 0.005 # Fallback

                            active_trade = {
                                'symbol': symbol,
                                'type': 'BUY',
                                'entry_time': timestamp,
                                'entry_price': entry_price,
                                'sl': sl,
                                'tp': entry_price + 2 * risk # 2R target
                            }
                            state = 'IN_TRADE'

                    peak_after_break = max(peak_after_break, row['High'])
                    if row['Close'] < pdh: state = 'WAITING' # Failed retest

                elif state == 'BROKEN_PDL':
                    if row['High'] < pdl:
                        state = 'RETEST_PDL'
                        pullback_high = row['High']
                        pullback_low = row['Low']

                    peak_after_break = min(peak_after_break, row['Low'])
                    if row['Close'] > pdl: state = 'WAITING'

                elif state == 'RETEST_PDL':
                    pullback_high = max(pullback_high, row['High'])

                    stock_cum_ret = (row['Close'] - open_price) / open_price
                    nifty_row = nifty_day[nifty_day.index <= timestamp].iloc[-1]
                    nifty_open = nifty_day.iloc[0]['Open']
                    nifty_cum_ret = (nifty_row['Close'] - nifty_open) / nifty_open
                    rs_ok = stock_cum_ret < nifty_cum_ret # Stock weaker than Nifty

                    if row['Close'] < peak_after_break:
                        v_ok = row['Volume'] > row['VolAvg5']
                        vw_ok = row['Close'] < row['VWAP']
                        r_ok = row['RSI'] < 45

                        if v_ok and vw_ok and r_ok and rs_ok:
                            entry_price = row['Close']
                            sl = pullback_high
                            risk = sl - entry_price
                            if risk <= 0: risk = entry_price * 0.005

                            active_trade = {
                                'symbol': symbol,
                                'type': 'SELL',
                                'entry_time': timestamp,
                                'entry_price': entry_price,
                                'sl': sl,
                                'tp': entry_price - 2 * risk
                            }
                            state = 'IN_TRADE'

                    peak_after_break = min(peak_after_break, row['Low'])
                    if row['Close'] > pdl: state = 'WAITING'

                elif state == 'IN_TRADE':
                    # Manage
                    if active_trade['type'] == 'BUY':
                        if row['Low'] <= active_trade['sl']:
                            trades.append({**active_trade, 'exit_time': timestamp, 'exit_price': active_trade['sl'], 'reason': 'SL'})
                            active_trade = None
                            break
                        elif row['High'] >= active_trade['tp']:
                            trades.append({**active_trade, 'exit_time': timestamp, 'exit_price': active_trade['tp'], 'reason': 'TP'})
                            active_trade = None
                            break
                        elif timestamp.time() >= time(15, 25):
                            trades.append({**active_trade, 'exit_time': timestamp, 'exit_price': row['Close'], 'reason': 'EOD'})
                            active_trade = None
                            break
                    else:
                        if row['High'] >= active_trade['sl']:
                            trades.append({**active_trade, 'exit_time': timestamp, 'exit_price': active_trade['sl'], 'reason': 'SL'})
                            active_trade = None
                            break
                        elif row['Low'] <= active_trade['tp']:
                            trades.append({**active_trade, 'exit_time': timestamp, 'exit_price': active_trade['tp'], 'reason': 'TP'})
                            active_trade = None
                            break
                        elif timestamp.time() >= time(15, 25):
                            trades.append({**active_trade, 'exit_time': timestamp, 'exit_price': row['Close'], 'reason': 'EOD'})
                            active_trade = None
                            break

            if active_trade:
                trades.append({**active_trade, 'exit_time': day_data.index[-1], 'exit_price': day_data.iloc[-1]['Close'], 'reason': 'EOD_FINAL'})
                active_trade = None

    return pd.DataFrame(trades)

if __name__ == "__main__":
    symbols = ["RELIANCE.NS", "HDFCBANK.NS", "ICICIBANK.NS"]
    nifty_symbol = "^NSEI"

    data = download_data(symbols + [nifty_symbol])
    nifty_df = data.pop(nifty_symbol)

    trade_log = run_liquidity_sweep_backtest(data, nifty_df, symbols)

    if not trade_log.empty:
        trade_log['pnl_pct'] = np.where(trade_log['type'] == 'BUY',
                                       (trade_log['exit_price'] - trade_log['entry_price']) / trade_log['entry_price'] * 100,
                                       (trade_log['entry_price'] - trade_log['exit_price']) / trade_log['entry_price'] * 100)

        print("\n" + "="*50)
        print("LIQUIDITY SWEEP MODEL BACKTEST REPORT")
        print("="*50)

        display_log = trade_log.copy()
        display_log['entry_time'] = display_log['entry_time'].dt.strftime('%Y-%m-%d %H:%M')
        display_log['exit_time'] = display_log['exit_time'].dt.strftime('%Y-%m-%d %H:%M')
        print(display_log[['symbol', 'type', 'entry_time', 'entry_price', 'exit_time', 'exit_price', 'pnl_pct', 'reason']].to_string(index=False))

        print("\n--- Performance Summary ---")
        total_trades = len(trade_log)
        wins = (trade_log['pnl_pct'] > 0).sum()
        win_rate = (wins / total_trades) * 100
        total_pnl = trade_log['pnl_pct'].sum()

        print(f"Total Trades: {total_trades}")
        print(f"Win Rate: {win_rate:.2f}%")
        print(f"Total PnL: {total_pnl:.2f}%")
        loss_sum = abs(trade_log[trade_log['pnl_pct']<=0]['pnl_pct'].sum())
        profit_factor = (trade_log[trade_log['pnl_pct']>0]['pnl_pct'].sum() / loss_sum) if loss_sum != 0 else float('inf')
        print(f"Profit Factor: {profit_factor:.2f}")
        print("="*50)
    else:
        print("No trades found.")
