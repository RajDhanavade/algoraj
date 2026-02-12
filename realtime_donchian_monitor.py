import pandas as pd
import numpy as np
import yfinance as yf
import os
import time
from datetime import datetime
from tabulate import tabulate

# --- Configuration ---
TICKERS = ["RELIANCE.NS", "HDFCBANK.NS", "WIPRO.NS", "TCS.NS", "SBIN.NS", "BPCL.NS", "COALINDIA.NS"]
TIMEFRAME = '15m'
DONCHIAN_PERIOD = 20
INITIAL_CAPITAL_PER_STOCK = 100000
CAPITAL_ALLOCATION = [0.4, 0.3, 0.3]
SYSTEM_STOP_LOSS_PCT = 5.0
ENTRY_DROP_RISE_PCT = 1.0
LEVERAGE = 4.0
BROKERAGE_FEE_PCT = 0.05
SLIPPAGE_PCT = 0.02
POLL_INTERVAL_SECONDS = 60

class RealTimeDonchianStrategy:
    def __init__(self, tickers):
        self.tickers = tickers
        # State per ticker: {positions: list, position_type: str, last_price: float}
        self.portfolio = {ticker: {'positions': [], 'position_type': None, 'last_price': 0.0} for ticker in tickers}
        self.trade_log = []
        self.initial_capital_per_stock = INITIAL_CAPITAL_PER_STOCK

    def calculate_donchian_channel(self, data, period):
        """Calculates the Donchian Channel."""
        data['upper_band'] = data['High'].rolling(period).max()
        data['lower_band'] = data['Low'].rolling(period).min()
        return data

    def update(self):
        """Fetches latest data and updates strategy state for all tickers."""
        try:
            # period="5d" is more than enough for 20 periods of 15m
            data_all = yf.download(tickers=self.tickers, period="5d", interval=TIMEFRAME, group_by='ticker', progress=False)
        except Exception as e:
            print(f"Error downloading data: {e}")
            return

        for ticker in self.tickers:
            try:
                if isinstance(data_all.columns, pd.MultiIndex):
                    ticker_data = data_all[ticker].copy()
                else:
                    ticker_data = data_all.copy()
            except KeyError:
                continue

            if ticker_data.empty:
                continue

            ticker_data.dropna(subset=['Open', 'High', 'Low', 'Close'], inplace=True)
            if len(ticker_data) < DONCHIAN_PERIOD:
                continue

            ticker_data = self.calculate_donchian_channel(ticker_data, DONCHIAN_PERIOD)

            latest_row = ticker_data.iloc[-1]
            current_price = float(latest_row['Close'])
            timestamp = ticker_data.index[-1]

            state = self.portfolio[ticker]
            state['last_price'] = current_price

            # --- System-wide Stop-Loss Check ---
            if state['positions']:
                deployed_capital_at_cost = sum(p['cost_basis'] for p in state['positions'])
                current_value = 0
                if state['position_type'] == 'long':
                    current_value = sum(current_price * p['shares'] for p in state['positions'])
                else: # short
                    current_value = sum(p['cost_basis'] - (current_price - p['entry_price']) * p['shares'] for p in state['positions'])

                pnl = current_value - deployed_capital_at_cost
                if deployed_capital_at_cost > 0 and (pnl / deployed_capital_at_cost) * 100 <= -SYSTEM_STOP_LOSS_PCT:
                    print(f"[{ticker}] SYSTEM STOP-LOSS triggered. Closing all levels.")
                    self._close_all_positions(ticker, current_price, timestamp, f"System SL")
                    continue

            # --- Reversal Logic ---
            if state['position_type'] == 'short' and latest_row['Low'] <= latest_row['lower_band']:
                print(f"[{ticker}] REVERSAL signal from SHORT to LONG.")
                self._close_all_positions(ticker, latest_row['lower_band'], timestamp, "Reverse to Long")
                # Fall through to Entry Logic

            elif state['position_type'] == 'long' and latest_row['High'] >= latest_row['upper_band']:
                print(f"[{ticker}] REVERSAL signal from LONG to SHORT.")
                self._close_all_positions(ticker, latest_row['upper_band'], timestamp, "Reverse to Short")
                # Fall through to Entry Logic

            # --- Entry Logic ---
            if len(state['positions']) < 3:
                level = len(state['positions']) + 1

                if state['position_type'] is None:
                    if latest_row['Low'] <= latest_row['lower_band']:
                        state['position_type'] = 'long'
                    elif latest_row['High'] >= latest_row['upper_band']:
                        state['position_type'] = 'short'

                if state['position_type'] == 'long' and latest_row['Low'] <= latest_row['lower_band']:
                    can_enter = True
                    if level > 1:
                        last_entry_price = state['positions'][-1]['entry_price']
                        if not (current_price <= last_entry_price * (1 - ENTRY_DROP_RISE_PCT / 100)):
                            can_enter = False
                    if can_enter:
                        self._open_position(ticker, 'long', level, latest_row['lower_band'], timestamp)

                elif state['position_type'] == 'short' and latest_row['High'] >= latest_row['upper_band']:
                    can_enter = True
                    if level > 1:
                        last_entry_price = state['positions'][-1]['entry_price']
                        if not (current_price >= last_entry_price * (1 + ENTRY_DROP_RISE_PCT / 100)):
                            can_enter = False
                    if can_enter:
                        self._open_position(ticker, 'short', level, latest_row['upper_band'], timestamp)

    def _open_position(self, ticker, pos_type, level, price, timestamp):
        state = self.portfolio[ticker]
        capital_for_level = self.initial_capital_per_stock * CAPITAL_ALLOCATION[level - 1]
        leveraged_capital = capital_for_level * LEVERAGE

        if pos_type == 'long':
            entry_price = float(price * (1 + SLIPPAGE_PCT / 100))
            shares = int(leveraged_capital / entry_price)
            if shares == 0: return
            brokerage = entry_price * shares * (BROKERAGE_FEE_PCT / 100)
            cost_basis = (entry_price * shares) + brokerage
        else: # short
            entry_price = float(price * (1 - SLIPPAGE_PCT / 100))
            shares = int(leveraged_capital / entry_price)
            if shares == 0: return
            brokerage = entry_price * shares * (BROKERAGE_FEE_PCT / 100)
            cost_basis = (entry_price * shares) - brokerage

        new_pos = {
            'entry_time': timestamp,
            'entry_price': entry_price,
            'shares': shares,
            'level': level,
            'cost_basis': cost_basis
        }
        state['positions'].append(new_pos)
        print(f"{datetime.now().strftime('%H:%M:%S')} - [{ticker}] OPENED {pos_type.upper()} LEVEL {level} at {entry_price:.2f}, Shares: {shares}")

    def _close_all_positions(self, ticker, price, timestamp, reason):
        state = self.portfolio[ticker]
        pos_type = state['position_type']

        if pos_type is None:
            return

        for p in state['positions']:
            if pos_type == 'long':
                exit_price = float(price * (1 - SLIPPAGE_PCT / 100))
                brokerage = exit_price * p['shares'] * (BROKERAGE_FEE_PCT / 100)
                net_pnl = ((exit_price * p['shares']) - p['cost_basis'] - brokerage)
            else: # short
                exit_price = float(price * (1 + SLIPPAGE_PCT / 100))
                brokerage = exit_price * p['shares'] * (BROKERAGE_FEE_PCT / 100)
                net_pnl = (p['cost_basis'] - (exit_price * p['shares']) - brokerage)

            self.trade_log.append({
                'ticker': ticker,
                'entry_time': p['entry_time'],
                'entry_price': p['entry_price'],
                'position_type': pos_type,
                'exit_time': timestamp,
                'exit_price': exit_price,
                'pnl': net_pnl,
                'shares': p['shares'],
                'exit_reason': f"{reason} L{p['level']}"
            })
            print(f"{datetime.now().strftime('%H:%M:%S')} - [{ticker}] CLOSED {pos_type.upper()} LEVEL {p['level']} at {exit_price:.2f}, PnL: {net_pnl:.2f}")

        state['positions'] = []
        state['position_type'] = None

    def display_dashboard(self):
        """Displays a real-time dashboard of the portfolio and PnL."""
        table_data = []
        total_unrealized_pnl = 0
        total_realized_pnl = sum(t['pnl'] for t in self.trade_log)

        for ticker in self.tickers:
            state = self.portfolio[ticker]
            positions = state['positions']
            pos_type = state['position_type']
            current_price = state['last_price']

            if not positions:
                table_data.append([ticker, "FLAT", 0, 0, f"{current_price:.2f}", "0.00"])
                continue

            shares = sum(p['shares'] for p in positions)
            cost_basis_total = sum(p['cost_basis'] for p in positions)

            if pos_type == 'long':
                current_val = shares * current_price
            else:
                current_val = sum(p['cost_basis'] - (current_price - p['entry_price']) * p['shares'] for p in positions)

            unrealized_pnl = current_val - cost_basis_total
            total_unrealized_pnl += unrealized_pnl

            table_data.append([ticker, pos_type.upper(), len(positions), shares, f"{current_price:.2f}", f"{unrealized_pnl:.2f}"])

        # Try to clear screen for a clean dashboard look
        os.system('clear' if os.name == 'posix' else 'cls')
        print(f"--- Real-Time Donchian Monitor ({TIMEFRAME}) ---")
        print(f"Last Update: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(tabulate(table_data, headers=["Ticker", "Type", "Levels", "Shares", "Last Price", "Unrealized PnL"], tablefmt="grid"))

        print(f"\nTotal Unrealized PnL: {total_unrealized_pnl:.2f}")
        print(f"Total Realized PnL:   {total_realized_pnl:.2f}")
        print(f"Net Portfolio PnL:    {total_unrealized_pnl + total_realized_pnl:.2f}")
        print(f"Total Trades:         {len(self.trade_log)}")
        print("-" * 30)
        print("Press Ctrl+C to stop.")

def main():
    monitor = RealTimeDonchianStrategy(TICKERS)
    print("Starting Real-Time Monitor...")

    try:
        while True:
            monitor.update()
            monitor.display_dashboard()
            time.sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopping Monitor...")
        if monitor.trade_log:
            trade_df = pd.DataFrame(monitor.trade_log)
            trade_df.to_csv("realtime_trades.csv", index=False)
            print("Trade log saved to realtime_trades.csv")

if __name__ == "__main__":
    main()
