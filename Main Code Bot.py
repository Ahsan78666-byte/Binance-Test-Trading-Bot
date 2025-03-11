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

# Define the trading pair and timeframe
symbol = 'SOLUSDT'
timeframe = '15m'

# Initialize variables
buy_price = None
sell_price = None
testing_mode = False
historical_data = []
buy_order_id = None  # To track limit buy order
sell_order_id = None  # To track limit sell order

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

# Main trading loop
while True:
    try:
        # Fetch candlestick data
        klines = client.get_klines(symbol=symbol, interval=timeframe, limit=100)
        historical_data = klines

        # Convert to DataFrame
        df = pd.DataFrame(historical_data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
        numeric_columns = ['open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume']
        df[numeric_columns] = df[numeric_columns].apply(pd.to_numeric, errors='coerce')
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)

        # Load buy and sell prices if they exist
        if os.path.exists("buy_price.json"):
            with open("buy_price.json", "r") as buy_price_file:
                buy_price = json.load(buy_price_file)
                print(f"Buy Price: {buy_price}")
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
            df['signal'] = 0
            df.loc[(df['close'] <= df['lower_band']) & (df['close'] < df['rolling_mean']), 'signal'] = 1
            return df

        df = bollinger_bands_strategy(df)

        # Get free USDT balance
        free_usdt_balance = float(client.get_asset_balance(asset='USDT')['free'])

        # Define buy condition
        def buy_condition():
            if df['close'].iloc[-1] <= 0.985 * df['lower_band'].iloc[-1]:
                return True
            else:
                print(f"Target Buy Price: {0.985 * df['lower_band'].iloc[-1]}")
                print("Buy condition not met")
                return False

        # Check sell order status
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

        # Check buy order status
        elif buy_order_id is not None:
            try:
                order = client.get_order(symbol=symbol, orderId=buy_order_id)
                status = order['status']
                if status == 'FILLED':
                    executed_qty = float(order['executedQty'])
                    avg_buy_price = get_average_fill_price(order)
                    print(f"{Fore.GREEN}Buy order filled at {avg_buy_price}{Style.RESET_ALL}")
                    buy_price = avg_buy_price
                    with open("buy_price.json", "w") as buy_price_file:
                        json.dump(buy_price, buy_price_file)

                    # Place sell limit order
                    symbol_info = client.get_symbol_info(symbol)
                    price_filter = next(f for f in symbol_info['filters'] if f['filterType'] == 'PRICE_FILTER')
                    tick_size = float(price_filter['tickSize'])
                    sell_price_target = avg_buy_price * 1.012
                    sell_price = round(sell_price_target / tick_size) * tick_size
                    price_precision = len(str(tick_size).split('.')[1])
                    sell_price_str = f"{sell_price:.{price_precision}f}"
                    lot_size_filter = next(f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE')
                    max_precision = len(lot_size_filter['stepSize'].split('.')[1])
                    qty_str = f"{executed_qty:.{max_precision}f}"
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
                    buy_order_id = None
                elif status not in ['NEW', 'PARTIALLY_FILLED']:
                    print(f"Buy order {buy_order_id} no longer active: {status}")
                    buy_order_id = None
            except Exception as e:
                print(f"Error checking buy order: {e}")
                buy_order_id = None

        # Place limit buy order if conditions are met
        elif buy_condition() and free_usdt_balance > 1 and sell_order_id is None and buy_order_id is None:
            if testing_mode:
                print(f"{Fore.GREEN}Simulating Limit Buy Order{Style.RESET_ALL}")
                simulated_buy_price = 0.985 * df['lower_band'].iloc[-1]
                print(f"{Fore.GREEN}Simulated Buy Price: {simulated_buy_price}{Style.RESET_ALL}")
                buy_price = simulated_buy_price
                with open("buy_price.json", "w") as buy_price_file:
                    json.dump(buy_price, buy_price_file)
                sell_price_target = buy_price * 1.012
                print(f"{Fore.RED}Simulating placing sell limit order at {sell_price_target}{Style.RESET_ALL}")
            else:
                # Get symbol info for precision rules
                symbol_info = client.get_symbol_info(symbol)
                price_filter = next(f for f in symbol_info['filters'] if f['filterType'] == 'PRICE_FILTER')
                tick_size = float(price_filter['tickSize'])
                price_precision = len(str(tick_size).split('.')[1])

                # Calculate limit price
                lower_band = df['lower_band'].iloc[-1]
                limit_price = 0.985 * lower_band  # Target price as per strategy
                limit_price = round(limit_price / tick_size) * tick_size
                limit_price_str = f"{limit_price:.{price_precision}f}"

                # Calculate quantity
                lot_size_filter = next(f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE')
                step_size = float(lot_size_filter['stepSize'])
                max_precision = len(lot_size_filter['stepSize'].split('.')[1])
                usdt_balance = float(client.get_asset_balance(asset='USDT')['free'])
                max_quantity = usdt_balance / limit_price
                quantity_to_buy = (max_quantity // step_size) * step_size
                quantity_to_buy = round(quantity_to_buy, max_precision)
                min_qty = float(lot_size_filter['minQty'])
                if quantity_to_buy < min_qty:
                    print("Quantity too small, cannot place order")
                    continue

                # Place limit buy order
                buy_order = client.create_order(
                    symbol=symbol,
                    side='BUY',
                    type='LIMIT',
                    timeInForce='GTC',  # Good Till Cancelled
                    quantity=quantity_to_buy,
                    price=limit_price_str
                )
                buy_order_id = buy_order['orderId']
                print(f"{Fore.GREEN}Placed limit buy order {buy_order_id} at {limit_price_str}{Style.RESET_ALL}")

        # Sleep for 1 second
        print(f"{Fore.BLUE}Sleeping for 1 second{Style.RESET_ALL}")
        time.sleep(1)

    except Exception as e:
        print(f"Error: {e}")
        time.sleep(1)  # Prevent rapid error looping


