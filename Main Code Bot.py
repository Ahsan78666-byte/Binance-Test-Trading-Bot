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

if not API_KEY or not API_SECRET:
    raise ValueError("Please set BINANCE_API_KEY and BINANCE_API_SECRET environment variables.")

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

# Initialize sell_order_id to track the limit sell order
sell_order_id = None

# Function to calculate average fill price from order fills
def get_average_fill_price(order):
    fills = order.get('fills', [])
    if not fills:
        return 0
    total_qty = 0
    total_cost = 0
    for fill in fills:
        qty = float(fill['qty'])
        price = float(fill['price'])
        total_qty += qty
        total_cost += qty * price
    return total_cost / total_qty if total_qty > 0 else 0

while True:
    try:
        # Fetch candlestick data for the trading pair
        klines = client.get_klines(symbol=symbol, interval=timeframe, limit=60)  # Fetch previous candles

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
        def bollinger_bands_strategy(df, window=10, num_std_dev=1):
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

        # Buy condition
        def buy_condition():
            if df['close'].iloc[-1] <= 0.985 * df['lower_band'].iloc[-1]:
                return True
            else:
                print(f"Wick Condition: {0.985 * df['lower_band'].iloc[-1]}")
                print("Buy condition not met")
            return False

        # Check sell order status if one exists
        if sell_order_id is not None:
            try:
                order = client.get_order(symbol=symbol, orderId=sell_order_id)
                status = order['status']
                if status == 'FILLED':
                    avg_sell_price = get_average_fill_price(order)
                    print(f"{Fore.RED}Sell order filled at {avg_sell_price}{Style.RESET_ALL}")
                    with open("sell_price.json", "w") as sell_price_file:
                        json.dump(avg_sell_price, sell_price_file)
                    sell_order_id = None
                    buy_price = None
                    with open("buy_price.json", "w") as buy_price_file:
                        json.dump(buy_price, buy_price_file)
                elif status not in ['NEW', 'PARTIALLY_FILLED']:
                    print(f"Sell order {sell_order_id} no longer active: {status}")
                    sell_order_id = None
            except Exception as e:
                print(f"Error checking sell order: {e}")
                sell_order_id = None

        # Buy condition check and place sell limit order immediately after
        elif buy_condition() and free_usdt_balance > 1 and sell_order_id is None:
            if testing_mode:
                print(f"{Fore.GREEN}Simulating Buy Order{Style.RESET_ALL}")
                simulated_buy_price = df['close'].iloc[-1]
                print(f"{Fore.GREEN}Simulated Buy Price: {simulated_buy_price}{Style.RESET_ALL}")
                buy_price = simulated_buy_price
                with open("buy_price.json", "w") as buy_price_file:
                    json.dump(buy_price, buy_price_file)
                # Simulate placing sell limit order
                sell_price_target = buy_price * 1.012
                print(f"{Fore.RED}Simulating placing sell limit order at {sell_price_target}{Style.RESET_ALL}")
            else:
                # Retrieve symbol info for 'Symbol'
                symbol_info = client.get_symbol_info('SOLUSDT')

                # Find the 'LOT_SIZE' filter
                lot_size_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'), None)
                if lot_size_filter:
                    # Extract the step size and precision from the filter
                    quantity_step_size = float(lot_size_filter['stepSize'])
                    max_precision = len(lot_size_filter['stepSize'].split('.')[1])

                    # Get the current price of SOLUSDT
                    solusdt_ticker = client.get_symbol_ticker(symbol='SOLUSDT')
                    current_sol_price = float(solusdt_ticker['price'])

                    # Get the available USDT balance
                    usdt_balance = float(client.get_asset_balance(asset='USDT')['free'])

                    # Calculate the maximum quantity you can buy with the available USDT balance
                    max_quantity = usdt_balance / current_sol_price

                    # Round down to the nearest valid multiple of stepSize
                    quantity_to_buy = (max_quantity // quantity_step_size) * quantity_step_size

                    # Adjust the quantity to match the maximum allowed precision
                    quantity_to_buy = round(quantity_to_buy, max_precision)

                    # Recalculate the actual USDT amount that will be spent
                    usdt_spent = quantity_to_buy * current_sol_price

                    # Ensure the calculated USDT spent does not exceed the available balance
                    if usdt_spent > usdt_balance:
                        raise ValueError("Calculated USDT spent exceeds available balance. Adjust logic.")

                    print(f"Executing Buy Order: Quantity={quantity_to_buy}, Price={current_sol_price}, Total USDT Spent={usdt_spent}")

                    # Place a real buy order
                    order = client.create_order(
                        symbol=symbol,
                        side='BUY',
                        type='MARKET',
                        quantity=quantity_to_buy
                    )
                    print(f"{Fore.GREEN}Executing Buy Order: {order}{Style.RESET_ALL}")
                    if order['status'] == 'FILLED':
                        executed_qty = float(order['executedQty'])
                        avg_buy_price = get_average_fill_price(order)
                        print(f"{Fore.GREEN}Buy Order Executed at Price: {avg_buy_price}{Style.RESET_ALL}")
                        buy_price = avg_buy_price
                        with open("buy_price.json", "w") as buy_price_file:
                            json.dump(buy_price, buy_price_file)

                        # Calculate sell price for 1.2% profit
                        sell_price_target = avg_buy_price * 1.012
                        price_filter = next(f for f in symbol_info['filters'] if f['filterType'] == 'PRICE_FILTER')
                        tick_size = float(price_filter['tickSize'])
                        sell_price = round(sell_price_target / tick_size) * tick_size
                        sell_price_str = f"{sell_price:.{len(str(tick_size).split('.')[1])}f}"

                        # Format quantity for sell order
                        qty_str = f"{executed_qty:.{max_precision}f}"

                        # Place limit sell order
                        sell_order = client.create_order(
                            symbol=symbol,
                            side='SELL',
                            type='LIMIT',
                            timeInForce='GTC',
                            quantity=qty_str,
                            price=sell_price_str
                        )
                        sell_order_id = sell_order['orderId']
                        print(f"{Fore.RED}Placed sell limit order {sell_order_id} at {sell_price_str}{Style.RESET_ALL}")
                    else:
                        print(f"Buy order not filled immediately: status {order['status']}")
                else:
                    print("LOT_SIZE filter not found in symbol info.")

        # Sleep for a while (you can adjust the interval)
        print(f"{Fore.BLUE}Sleeping for 1 seconds{Style.RESET_ALL}")
        time.sleep(1)  # Sleep for 1 seconds

    except Exception as e:
        print("Error:", e)



