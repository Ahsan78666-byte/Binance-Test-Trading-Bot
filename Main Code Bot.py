import os
import time
import pandas as pd
from binance.client import Client
from colorama import init, Fore, Style
import json
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Initialize colorama for colored output
init()

# Load API credentials from environment variables
API_KEY = os.environ.get('API_KEY')
API_SECRET = os.environ.get('API_SECRET')

# Initialize Binance client
client = Client(API_KEY, API_SECRET, testnet=True)  # Set testnet=False for live trading

if not API_KEY or not API_SECRET:
    raise ValueError("Please set API_KEY and API_SECRET in your .env file.")

# Trading pair and timeframe
symbol = 'SOLUSDT'
timeframe = '15m'

# Set to False for live trading deployment
testing_mode = False

# State file for tracking buy_price and position_open
state_file = "trade_state.json"

### State Management Functions
def load_state():
    """Load trading state from trade_state.json."""
    default_state = {"buy_price": None, "position_open": False}
    try:
        if os.path.exists(state_file):
            with open(state_file, "r") as f:
                state = json.load(f)
                buy_price = state.get("buy_price")
                if buy_price is not None:
                    buy_price = float(buy_price)  # Ensure buy_price is a float
                position_open = bool(state.get("position_open", False))
                return {"buy_price": buy_price, "position_open": position_open}
        return default_state
    except (json.JSONDecodeError, IOError, ValueError) as e:
        print(f"Error loading state: {e}. Using default state.")
        return default_state

def save_state(buy_price, position_open):
    """Save trading state to trade_state.json."""
    try:
        with open(state_file, "w") as f:
            json.dump({"buy_price": buy_price, "position_open": position_open}, f)
    except IOError as e:
        print(f"Error saving state: {e}")

### Trading Strategy Functions
def bollinger_bands_strategy(df, window=20, num_std_dev=1):
    """Calculate Bollinger Bands on the DataFrame."""
    df['rolling_mean'] = df['close'].rolling(window=window).mean()
    df['rolling_std'] = df['close'].rolling(window=window).std()
    df['upper_band'] = df['rolling_mean'] + (df['rolling_std'] * num_std_dev)
    df['lower_band'] = df['rolling_mean'] - (df['rolling_std'] * num_std_dev)
    return df

def buy_condition(df):
    """Check if price is at or below 99% of the lower Bollinger Band."""
    return df['close'].iloc[-1] <= 0.99 * df['lower_band'].iloc[-1]

def sell_condition(current_price, buy_price):
    """Check if profit is at least 1.2% above buy_price."""
    if buy_price is None:
        return False
    price_difference = (current_price - buy_price) / buy_price
    return price_difference >= 0.012

### Main Trading Loop
while True:
    try:
        # Load current state
        state = load_state()
        buy_price = state["buy_price"]
        position_open = state["position_open"]

        # Fetch candlestick data from Binance
        klines = client.get_klines(symbol=symbol, interval=timeframe, limit=100)
        df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 
                                           'close_time', 'quote_asset_volume', 'number_of_trades', 
                                           'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
        numeric_columns = ['open', 'high', 'low', 'close', 'volume', 'quote_asset_volume', 
                           'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume']
        df[numeric_columns] = df[numeric_columns].apply(pd.to_numeric, errors='coerce')
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)

        # Apply Bollinger Bands strategy
        df = bollinger_bands_strategy(df)

        # Get current price
        current_price = df['close'].iloc[-1]

        # Get free USDT balance
        free_usdt_balance = float(client.get_asset_balance(asset='USDT')['free'])

        # Trading logic based on position state
        if not position_open:
            # Check for buy opportunity
            if buy_condition(df) and free_usdt_balance > 1:
                if testing_mode:
                    print(f"{Fore.GREEN}Simulating Buy Order{Style.RESET_ALL}")
                    print(f"{Fore.GREEN}Simulated Buy Price: {current_price}{Style.RESET_ALL}")
                    buy_price = current_price
                    position_open = True
                    save_state(buy_price, position_open)
                else:
                    # Get symbol info for quantity precision
                    symbol_info = client.get_symbol_info(symbol)
                    lot_size_filter = next(f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE')
                    quantity_step_size = float(lot_size_filter['stepSize'])
                    max_precision = len(lot_size_filter['stepSize'].split('.')[1])

                    # Get current price for quantity calculation
                    current_sol_price = float(client.get_symbol_ticker(symbol=symbol)['price'])

                    # Calculate quantity to buy
                    max_quantity = free_usdt_balance / current_sol_price
                    quantity_to_buy = (max_quantity // quantity_step_size) * quantity_step_size
                    quantity_to_buy = round(quantity_to_buy, max_precision)

                    if quantity_to_buy > 0:
                        order = client.create_order(
                            symbol=symbol,
                            side='BUY',
                            type='MARKET',
                            quantity=quantity_to_buy
                        )
                        buy_price = float(order['fills'][0]['price'])
                        position_open = True
                        save_state(buy_price, position_open)
                        print(f"{Fore.GREEN}Buy Order Executed at Price: {buy_price}{Style.RESET_ALL}")
                    else:
                        print("Insufficient USDT balance to buy.")
        else:
            # Check for sell opportunity
            if sell_condition(current_price, buy_price):
                if testing_mode:
                    print(f"{Fore.RED}Simulating Sell Order{Style.RESET_ALL}")
                    print(f"{Fore.RED}Simulated Sell Price: {current_price}{Style.RESET_ALL}")
                    buy_price = None
                    position_open = False
                    save_state(buy_price, position_open)
                else:
                    sol_balance = float(client.get_asset_balance(asset='SOL')['free'])
                    if sol_balance > 0:
                        # Reuse quantity precision from symbol info
                        symbol_info = client.get_symbol_info(symbol)
                        lot_size_filter = next(f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE')
                        quantity_step_size = float(lot_size_filter['stepSize'])
                        max_precision = len(lot_size_filter['stepSize'].split('.')[1])

                        quantity_to_sell = (sol_balance // quantity_step_size) * quantity_step_size
                        quantity_to_sell = round(quantity_to_sell, max_precision)
                        if quantity_to_sell > 0:
                            order = client.create_order(
                                symbol=symbol,
                                side='SELL',
                                type='MARKET',
                                quantity=quantity_to_sell
                            )
                            sell_price = float(order['fills'][0]['price'])
                            buy_price = None
                            position_open = False
                            save_state(buy_price, position_open)
                            print(f"{Fore.RED}Sell Order Executed at Price: {sell_price}{Style.RESET_ALL}")
                        else:
                            print("No SOL to sell or quantity too small.")
                    else:
                        print("No SOL balance to sell.")

        # Brief pause between iterations
        print(f"{Fore.BLUE}Sleeping for 1 second{Style.RESET_ALL}")
        time.sleep(1)

    except Exception as e:
        print(f"Error: {e}")
        time.sleep(1)  # Prevent rapid error looping






