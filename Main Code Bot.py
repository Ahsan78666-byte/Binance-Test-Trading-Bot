import os
import time
import pandas as pd
from binance.client import Client
from colorama import init, Fore, Style
import json
import math
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Initialize colorama
init()

# Load API credentials from environment variables
API_KEY = os.environ.get('API_KEY')
API_SECRET = os.environ.get('API_SECRET')

if not API_KEY or not API_SECRET:
    raise ValueError("Please set API_KEY and API_SECRET environment variables.")

# Initialize the Binance client
client = Client(API_KEY, API_SECRET, testnet=True)

# Define the trading pair
symbol = 'SOLUSDT'
timeframe = '15m'

# Initialize buy_price and sell_price as None
buy_price = None
sell_price = None

# Set to True for testing, False for live trading
testing_mode = False

# Initialize historical data
historical_data = []

def fetch_historical_data():
    # Fetch candlestick data for the trading pair
    klines = client.get_klines(symbol=symbol, interval=timeframe, limit=400)
    return klines

def process_klines(klines):
    # Extract the historical OHLCV data
    historical_data = klines[:-1]  # Exclude the last (current) candle
    latest_ohlcv = klines[-1]  # The latest (current) candle

    # Convert the data into a DataFrame
    df = pd.DataFrame(historical_data, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time',
        'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume',
        'taker_buy_quote_asset_volume', 'ignore'
    ])

    # Convert numeric columns to the appropriate data types
    numeric_columns = ['open', 'high', 'low', 'close', 'volume', 'close_time',
                       'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume',
                       'taker_buy_quote_asset_volume']
    df[numeric_columns] = df[numeric_columns].apply(pd.to_numeric, errors='coerce')

    # Set the timestamp as the index and convert to datetime
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)

    return df

def load_price_from_file(file_path):
    if os.path.exists(file_path):
        with open(file_path, "r") as file:
            return json.load(file)
    return None

def save_price_to_file(file_path, price):
    with open(file_path, "w") as file:
        json.dump(price, file)

def bollinger_bands_strategy(df, window=10, num_std_dev=1):
    df['rolling_mean'] = df['close'].rolling(window=window).mean()
    df['rolling_std'] = df['close'].rolling(window=window).std()
    df['upper_band'] = df['rolling_mean'] + (df['rolling_std'] * num_std_dev)
    df['lower_band'] = df['rolling_mean'] - (df['rolling_std'] * num_std_dev)
    df['signal'] = 0  # 0 means do nothing
    df.loc[(df['close'] <= df['lower_band']) & (df['close'] < df['rolling_mean']), 'signal'] = 1
    return df

while True:
    try:
        # Fetch and process historical data
        klines = fetch_historical_data()
        df = process_klines(klines)
        df = bollinger_bands_strategy(df)

        # Load buy and sell prices from files
        buy_price = load_price_from_file("buy_price.json")
        sell_price = load_price_from_file("sell_price.json")

        # Retrieve free USDT balance
        free_usdt_balance = float(client.get_asset_balance(asset='USDT')['free'])

        # Your buy and sell conditions
        def buy_condition():
            if df['close'].iloc[-1] <= 0.98 * df['lower_band'].iloc[-1]:
                return True
            else:
                print(f"Wick Condition: {0.98 * df['lower_band'].iloc[-1]}")
                print("Buy condition not met")
            return False
          
        # Sell condition using "OR" gate logic
        def sell_condition():
            if buy_price is not None:
                current_price = df['close'].iloc[-1]
                buy_price_float = float(buy_price)
                price_difference = (current_price - buy_price_float) / buy_price_float
                if price_difference >= 0.012:           
                    return True
                else:
                    print(f"Sell condition not met. Price Differnce: {price_difference}, Current Price: {df['close'].iloc[-1]}")
                    return False
            else:
                print(f"Sell condition not met. Buy Price: {buy_price}, Current Price: {df['close'].iloc[-1]}")
            return False

        # Buy condition check
        if df['signal'].iloc[-1] == 1 and free_usdt_balance > 1:
            if testing_mode:
                print(f"{Fore.GREEN}Simulating Buy Order{Style.RESET_ALL}")
                print(f"{Fore.GREEN}Simulated Buy Price: {df['close'].iloc[-1]}{Style.RESET_ALL}")
                save_price_to_file("buy_price.json", df['close'].iloc[-1])
            else:
                # Retrieve symbol info for 'SOLUSDT'
                symbol_info = client.get_symbol_info('SOLUSDT')
                lot_size_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'), None)

                if lot_size_filter:
                    quantity_step_size = float(lot_size_filter['stepSize'])
                    max_precision = len(lot_size_filter['maxQty'].split('.')[1])

                    # Calculate the quantity based on available USDT balance
                    solusdt_ticker = client.get_symbol_ticker(symbol='SOLUSDT')
                    current_sol_price = float(solusdt_ticker['price'])
                    usdt_balance = float(client.get_asset_balance(asset='USDT')['free'])
                    quantity_to_buy = usdt_balance / current_sol_price

                    # Ensure the quantity adheres to Binance's rules for step size
                    quantity_to_buy -= quantity_to_buy % quantity_step_size

                    # Adjust the quantity to match the maximum allowed precision
                    quantity_to_buy = round(quantity_to_buy, max_precision)

                    print("Executing Buy Order")
                    # Place a real buy order here
                    order = client.create_order(
                        symbol=symbol,
                        side='BUY',
                        type='MARKET',
                        quantity=quantity_to_buy
                    )
                    print(f"{Fore.GREEN}Executing Buy Order: {order}{Style.RESET_ALL}")
                    buy_price = order['fills'][0]['price']  # Store the real buy price
                    save_price_to_file("buy_price.json", buy_price)

                else:
                    print("LOT_SIZE filter not found in symbol info.")

        # Sell condition check
        if sell_condition():
            sol_balance = float(client.get_asset_balance(asset='SOL')['free'])
            if sol_balance > 0:
                if testing_mode:
                    print(f"{Fore.RED}Simulating Sell Order{Style.RESET_ALL}")
                    print(f"{Fore.RED}Simulated Sell Price: {df['close'].iloc[-1]}{Style.RESET_ALL}")
                    buy_price = None  # Reset the buy price after selling
                    save_price_to_file("buy_price.json", buy_price)
                else:
                    symbol_info = client.get_symbol_info('SOLUSDT')
                    lot_size_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'), None)

                    if lot_size_filter:
                        quantity_step_size = float(lot_size_filter['stepSize'])
                        max_precision = len(lot_size_filter['maxQty'].split('.')[1])

                        # Calculate the quantity based on available balance
                        sol_balance = float(client.get_asset_balance(asset='SOL')['free'])
                        quantity_to_sell = sol_balance

                        # Ensure the quantity adheres to Binance's rules for step size
                        quantity_to_sell -= quantity_to_sell % quantity_step_size

                        # Adjust the quantity to match the maximum allowed precision
                        quantity_to_sell = round(quantity_to_sell, max_precision)

                        print(f"{Fore.RED}Executing Sell Order{Style.RESET_ALL}")
                        # Place a real sell order here
                        order = client.create_order(
                            symbol=symbol,
                            side='SELL',
                            type='MARKET',
                            quantity=quantity_to_sell
                        )
                        print(f"{Fore.RED}Sell Order Executed: {order}{Style.RESET_ALL}")
                        sell_price = order['fills'][0]['price']  # Store the real sell price
                        save_price_to_file("sell_price.json", sell_price)
                        buy_price = None  # Reset the buy price after selling
                        save_price_to_file("buy_price.json", buy_price)
                    else:
                        print("LOT_SIZE filter not found in symbol info.")

        # Sleep for a while (you can adjust the interval)
        print(f"{Fore.BLUE}Sleeping for 1 second{Style.RESET_ALL}")
        time.sleep(1)  # Sleep for 1 second

    except Exception as e:
        print("Error:", e)
