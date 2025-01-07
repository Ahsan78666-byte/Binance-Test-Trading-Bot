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

# Initialize the Binance client
client = Client(API_KEY, API_SECRET, testnet=True)

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
        klines = client.get_historical_klines(symbol=symbol, interval=timeframe, limit=124)  # Fetch previous candles

        # Extract the historical OHLCV data
        historical_data = klines[:-1]  # Exclude the last (current) candle
        latest_ohlcv = klines[-1]  # The latest (current) candle

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
        def bollinger_bands_strategy(df, window=8, num_std_dev=1):
            df['rolling_mean'] = df['close'].rolling(window=window).mean()
            df['rolling_std'] = df['close'].rolling(window=window).std()
            df['upper_band'] = df['rolling_mean'] + (df['rolling_std'] * num_std_dev)
            df['lower_band'] = df['rolling_mean'] - (df['rolling_std'] * num_std_dev)
            df['signal'] = 0  # 0 means do nothing
            df.loc[(df['close'] <= df['lower_band']) & (df['close'] < df['rolling_mean']), 'signal'] = 1
            return df

        df = bollinger_bands_strategy(df)

        # Calculate the 100-period EMA
        df['ema_100'] = df['close'].ewm(span=100, adjust=False).mean()

        # Retrieve free USDT balance
        free_usdt_balance = float(client.get_asset_balance(asset='USDT')['free'])

        # Retrieve symbol info
        symbol_info = client.get_symbol_info(symbol)
        min_notional = None

        for f in symbol_info['filters']:
            if f['filterType'] == 'MIN_NOTIONAL':
                min_notional = float(f['minNotional'])

        # Ensure min_notional is properly assigned
        if min_notional is None:
            raise ValueError("min_notional is not assigned properly. Please check the symbol info.")

        # Your buy and sell conditions
        def buy_condition():
            current_open = float(latest_ohlcv[1])
            current_ema_100 = df['ema_100'].iloc[-1]
            if df['close'].iloc[-1] <= 0.982 * df['lower_band'].iloc[-1] and current_open < current_ema_100:
                return True
            else:
                print(f"Wick Condition: {0.982 * df['lower_band'].iloc[-1]}")
                print(f"Current Open: {current_open}, EMA 100: {current_ema_100}")
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
                    print(f"Sell condition not met. Price Difference: {price_difference}, Current Price: {df['close'].iloc[-1]}")
                    return False
            else:
                print(f"Sell condition not met. Buy Price: {buy_price}, Current Price: {df['close'].iloc[-1]}")
            return False

        # Buy condition check
        if buy_condition() and free_usdt_balance > 1:
            if testing_mode:
                print(f"{Fore.GREEN}Simulating Buy Order{Style.RESET_ALL}")
                print(f"{Fore.GREEN}Simulated Buy Price: {df['close'].iloc[-1]}{Style.RESET_ALL}")  # Print the simulated buy price
                with open("buy_price.json", "w") as buy_price_file:
                    json.dump(df['close'].iloc[-1], buy_price_file)
            else:
                # Retrieve symbol info for 'Symbol'
                symbol_info = client.get_symbol_info('SOLUSDT')

                # Find the 'LOT_SIZE' filter
                lot_size_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'), None)

                if lot_size_filter:
                    # Extract the step size and precision from the filter
                    quantity_step_size = float(lot_size_filter['stepSize'])
                    max_precision = len(lot_size_filter['minQty'].split('.')[1])

                    # Calculate the quantity based on available USDT balance
                    solusdt_ticker = client.get_symbol_ticker(symbol='SOLUSDT')
                    current_sol_price = float(solusdt_ticker['price'])
                    usdt_balance = float(client.get_asset_balance(asset='USDT')['free'])
                    quantity_to_buy = usdt_balance / current_sol_price

                    # Ensure the quantity adheres to Binance's rules for step size
                    quantity_to_buy -= quantity_to_buy % quantity_step_size

                    # Adjust the quantity to match the maximum allowed precision
                    quantity_to_buy = round(quantity_to_buy, max_precision)

                    # Check if the notional value meets the minimum requirement
                    notional_value = quantity_to_buy * current_sol_price
                    if notional_value >= min_notional:
                        print("Executing Buy Order")
                        # Place a real buy order here
                        order = client.create_order(
                            symbol=symbol,
                            side='BUY',
                            type='MARKET',
                            quantity=quantity_to_buy
                        )
                        print(f"{Fore.GREEN}Executing Buy Order: {order}{Style.RESET_ALL}")
                        print(f"{Fore.GREEN}Buy Order Executed at Price: {order['fills'][0]['price']}{Style.RESET_ALL}")
                        buy_price = order['fills'][0]['price']  # Store the real buy price
                        with open("buy_price.json", "w") as buy_price_file:
                            json.dump(buy_price, buy_price_file)
                    else:
                        print(f"{Fore.RED}Order Notional Value is too low: {notional_value} < {min_notional}{Style.RESET_ALL}")
                else:
                    print("LOT_SIZE filter not found in symbol info.")

        # Sell condition check
        if sell_condition():
            sol_balance = float(client.get_asset_balance(asset='SOL')['free'])
            if sol_balance > 0:
                if testing_mode:
                    print(f"{Fore.RED}Simulating Sell Order{Style.RESET_ALL}")
                    print(f"{Fore.RED}Simulated Sell Price: {df['close'].iloc[-1]}{Style.RESET_ALL}")  # Print the simulated sell price
                    buy_price = None  # Reset the buy price after selling
                    with open("buy_price.json", "w") as buy_price_file:
                        json.dump(buy_price, buy_price_file)
                else:
                    # Retrieve symbol info for 'Symbol'
                    symbol_info = client.get_symbol_info('SOLUSDT')

                    # Find the 'LOT_SIZE' filter
                    lot_size_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'), None)

                    if lot_size_filter:
                        # Extract the step size and precision from the filter
                        quantity_step_size = float(lot_size_filter['stepSize'])
                        max_precision = len(lot_size_filter['minQty'].split('.')[1])

                        # Calculate the quantity based on available balance
                        sol_balance = float(client.get_asset_balance(asset='SOL')['free'])
                        quantity_to_sell = sol_balance

                        # Ensure the quantity adheres to Binance's rules for step size
                        quantity_to_sell -= quantity_to_sell % quantity_step_size

                        # Adjust the quantity to match the maximum allowed precision
                        quantity_to_sell = round(quantity_to_sell, max_precision)

                        # Check if the notional value meets the minimum requirement
                        notional_value = quantity_to_sell * df['close'].iloc[-1]
                        if notional_value >= min_notional:
                            print(f"{Fore.RED}Executing Sell Order{Style.RESET_ALL}")
                            # Place a real sell order here
                            order = client.create_order(
                                symbol=symbol,
                                side='SELL',
                                type='MARKET',
                                quantity=quantity_to_sell
                            )
                            print(f"{Fore.RED}Sell Order Executed: {order}{Style.RESET_ALL}")
                            print(f"{Fore.RED}Sell Order Executed at Price: {order['fills'][0]['price']}{Style.RESET_ALL}")
                            sell_price = order['fills'][0]['price']  # Store the real sell price
                            with open("sell_price.json", "w") as sell_price_file:
                                json.dump(sell_price, sell_price_file)
                            buy_price = None  # Reset the buy price after selling
                            with open("buy_price.json", "w") as buy_price_file:
                                json.dump(buy_price, buy_price_file)
                        else:
                            print(f"{Fore.RED}Order Notional Value is too low: {notional_value} < {min_notional}{Style.RESET_ALL}")
                    else:
                        print("LOT_SIZE filter not found in symbol info.")

        # Sleep for a while (you can adjust the interval)
        print(f"{Fore.BLUE}Sleeping for 1 seconds{Style.RESET_ALL}")
        time.sleep(1)  # Sleep for 1 seconds

    except Exception as e:
        print("Error:", e)








