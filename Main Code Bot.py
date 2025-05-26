import os
import time
import pandas as pd
from binance.client import Client
from colorama import init, Fore, Style
import json
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Initialize colorama
init()

# Load API credentials from environment variables
API_KEY = os.environ.get('API_KEY')
API_SECRET = os.environ.get('API_SECRET')

if not API_KEY or not API_SECRET:
    raise ValueError("Please set API_KEY and API_SECRET in your .env file.")

# Initialize Binance client
client = Client(API_KEY, API_SECRET, testnet=True)  # Set testnet=False for live trading

# Define the trading pair and timeframe
symbol = 'SOLUSDT'
timeframe = '15m'

# Initialize buy_price as None
buy_price = None

# Set to True for testing, False for live trading
testing_mode = False

while True:
    try:
        # Fetch candlestick data for the trading pair
        klines = client.get_klines(symbol=symbol, interval=timeframe, limit=100)

        # Convert the data into a DataFrame
        df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        df['close'] = df['close'].astype(float)

        # Load buy_price from file if it exists
        if os.path.exists("buy_price.json"):
            with open("buy_price.json", "r") as buy_price_file:
                buy_price = json.load(buy_price_file)
                if buy_price is not None:
                    print(f"Buy Price: {buy_price}")
                else:
                    buy_price = None

        # Define Bollinger Bands strategy
        def bollinger_bands_strategy(df, window=20, num_std_dev=1):
            df['rolling_mean'] = df['close'].rolling(window=window).mean()
            df['rolling_std'] = df['close'].rolling(window=window).std()
            df['upper_band'] = df['rolling_mean'] + (df['rolling_std'] * num_std_dev)
            df['lower_band'] = df['rolling_mean'] - (df['rolling_std'] * num_std_dev)
            return df

        df = bollinger_bands_strategy(df)

        # Get current ticker price for accuracy
        current_price = float(client.get_symbol_ticker(symbol=symbol)['price'])

        # Retrieve free USDT balance
        free_usdt_balance = float(client.get_asset_balance(asset='USDT')['free'])

        # Buy condition
        def buy_condition():
            if current_price <= 0.99 * df['lower_band'].iloc[-1]:
                return True
            else:
                print(f"Wick Condition: {0.99 * df['lower_band'].iloc[-1]}")
                print(f"Current Price: {current_price}")
                print("Buy condition not met")
                return False

        # Sell condition
        def sell_condition():
            if buy_price is not None:
                price_difference = (current_price - buy_price) / buy_price
                if price_difference >= 0.012:
                    return True
                else:
                    print(f"Sell condition not met. Price Difference: {price_difference}, Current Price: {current_price}")
                    return False
            else:
                print(f"Sell condition not met. Buy Price: {buy_price}, Current Price: {current_price}")
                return False

        # Execute buy only if no position is open
        if buy_price is None and buy_condition() and free_usdt_balance > 1:
            if testing_mode:
                print(f"{Fore.GREEN}Simulating Buy Order{Style.RESET_ALL}")
                print(f"{Fore.GREEN}Simulated Buy Price: {current_price}{Style.RESET_ALL}")
                buy_price = current_price
                with open("buy_price.json", "w") as buy_price_file:
                    json.dump(buy_price, buy_price_file)
            else:
                symbol_info = client.get_symbol_info(symbol)
                lot_size_filter = next(f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE')
                quantity_step_size = float(lot_size_filter['stepSize'])
                max_precision = len(lot_size_filter['stepSize'].split('.')[1])

                max_quantity = free_usdt_balance / current_price
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
                    with open("buy_price.json", "w") as buy_price_file:
                        json.dump(buy_price, buy_price_file)
                    print(f"{Fore.GREEN}Buy Order Executed at Price: {buy_price}{Style.RESET_ALL}")
                else:
                    print("Insufficient USDT balance to buy.")

        # Execute sell if condition is met
        if sell_condition():
            sol_balance = float(client.get_asset_balance(asset='SOL')['free'])
            if sol_balance > 0:
                if testing_mode:
                    print(f"{Fore.RED}Simulating Sell Order{Style.RESET_ALL}")
                    print(f"{Fore.RED}Simulated Sell Price: {current_price}{Style.RESET_ALL}")
                    buy_price = None
                    with open("buy_price.json", "w") as buy_price_file:
                        json.dump(buy_price, buy_price_file)
                else:
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
                        with open("buy_price.json", "w") as buy_price_file:
                            json.dump(buy_price, buy_price_file)
                        print(f"{Fore.RED}Sell Order Executed at Price: {sell_price}{Style.RESET_ALL}")
                    else:
                        print("No SOL to sell or quantity too small.")
            else:
                print("No SOL balance to sell.")

        # Sleep for 1 second
        print(f"{Fore.BLUE}Sleeping for 1 second{Style.RESET_ALL}")
        time.sleep(1)

    except Exception as e:
        print(f"Error: {e}")
        time.sleep(1)  # Prevent rapid error looping





