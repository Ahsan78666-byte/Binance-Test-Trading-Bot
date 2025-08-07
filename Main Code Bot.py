import os
import time
import pandas as pd
from binance.client import Client
from colorama import init, Fore, Style
import json
from dotenv import load_dotenv


# Load .env file
load_dotenv()

# Initialize colorama
init()

# Load API credentials from environment variables
API_KEY = os.environ.get('API_KEY')
API_SECRET = os.environ.get('API_SECRET')

if not API_KEY or not API_SECRET:
    raise ValueError("Please set API_KEY and API_SECRET environment variables in your .env file.")

# Initialize the Binance client (use testnet if you want testing)
client = Client(API_KEY, API_SECRET, testnet=True)

# Define trading pair and timeframe
symbol = 'SOLUSDT'
timeframe = '15m'

# File paths for saved prices
BUY_PRICE_FILE = "buy_price.json"
SELL_PRICE_FILE = "sell_price.json"

# Initialize variables
buy_price = None
sell_price = None

# Testing mode switch
testing_mode = False

def load_price(file_path):
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r') as f:
                price = json.load(f)
                if price is not None:
                    return float(price)
        except Exception as e:
            print(f"{Fore.YELLOW}Warning: Failed to load price from {file_path}: {e}{Style.RESET_ALL}")
    return None

def save_price(file_path, price):
    try:
        with open(file_path, 'w') as f:
            json.dump(price, f)
    except Exception as e:
        print(f"{Fore.YELLOW}Warning: Failed to save price to {file_path}: {e}{Style.RESET_ALL}")

# Bollinger Bands strategy function
def bollinger_bands_strategy(df, window=10, num_std_dev=1):
    df['rolling_mean'] = df['close'].rolling(window=window).mean()
    df['rolling_std'] = df['close'].rolling(window=window).std()
    df['upper_band'] = df['rolling_mean'] + (df['rolling_std'] * num_std_dev)
    df['lower_band'] = df['rolling_mean'] - (df['rolling_std'] * num_std_dev)
    df['signal'] = 0  # 0 = hold, 1 = buy
    df.loc[(df['close'] <= df['lower_band']) & (df['close'] < df['rolling_mean']), 'signal'] = 1
    return df

def buy_condition(df):
    threshold = 0.99 * df['lower_band'].iloc[-1]
    current_close = df['close'].iloc[-1]
    if current_close <= threshold:
        return True
    else:
        print(f"Wick Condition Not Met: close {current_close} > 0.99 * lower_band {threshold}")
        return False

def sell_condition(current_price, buy_price):
    if buy_price is None:
        print(f"Sell condition not met because buy_price is None")
        return False
    price_difference = (current_price - buy_price) / buy_price
    if price_difference >= 0.012:  # 1.2% gain target
        return True
    else:
        print(f"Sell condition not met: price difference {price_difference:.5f} < 0.012")
        return False

def round_down_quantity(quantity, step):
    """Round down quantity to nearest multiple of step size."""
    return (quantity // step) * step

# Load saved buy and sell prices on startup
buy_price = load_price(BUY_PRICE_FILE)
sell_price = load_price(SELL_PRICE_FILE)
if buy_price is not None:
    print(f"{Fore.CYAN}Loaded buy price: {buy_price}{Style.RESET_ALL}")
if sell_price is not None:
    print(f"{Fore.CYAN}Loaded sell price: {sell_price}{Style.RESET_ALL}")

# Main loop
while True:
    try:
        # Fetch klines (candles). We fetch 101 to exclude current candle
        klines = client.get_klines(symbol=symbol, interval=timeframe, limit=101)
        # Exclude the last candle (current forming candle)
        historical_klines = klines[:-1]

        # Create DataFrame
        df = pd.DataFrame(historical_klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ])

        # Convert columns to numeric
        numeric_cols = ['open', 'high', 'low', 'close', 'volume',
                        'close_time', 'quote_asset_volume', 'number_of_trades',
                        'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume']
        df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors='coerce')

        # Timestamp conversion and indexing
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)

        # Apply strategy
        df = bollinger_bands_strategy(df)

        # Get latest actual close price (last row in df)
        current_close = df['close'].iloc[-1]

        # Get USDT balance
        free_usdt_balance = float(client.get_asset_balance(asset='USDT')['free'])

        # Get SOL balance
        free_sol_balance = float(client.get_asset_balance(asset='SOL')['free'])

        # Buy logic
        if buy_condition(df) and free_usdt_balance > 1 and buy_price is None:
            if testing_mode:
                print(f"{Fore.GREEN}Simulated Buy Order at {current_close}{Style.RESET_ALL}")
                buy_price = current_close
                save_price(BUY_PRICE_FILE, buy_price)
            else:
                # Get LOT_SIZE filter stepSize and precision
                symbol_info = client.get_symbol_info(symbol)
                lot_size_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'), None)
                if not lot_size_filter:
                    print(f"{Fore.RED}LOT_SIZE filter not found for symbol {symbol}{Style.RESET_ALL}")
                    time.sleep(60)
                    continue
                step_size = float(lot_size_filter['stepSize'])
                precision = len(lot_size_filter['stepSize'].split('.')[1])

                # Calculate max quantity purchasable
                max_qty = free_usdt_balance / current_close
                qty_to_buy = round_down_quantity(max_qty, step_size)
                qty_to_buy = round(qty_to_buy, precision)

                if qty_to_buy <= 0:
                    print(f"{Fore.RED}Quantity to buy calculated as zero or too small: {qty_to_buy}{Style.RESET_ALL}")
                else:
                    print(f"{Fore.GREEN}Placing BUY order: qty={qty_to_buy}, price ≈ {current_close}{Style.RESET_ALL}")
                    order = client.create_order(
                        symbol=symbol,
                        side='BUY',
                        type='MARKET',
                        quantity=qty_to_buy
                    )
                    fills = order.get('fills')
                    if fills and len(fills) > 0:
                        buy_price = float(fills[0]['price'])
                        print(f"{Fore.GREEN}Buy order executed at price: {buy_price}{Style.RESET_ALL}")
                        save_price(BUY_PRICE_FILE, buy_price)
                    else:
                        print(f"{Fore.RED}Buy order filled price not found!{Style.RESET_ALL}")

        # Sell logic
        elif buy_price is not None and sell_condition(current_close, buy_price) and free_sol_balance > 0:
            if testing_mode:
                print(f"{Fore.RED}Simulated Sell Order at {current_close}{Style.RESET_ALL}")
                buy_price = None
                save_price(BUY_PRICE_FILE, buy_price)
                save_price(SELL_PRICE_FILE, current_close)
            else:
                symbol_info = client.get_symbol_info(symbol)
                lot_size_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'), None)
                if not lot_size_filter:
                    print(f"{Fore.RED}LOT_SIZE filter not found for symbol {symbol}{Style.RESET_ALL}")
                    time.sleep(60)
                    continue
                step_size = float(lot_size_filter['stepSize'])
                precision = len(lot_size_filter['stepSize'].split('.')[1])

                qty_to_sell = round_down_quantity(free_sol_balance, step_size)
                qty_to_sell = round(qty_to_sell, precision)

                if qty_to_sell <= 0:
                    print(f"{Fore.RED}Quantity to sell calculated as zero or too small: {qty_to_sell}{Style.RESET_ALL}")
                else:
                    print(f"{Fore.RED}Placing SELL order: qty={qty_to_sell}{Style.RESET_ALL}")
                    order = client.create_order(
                        symbol=symbol,
                        side='SELL',
                        type='MARKET',
                        quantity=qty_to_sell
                    )
                    fills = order.get('fills')
                    if fills and len(fills) > 0:
                        sell_price = float(fills[0]['price'])
                        print(f"{Fore.RED}Sell order executed at price: {sell_price}{Style.RESET_ALL}")
                        save_price(SELL_PRICE_FILE, sell_price)
                        buy_price = None
                        save_price(BUY_PRICE_FILE, buy_price)
                    else:
                        print(f"{Fore.RED}Sell order filled price not found!{Style.RESET_ALL}")

        else:
            print(f"{Fore.BLUE}No trade action taken. Current price: {current_close}, Buy Price: {buy_price}{Style.RESET_ALL}")

        # Sleep for 0 minutes to align with candle timeframe
        print(f"{Fore.BLUE}Sleeping for 0 minutes...{Style.RESET_ALL}")
        time.sleep(2)

    except Exception as e:
        print(f"{Fore.RED}Error: {e}{Style.RESET_ALL}")
        # Avoid tight loop on error
        time.sleep(2)





