from kiteconnect import KiteConnect
import logging

# --- README: HOW TO USE THIS SCRIPT ---
#
# This script simplifies the process of generating a daily access token for the Zerodha Kite API.
#
# **Instructions:**
#
# 1. **Fill in your Credentials:**
#    - Add your `API_KEY` and `API_SECRET` from your Zerodha developer app into the configuration section below.
#
# 2. **Run the Script (Part 1):**
#    - Run this script from your terminal: `python generate_access_token.py`
#    - The script will print a login URL to your console.
#
# 3. **Log in and Get the Request Token:**
#    - Copy the printed URL and paste it into your web browser.
#    - Log in with your Zerodha credentials.
#    - After logging in, you will be redirected to a blank page (e.g., http://127.0.0.1). This is normal.
#    - Copy the **entire URL** from your browser's address bar. It will look something like:
#      `http://127.0.0.1/?request_token=YOUR_REQUEST_TOKEN_HERE&action=login&status=success`
#
# 4. **Run the Script (Part 2):**
#    - Go back to your terminal. The script will be waiting for you to paste the URL.
#    - Paste the full URL you copied and press Enter.
#
# 5. **Get Your Access Token:**
#    - The script will automatically extract the `request_token`, generate the final `access_token`, and print it to the console.
#    - This is the access token you will use for the main trading bot script. It is valid for one day.
#
# --- CONFIGURATION ---

logging.basicConfig(level=logging.INFO)

# --- Credentials (FILL THESE IN) ---
API_KEY = "YOUR_API_KEY"
API_SECRET = "YOUR_API_SECRET"


# --- SCRIPT LOGIC ---

if __name__ == "__main__":
    if "YOUR_API_KEY" in [API_KEY, API_SECRET]:
        logging.error("Please fill in your API_KEY and API_SECRET in the script.")
    else:
        # --- Part 1: Generate Login URL ---
        kite = KiteConnect(api_key=API_KEY)
        login_url = kite.login_url()

        print("--- Step 1: Generate Login URL ---")
        print(f"Please open this URL in your browser to log in:\n\n{login_url}\n")

        # --- Part 2: Get Request Token and Generate Access Token ---
        try:
            redirected_url = input("--- Step 2: After logging in, please paste the full redirected URL here and press Enter ---\n> ")

            # Extract request_token from the URL
            request_token = redirected_url.split("request_token=")[1].split("&")[0]
            logging.info(f"Successfully extracted request_token: {request_token}")

            # Generate access token
            data = kite.generate_session(request_token, api_secret=API_SECRET)
            access_token = data["access_token"]

            print("\n--- Step 3: Your Access Token is Ready! ---")
            print(f"Successfully generated access_token. Please copy it and paste it into your main trading bot script.")
            print(f"\nYour Access Token (valid for one day):\n\n{access_token}\n")

        except Exception as e:
            logging.error(f"An error occurred: {e}")
            print("\nCould not generate access token. Please ensure you copied the full redirected URL correctly and try again.")