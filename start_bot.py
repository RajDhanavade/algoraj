import logging
import threading
import webbrowser
from flask import Flask, request, render_template_string
from kiteconnect import KiteConnect
import subprocess
import os

# --- 1. README ---
#
# This script provides a semi-automated way to start your trading bot.
#
# How it works:
# 1. It starts a small, temporary web server on your local machine.
# 2. It automatically opens the Zerodha login URL in your browser.
# 3. You log in with your Zerodha credentials.
# 4. After login, Zerodha redirects you back to our local server.
# 5. The server "catches" the request_token from the URL.
# 6. It uses this token to generate the final access_token.
# 7. It then automatically starts the main `zerodha_bot_template.py` script for you.
#
# --- 2. CONFIGURATION ---

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Credentials (FILL THESE IN) ---
# Make sure these match the credentials in your `zerodha_bot_template.py`
API_KEY = "YOUR_API_KEY"
API_SECRET = "YOUR_API_SECRET"

# --- 3. WEB SERVER AND BOT LAUNCHER ---

app = Flask(__name__)
access_token = None
kite = KiteConnect(api_key=API_KEY)

# Suppress Flask's startup messages for a cleaner console
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

@app.route("/")
def home():
    """A simple page to show that the local server is running."""
    return "<html><body><h1>Local server is running. Please log in via the tab that just opened.</h1></body></html>"

@app.route("/redirect")
def redirect_url_handler():
    """Handles the redirect from Zerodha after successful login."""
    global access_token
    request_token = request.args.get("request_token")
    if not request_token:
        return "<html><body><h1>Login failed. No request token found.</h1></body></html>"

    try:
        data = kite.generate_session(request_token, api_secret=API_SECRET)
        access_token = data["access_token"]
        logging.info("Access token generated successfully!")

        # Now that we have the token, we can shut down the server
        # and start the main bot script.
        # Note: This is a simple way to shut down a dev server.
        # In a production environment, you'd use a more robust method.
        shutdown_server()

        return render_template_string("""
            <html>
                <head><title>Success!</title></head>
                <body>
                    <h1>Login Successful!</h1>
                    <p>Access token has been generated. The trading bot is now starting in your terminal.</p>
                    <p>You can close this browser tab.</p>
                </body>
            </html>
        """)
    except Exception as e:
        logging.error(f"Error generating access token: {e}")
        return f"<html><body><h1>Error</h1><p>Could not generate access token: {e}</p></body></html>"

def shutdown_server():
    """Function to shut down the Flask server."""
    func = request.environ.get('werkzeug.server.shutdown')
    if func is None:
        raise RuntimeError('Not running with the Werkzeug Server')
    func()

def start_bot_process(token):
    """Starts the main trading bot as a separate process."""
    logging.info("Starting the main trading bot script...")
    # We need to find the `zerodha_bot_template.py` file and run it.
    # This assumes it's in the same directory.
    bot_script_path = os.path.join(os.path.dirname(__file__), 'zerodha_bot_template.py')

    # We pass the access token to the bot script as an environment variable.
    # This is a secure way to handle it.
    env = os.environ.copy()
    env['ZERODHA_ACCESS_TOKEN'] = token

    # Run the script using subprocess.
    # We use `subprocess.Popen` so it runs in the background and we can see its output.
    subprocess.Popen(['python', bot_script_path], env=env)

def main():
    """Main function to start the login process."""
    if "YOUR_API_KEY" in [API_KEY, API_SECRET]:
        logging.error("Please fill in your API_KEY and API_SECRET in the `start_bot.py` script.")
        return

    # Start the Flask server in a separate thread
    server_thread = threading.Thread(target=lambda: app.run(port=5000))
    server_thread.daemon = True
    server_thread.start()
    logging.info("Local server started on http://127.0.0.1:5000")

    # Open the Zerodha login URL in a new browser tab
    login_url = kite.login_url()
    webbrowser.open(login_url)
    logging.info("Please complete the login in your browser.")

    # The server will run until the redirect is handled and `shutdown_server` is called.
    server_thread.join()

    if access_token:
        start_bot_process(access_token)
    else:
        logging.error("Failed to obtain access token. Bot will not start.")

if __name__ == "__main__":
    main()