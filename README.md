# Zerodha Donchian Channel Trading Bot

This project contains a simple, long-only trading bot designed to work with the Zerodha Kite API. It uses a Donchian Channel strategy and is built to be tested safely and run in a live environment.

## Features

- **Strategy:** Long-only Donchian Channel.
  - **Entry:** Buys when the price hits the lower Donchian band.
  - **Exit:** Sells when the price hits the upper band (Take-Profit) or hits a percentage-based stop-loss.
- **Real-Time:** Uses WebSockets for real-time price data, ensuring immediate reaction to market events.
- **Automated Startup:** A simple `start_bot.py` script handles the daily authentication process automatically.
- **Telegram Notifications:** Get real-time alerts for all major events (startup, orders, errors) on your phone.
- **Safe Testing Mode:** A built-in `TESTING_MODE` allows you to test the bot with a single share and no leverage before deploying your full strategy.
- **Backtesting:** A companion backtesting script (`donchian_reversal_backtest.py`) allows you to test the strategy using the same Zerodha data source.

## Setup Instructions

Follow these steps to get your bot up and running.

### Step 1: Install Dependencies

This project requires a few Python libraries. You can install them all by running this command in your terminal:

```bash
pip install -r requirements.txt
```

### Step 2: Get Zerodha API Credentials

1.  Go to the [Zerodha Kite Connect developer portal](https://developers.kite.trade/).
2.  Click "Create new app".
3.  Fill in the form:
    - **App name:** `MyDonchianBot` (or any name you prefer)
    - **Client ID:** Your Zerodha client ID.
    - **Redirect URL:** `http://127.0.0.1:5000/redirect` (Use this exact URL)
    - **Description:** A short description, e.g., "Personal trading bot".
4.  After creating the app, you will get your `api_key` and `api_secret`.

### Step 3: Create a Telegram Bot

To receive notifications, you need a Telegram bot.

1.  Open Telegram and search for the **BotFather**.
2.  Start a chat with BotFather and send the `/newbot` command.
3.  Follow the prompts to name your bot. BotFather will give you a **Bot Token**.
4.  Next, find your **Chat ID**. Search for the **userinfobot** on Telegram, start a chat, and it will give you your user/chat ID.
5.  If you want to send notifications to a group, add the bot to the group and get the group's chat ID.

### Step 4: Configure the Scripts

You need to add your secret credentials to two files. **Never share these files or commit them to a repository.**

1.  **Configure the Auto-Start Script (`start_bot.py`):**
    - Open `start_bot.py`.
    - Fill in your `API_KEY` and `API_SECRET`.

2.  **Configure the Main Bot (`zerodha_bot_template.py`):**
    - Open `zerodha_bot_template.py`.
    - Fill in your `API_KEY` and `API_SECRET`.
    - Fill in your `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
    - Configure your `TRADING_SYMBOL`, `QUANTITY`, `LEVERAGE`, and other strategy parameters.

### Step 5: Run the Bot

You are now ready to start the bot.

1.  **For initial, safe testing:**
    - In `zerodha_bot_template.py`, make sure `TESTING_MODE` is set to `True`. This will force the bot to trade only 1 share with no leverage.

2.  **Start the bot:**
    - Open your terminal and run the `start_bot.py` script:
      ```bash
      python start_bot.py
      ```
    - Your web browser will automatically open the Zerodha login page.
    - Log in with your credentials.
    - After a successful login, you will be redirected to a success page. You can close the browser tab.

3.  **Check your terminal.** The trading bot is now running and will start printing log messages. You will also receive a startup notification on Telegram.

### Step 6: Running the Backtest

To run the backtest with the same logic:

1.  Open `donchian_reversal_backtest.py`.
2.  Fill in your `API_KEY`, `API_SECRET`, and a valid `ACCESS_TOKEN` (you can get this from the `generate_access_token.py` script if needed, or from the `start_bot.py` log).
3.  Run the script: `python donchian_reversal_backtest.py`.
4.  The results will be printed to the console and saved in a CSV file.

---
**Disclaimer:** Trading in financial markets involves risk. This software is provided as a template and should be used at your own risk. Always test thoroughly before deploying with significant capital.