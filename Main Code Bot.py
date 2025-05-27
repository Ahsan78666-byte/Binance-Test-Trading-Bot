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

# Define the trading pair
symbol = 'SOLUSDT'
timeframe = '15m'

# Initialize buy_price as None
buy_price = None

# Initialize sell price variable
sell_price = None
           
# Set to True for testing, False for live trading
testing_mode = False

# Initialize historical data
historical_data = []

while True:
    try:
        # Fetch candlestick data for the trading pair
        klines = client.get_klines(symbol=symbol, interval=timeframe, limit=100)  # Fetch previous candles

        # Extract the historical OHLCV data
        historical_data = klines  # Exclude the last (current) candle
        latest_ohlcv = klines  # The latest (current) candle

        # Convert the data into a DataFrame
        df = pd.DataFrame(historical_data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])

        # Convert numeric columns to the appropriate data types
        numeric_columns = ['open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume']
        df[numeric_columns] = df[numeric_columns].apply(pd.to_numeric, errors='coerce')

        # Set the timestamp as the index and convert to datetime
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)

        # Load the buy price from the file if it exists
        if os.path.exists("buy_price.json"):
            with open("buy_price.json", "r") as buy_price_file:
               buy_price = json.load(buy_price_file)
               print(f"Buy Price: {buy_price}")

        # Load the sell price from the file if it exists
        if os.path.exists("sell_price.json"):
            with open("sell_price.json", "r") as sell_price_file:
               sell_price = json.load(sell_price_file)
               print(f"Sell Price: {sell_price}")      

        # Define Bollinger Bands strategy
        def bollinger_bands_strategy(df, window=20, num_std_dev=1):
            df['rolling_mean'] = df['close'].rolling(window=window).mean()
            df['rolling_std'] = df['close'].rolling(window=window).std()
            df['upper_band'] = df['rolling_mean'] + (df['rolling_std'] * num_std_dev)
            df['lower_band'] = df['rolling_mean'] - (df['rolling_std'] * num_std_dev)
            df['signal'] = 0  # 0 means do nothing
            df.loc[(df['close'] <= df['lower_band']) & (df['close'] < df['rolling_mean']), 'signal'] = 1
            return df

        df = bollinger_bands_strategy(df)

        # Retrieve free USDT balance
        free_usdt_balance = float(client.get_asset_balance(asset='USDT')['free'])

        # Your buy and sell conditions
        def buy_condition():
            if df['close'].iloc[-1] <= 0.99 * df['lower_band'].iloc[-1]:
                return True
            else:
                print(f"Wick Condition: {0.99 * df['lower_band'].iloc[-1]}")
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
        if buy_condition() and free_usdt_balance > 1:
            if testing_mode:
                print(f"{Fore.GREEN}Simulating Buy Order{Style.RESET_ALL}")
                print(f"{Fore.GREEN}Simulated Buy Price: {df['close'].iloc[-1]}{Style.RESET_ALL}") # Print the simulated buy price         
                with open("buy_price.json", "w") as buy_price_file:
                    json.dump(df['close'].iloc[-1], buy_price_file)
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
                        json.dump(buy_price, position_open)
                        print(f"{Fore.GREEN}Buy Order Executed at Price: {buy_price}{Style.RESET_ALL}")
                    else:
                        print("Insufficient USDT balance to buy.")

                # Continue to the sell condition check
                    continue
            
        # Sell condition check
        if sell_condition():
            sol_balance = float(client.get_asset_balance(asset='SOL')['free'])
            if sol_balance > 0:
                if testing_mode:
                    print(f"{Fore.RED}Simulating Sell Order{Style.RESET_ALL}")
                    print(f"{Fore.RED}Simulated Sell Price:{df['close'].iloc[-1]}{Style.RESET_ALL}")  # Print the simulated sell price
                    buy_price = None  # Reset the buy price after selling
                    with open("buy_price.json", "w") as buy_price_file:
                        json.dump(buy_price, buy_price_file)
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
                            json.dump(buy_price, position_open)
                            print(f"{Fore.RED}Sell Order Executed at Price: {sell_price}{Style.RESET_ALL}")
                        else:
                            print("No SOL to sell or quantity too small.")
                    else:
                        print("No SOL balance to sell.")
        # Sleep for a while (you can adjust the interval)
        print(f"{Fore.BLUE}Sleeping for 1 seconds{Style.RESET_ALL}")
        time.sleep(1)  # Sleep for 0 seconds                    

    except Exception as e:
        print("Error:", e)






