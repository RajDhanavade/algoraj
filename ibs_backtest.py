import yfinance as yf
import pandas as pd

# --- Configuration ---
# Step 1: Setup Nifty 50 Stock Symbols
nifty_50_symbols = [
    "ADANIENT.NS", "ADANIPORTS.NS", "APOLLOHOSP.NS", "ASIANPAINT.NS", "AXISBANK.NS",
    "BAJAJ-AUTO.NS", "BAJFINANCE.NS", "BAJAJFINSV.NS", "BPCL.NS", "BHARTIARTL.NS",
    "BRITANNIA.NS", "CIPLA.NS", "COALINDIA.NS", "DIVISLAB.NS", "DRREDDY.NS",
    "EICHERMOT.NS", "GRASIM.NS", "HCLTECH.NS", "HDFCBANK.NS", "HDFCLIFE.NS",
    "HEROMOTOCO.NS", "HINDALCO.NS", "HINDUNILVR.NS", "ICICIBANK.NS", "ITC.NS",
    "INDUSINDBK.NS", "INFY.NS", "JSWSTEEL.NS", "KOTAKBANK.NS", "LTIM.NS",
    "LT.NS", "M&M.NS", "MARUTI.NS", "NTPC.NS", "NESTLEIND.NS", "ONGC.NS",

    "POWERGRID.NS", "RELIANCE.NS", "SBILIFE.NS", "SHRIRAMFIN.NS", "SBIN.NS",
    "SUNPHARMA.NS", "TCS.NS", "TATACONSUM.NS", "TATAMOTORS.NS", "TATASTEEL.NS",
    "TECHM.NS", "TITAN.NS", "ULTRACEMCO.NS", "WIPRO.NS"
]

# Step 2: Download Stock Data
def download_data(symbols):
    """
    Downloads one month of 5-minute intraday data for the given symbols.
    """
    data = yf.download(tickers=symbols, period="1mo", interval="5m", group_by='ticker')
    return data

# Step 3: Calculate Camarilla Pivot Points
def calculate_camarilla_pivots(previous_day_data):
    """
    Calculates Camarilla pivot points for the given stock data.
    """
    high = previous_day_data['High']
    low = previous_day_data['Low']
    close = previous_day_data['Close']

    pivot = (high + low + close) / 3
    range_ = high - low

    pivots = {
        'R5': (high / low) * close,
        'R4': close + (range_ * 1.1 / 2),
        'R3': close + (range_ * 1.1 / 4),
        'R2': close + (range_ * 1.1 / 6),
        'R1': close + (range_ * 1.1 / 12),
        'S1': close - (range_ * 1.1 / 12),
        'S2': close - (range_ * 1.1 / 6),
        'S3': close - (range_ * 1.1 / 4),
        'S4': close - (range_ * 1.1 / 2),
    }
    pivots['S5'] = close - (pivots['R5'] - close)
    return pivots

# Step 4: Run the Backtest
def run_backtest(data, symbols):
    """
    Runs the backtest for the Camarilla breakout strategy.
    """
    trade_log = []

    unique_days = data.index.normalize().unique()

    for i in range(1, len(unique_days)):
        previous_day = unique_days[i-1]
        current_day = unique_days[i]

        for symbol in symbols:
            if symbol not in data.columns.get_level_values(0):
                continue

            symbol_data = data[symbol].dropna()

            previous_day_data = symbol_data.loc[symbol_data.index.normalize() == previous_day]
            if previous_day_data.empty:
                continue

            prev_day_high = previous_day_data['High'].max()
            prev_day_low = previous_day_data['Low'].min()
            prev_day_close = previous_day_data['Close'].iloc[-1]

            pivots = calculate_camarilla_pivots({
                'High': prev_day_high,
                'Low': prev_day_low,
                'Close': prev_day_close
            })
            r5 = pivots['R5']
            s5 = pivots['S5']

            current_day_data = symbol_data.loc[symbol_data.index.normalize() == current_day]
            if current_day_data.empty:
                continue

            entry_price = 0
            trade_type = None

            for _, row in current_day_data.iterrows():
                if row['High'] > r5:
                    trade_type = 'long'
                    entry_price = r5
                    break

                if row['Low'] < s5:
                    trade_type = 'short'
                    entry_price = s5
                    break

            if trade_type:
                exit_price = current_day_data['Close'].iloc[-1]

                if trade_type == 'long':
                    pnl = exit_price - entry_price
                else:
                    pnl = entry_price - exit_price

                trade_log.append({
                    'Symbol': symbol,
                    'Date': current_day.date(),
                    'Trade Type': trade_type,
                    'Entry Price': entry_price,
                    'Exit Price': exit_price,
                    'PnL': pnl,
                    'ROI': (pnl / entry_price) * 100
                })

    return pd.DataFrame(trade_log)

# Step 5: Calculate and Display Performance
def calculate_and_display_performance(trade_log):
    """
    Calculates and displays the performance of the backtest.
    """
    if trade_log.empty:
        print("No trades were made during the backtest period.")
        return

    total_trades = len(trade_log)
    winning_trades = trade_log[trade_log['PnL'] > 0]
    losing_trades = trade_log[trade_log['PnL'] <= 0]

    win_rate = (len(winning_trades) / total_trades) * 100 if total_trades > 0 else 0
    total_pnl = trade_log['PnL'].sum()
    total_roi = trade_log['ROI'].sum()

    print("\n--- Trade Summary ---")
    print(f"Total Trades: {total_trades}")
    print(f"Winning Trades: {len(winning_trades)}")
    print(f"Losing Trades: {len(losing_trades)}")
    print(f"Win-Loss Ratio: {win_rate:.2f}%")
    print(f"Total PnL: {total_pnl:.2f}")
    print(f"Monthly ROI: {total_roi:.2f}%")
    print("---------------------\n")

    print("Trade Log:")
    print(trade_log)

# Step 6: Execute the Backtest
if __name__ == "__main__":
    print("Starting the backtest...")

    # Download data for all Nifty 50 stocks
    nifty_data = download_data(nifty_50_symbols)

    if not nifty_data.empty:
        # Run the backtest
        trade_log = run_backtest(nifty_data, nifty_50_symbols)

        # Calculate and display performance
        calculate_and_display_performance(trade_log)
    else:
        print("Failed to download data. The backtest cannot proceed.")

    print("Backtest finished.")