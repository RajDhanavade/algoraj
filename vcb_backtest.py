import pandas as pd
import numpy as np
import yfinance as yf
import pandas_ta as ta
from datetime import time

def download_data(symbols, period="60d", interval="5m"):
    print(f"Downloading data for {symbols}...")
    data = {}
    for symbol in symbols:
        try:
            df = yf.download(symbol, period=period, interval=interval, progress=False)
            if not df.empty:
                data[symbol] = df
            else:
                print(f"Warning: No data for {symbol}")
        except Exception as e:
            print(f"Error downloading {symbol}: {e}")
    return data

def run_vcb_backtest(data_dict, symbols):
    trades = []

    for symbol in symbols:
        if symbol not in data_dict:
            continue
        df = data_dict[symbol].copy()
        # If columns are MultiIndex, flatten them
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df.dropna(inplace=True)

        # Convert index to Asia/Kolkata
        if df.index.tz is None:
            df.index = df.index.tz_localize('UTC').tz_convert('Asia/Kolkata')
        else:
            df.index = df.index.tz_convert('Asia/Kolkata')

        # Calculate Indicators
        if len(df) < 30:
            print(f"Skipping {symbol} due to insufficient data.")
            continue

        df['EMA20'] = ta.ema(df['Close'], length=20)
        df['EMA9'] = ta.ema(df['Close'], length=9)
        df['RSI'] = ta.rsi(df['Close'], length=14)
        df['ATR'] = ta.atr(df['High'], df['Low'], df['Close'], length=14)

        try:
            adx = ta.adx(df['High'], df['Low'], df['Close'], length=14)
            if adx is not None:
                df['ADX'] = adx['ADX_14']
            else:
                df['ADX'] = np.nan
        except Exception as e:
            print(f"ADX calculation failed for {symbol}: {e}")
            df['ADX'] = np.nan

        df['VolAvg10'] = df['Volume'].shift(1).rolling(window=10).mean()

        df['date'] = df.index.date
        days = df['date'].unique()

        print(f"Analyzing {symbol} over {len(days)} days...")

        for day in days:
            day_data = df[df['date'] == day].copy()
            if len(day_data) < 10: continue

            # Setup window: 9:15 to 9:45 (First 6 candles of 5m)
            # 9:15, 9:20, 9:25, 9:30, 9:35, 9:40 -> 9:45 is the close of the 6th candle
            setup_df = day_data.between_time('09:15', '09:40')
            if len(setup_df) < 6: continue

            # 30-min range
            range_30 = setup_df['High'].max() - setup_df['Low'].min()
            high_30 = setup_df['High'].max()
            low_30 = setup_df['Low'].min()

            close_940 = setup_df.iloc[-1]['Close']
            range_pct = (range_30 / close_940) * 100

            # ADX at 9:40
            adx_val = setup_df.iloc[-1]['ADX']
            if isinstance(adx_val, pd.Series):
                adx_940 = adx_val.iloc[0]
            else:
                adx_940 = adx_val

            if pd.isna(adx_940):
                continue

            # Volume declining
            # Simple check: slope of volume
            y = setup_df['Volume'].values
            if len(y) < 2: continue
            x = np.arange(len(y))
            slope = np.polyfit(x, y, 1)[0]
            vol_declining = slope < 0

            # Price near EMA20
            ema20_940 = setup_df.iloc[-1]['EMA20']
            near_ema = abs(close_940 - ema20_940) / ema20_940 < 0.002

            # Setup conditions check
            setup_ok = range_pct < 0.6 and adx_940 < 20 and vol_declining and near_ema

            if not setup_ok:
                continue

            # print(f"{day} {symbol} Setup OK!")

            # Monitor for breakout after 9:45
            post_setup_df = day_data.between_time('09:45', '15:00')

            active_trade = None

            for timestamp, row in post_setup_df.iterrows():
                if active_trade is None:
                    # Look for Entry
                    # Buy
                    if row['Close'] > high_30 and row['Volume'] > 1.5 * row['VolAvg10'] and row['RSI'] > 55:
                        sl_dist = max(row['Close'] - row['Low'], 0.8 * row['ATR'])
                        active_trade = {
                            'symbol': symbol,
                            'type': 'BUY',
                            'entry_price': row['Close'],
                            'entry_time': timestamp,
                            'sl': row['Close'] - sl_dist,
                            'tp1': row['Close'] + sl_dist,
                            'tp1_hit': False
                        }
                    # Sell
                    elif row['Close'] < low_30 and row['Volume'] > 1.5 * row['VolAvg10'] and row['RSI'] < 45:
                        sl_dist = max(row['High'] - row['Close'], 0.8 * row['ATR'])
                        active_trade = {
                            'symbol': symbol,
                            'type': 'SELL',
                            'entry_price': row['Close'],
                            'entry_time': timestamp,
                            'sl': row['Close'] + sl_dist,
                            'tp1': row['Close'] - sl_dist,
                            'tp1_hit': False
                        }
                else:
                    # Manage trade
                    curr_close = row['Close']

                    if active_trade['type'] == 'BUY':
                        # Check TP1
                        if not active_trade['tp1_hit'] and row['High'] >= active_trade['tp1']:
                            active_trade['tp1_hit'] = True

                        # Exit conditions
                        exit_reason = None
                        exit_price = None

                        if row['Low'] <= active_trade['sl']:
                            exit_reason = 'SL'
                            exit_price = active_trade['sl']
                        elif active_trade['tp1_hit'] and curr_close < row['EMA9']:
                            exit_reason = 'Trail'
                            exit_price = curr_close
                        elif timestamp.time() >= time(15, 0):
                            exit_reason = 'EOD'
                            exit_price = curr_close

                        if exit_reason:
                            pnl = (exit_price - active_trade['entry_price']) / active_trade['entry_price']
                            trades.append({
                                'symbol': symbol,
                                'type': 'BUY',
                                'entry_time': active_trade['entry_time'],
                                'entry_price': active_trade['entry_price'],
                                'exit_time': timestamp,
                                'exit_price': exit_price,
                                'pnl_pct': pnl * 100,
                                'reason': exit_reason
                            })
                            active_trade = None
                            break # One trade per day per stock

                    elif active_trade['type'] == 'SELL':
                        # Check TP1
                        if not active_trade['tp1_hit'] and row['Low'] <= active_trade['tp1']:
                            active_trade['tp1_hit'] = True

                        # Exit conditions
                        exit_reason = None
                        exit_price = None

                        if row['High'] >= active_trade['sl']:
                            exit_reason = 'SL'
                            exit_price = active_trade['sl']
                        elif active_trade['tp1_hit'] and curr_close > row['EMA9']:
                            exit_reason = 'Trail'
                            exit_price = curr_close
                        elif timestamp.time() >= time(15, 0):
                            exit_reason = 'EOD'
                            exit_price = curr_close

                        if exit_reason:
                            pnl = (active_trade['entry_price'] - exit_price) / active_trade['entry_price']
                            trades.append({
                                'symbol': symbol,
                                'type': 'SELL',
                                'entry_time': active_trade['entry_time'],
                                'entry_price': active_trade['entry_price'],
                                'exit_time': timestamp,
                                'exit_price': exit_price,
                                'pnl_pct': pnl * 100,
                                'reason': exit_reason
                            })
                            active_trade = None
                            break # One trade per day per stock

    return pd.DataFrame(trades)

if __name__ == "__main__":
    symbols = ["RELIANCE.NS", "HDFCBANK.NS", "ICICIBANK.NS", "TCS.NS"]
    data = download_data(symbols)

    trade_log = run_vcb_backtest(data, symbols)

    if not trade_log.empty:
        print("\n" + "="*50)
        print("VOLATILITY COMPRESSION BREAKOUT (VCB) BACKTEST REPORT")
        print("="*50)
        print(f"Symbols: {symbols}")
        print(f"Period: Last 60 days (5m timeframe)")
        print("-" * 50)

        print("\n--- Trade Log ---")
        display_log = trade_log.copy()
        display_log['entry_time'] = display_log['entry_time'].dt.strftime('%Y-%m-%d %H:%M')
        display_log['exit_time'] = display_log['exit_time'].dt.strftime('%Y-%m-%d %H:%M')
        print(display_log.to_string(index=False))

        print("\n--- Performance Summary ---")
        total_trades = len(trade_log)
        wins = (trade_log['pnl_pct'] > 0).sum()
        losses = total_trades - wins
        win_rate = (wins / total_trades) * 100
        total_pnl = trade_log['pnl_pct'].sum()
        avg_pnl = trade_log['pnl_pct'].mean()

        profitable_trades = trade_log[trade_log['pnl_pct'] > 0]
        losing_trades = trade_log[trade_log['pnl_pct'] <= 0]

        avg_win = profitable_trades['pnl_pct'].mean() if not profitable_trades.empty else 0
        avg_loss = losing_trades['pnl_pct'].mean() if not losing_trades.empty else 0
        profit_factor = abs(profitable_trades['pnl_pct'].sum() / losing_trades['pnl_pct'].sum()) if not losing_trades.empty else float('inf')

        print(f"Total Trades: {total_trades}")
        print(f"Wins: {wins} | Losses: {losses}")
        print(f"Win Rate: {win_rate:.2f}%")
        print(f"Total PnL: {total_pnl:.2f}%")
        print(f"Average PnL per trade: {avg_pnl:.2f}%")
        print(f"Average Win: {avg_win:.2f}%")
        print(f"Average Loss: {avg_loss:.2f}%")
        print(f"Profit Factor: {profit_factor:.2f}")
        print(f"Max Win: {trade_log['pnl_pct'].max():.2f}%")
        print(f"Max Loss: {trade_log['pnl_pct'].min():.2f}%")
        print("="*50)
    else:
        print("No trades found matching the setup conditions.")
