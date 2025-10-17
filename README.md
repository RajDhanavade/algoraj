# Zerodha Options Backtester

This project contains a backtesting script (`options_backtester.py`) for an options selling strategy on the BANKNIFTY index, using the Zerodha Kite API for historical data.

## Features

- **Strategy:** Sells In-the-Money (ITM) options based on Donchian Channel signals on the BANKNIFTY index.
  - Sells an ITM Put when the index hits the lower Donchian band.
  - Sells an ITM Call when the index hits the upper Donchian band (reversal).
- **Automated Authentication:** The script includes a built-in, semi-automated process to generate your daily access token.
- **Data Source:** Uses the Zerodha Kite API for all historical data to ensure consistency.
- **Risk Management:** Includes a simplified stop-loss mechanism based on a percentage of the estimated margin.

## Setup Instructions

### Step 1: Install Dependencies

Install the necessary Python libraries by running this command in your terminal:

```bash
pip install -r requirements.txt
```

### Step 2: Get Zerodha API Credentials

1.  Go to the [Zerodha Kite Connect developer portal](https://developers.kite.trade/).
2.  Create a new app.
3.  Fill in the form:
    - **App name:** `OptionsBacktester` (or any name you prefer)
    - **Client ID:** Your Zerodha client ID.
    - **Redirect URL:** `http://127.0.0.1:5000/redirect` (Use this exact URL)
    - **Description:** A short description, e.g., "Options backtesting script".
4.  After creating the app, you will get your `api_key` and `api_secret`.

### Step 3: Configure the Script

Open `options_backtester.py` and fill in your credentials:

-   `API_KEY`: Your key from the Zerodha app.
-   `API_SECRET`: Your secret from the Zerodha app.

### Step 4: Run the Backtester

1.  Open your terminal and run the script:
    ```bash
    python options_backtester.py
    ```
2.  The script will print a message and automatically open the Zerodha login page in your browser.
3.  Log in with your Zerodha credentials.
4.  After a successful login, you will be redirected to a success page. You can close the browser tab.
5.  The backtest will now start running in your terminal. The results will be printed and saved to `options_trades_v2.csv`.

---
**Disclaimer:** Backtesting results are not a guarantee of future performance. This script uses a simplified margin and stop-loss calculation and should be used for educational and research purposes. Trading in financial markets involves risk.