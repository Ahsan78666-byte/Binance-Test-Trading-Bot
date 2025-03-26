import pandas as pd
from binance.client import Client
import json
import os
from decimal import Decimal, ROUND_DOWN
from colorama import Fore, Style
import time

# API Keys (replace with your actual keys or use environment variables)
API_KEY = 'your_api_key'
API_SECRET = 'your_api_secret'

# Symbol and trading parameters
symbol = 'BTCUSDT'
BB_WINDOW = 20          # Bollinger Bands window
BB_STD_DEV = 2          # Number of standard deviations
BUY_FACTOR = 0.99       # Factor to adjust buy price below lower band
SELL_MULTIPLIER = 1.01  # Multiplier for sell price above buy price
testing_mode = False    # Set to True for simulation without real trades

# Initialize Binance client
client = Client(API_KEY, API_SECRET)

# Function to fetch historical klines data
def fetch_historical_data(symbol, interval='1m', limit=1000):
    """Fetch historical price data from Binance."""
    klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(klines, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time',
        'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume',
        'taker_buy_quote_asset_volume', 'ignore'
    ])
    numeric_columns = ['open', 'high', 'low', 'close', 'volume', 'close_time',
                       'quote_asset_volume', 'number_of_trades',
                       'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume']
    df[numeric_columns] = df[numeric_columns].apply(pd.to_numeric, errors='coerce')
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    return df

# Bollinger Bands strategy
def bollinger_bands_strategy(df, window=BB_WINDOW, num_std_dev=BB_STD_DEV):
    """Calculate Bollinger Bands and generate trading signals."""
    df['rolling_mean'] = df['close'].rolling(window=window).mean()
    df['rolling_std'] = df['close'].rolling(window=window).std()
    df['upper_band'] = df['rolling_mean'] + (df['rolling_std'] * num_std_dev)
    df['lower_band'] = df['rolling_mean'] - (df['rolling_std'] * num_std_dev)
    df['signal'] = 0
    df.loc[(df['close'] <= df['lower_band']) & (df['close'] < df['rolling_mean']), 'signal'] = 1
    return df

# Function to get average fill price from an order
def get_average_fill_price(order):
    """Calculate the average price from order fills."""
    fills = order['fills']
    total_qty = Decimal('0')
    total_cost = Decimal('0')
    for fill in fills:
        price = Decimal(fill['price'])
        qty = Decimal(fill['qty'])
        total_qty += qty
        total_cost += price * qty
    if total_qty > 0:
        return float(total_cost / total_qty)
    return None

# Buy condition function
def buy_condition(df):
    """Check if the current price meets the buy condition."""
    target_price = BUY_FACTOR * df['lower_band'].iloc[-1]
    if df['close'].iloc[-1] <= target_price:
        return True
    else:
        print(f"Target Buy Price: {target_price}")
        print("Buy condition not met")
        return False

# Initialize order IDs
buy_order_id = None
sell_order_id = None

# Main trading loop
while True:
    try:
        # Fetch latest data and apply strategy
        df = fetch_historical_data(symbol)
        df = bollinger_bands_strategy(df)

        # Check if there's an active sell order
        if sell_order_id is not None:
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

        # Check if there's an active buy order
        elif buy_order_id is not None:
            order = client.get_order(symbol=symbol, orderId=buy_order_id)
            status = order['status']
            if status == 'FILLED':
                executed_qty_str = order['executedQty']
                executed_qty_dec = Decimal(executed_qty_str)
                avg_buy_price = get_average_fill_price(order)
                print(f"{Fore.GREEN}Buy order filled at {avg_buy_price}{Style.RESET_ALL}")
                buy_price = avg_buy_price
                with open("buy_price.json", "w") as buy_price_file:
                    json.dump(buy_price, buy_price_file)

                # Place sell order
                symbol_info = client.get_symbol_info(symbol)
                price_filter = next(f for f in symbol_info['filters'] if f['filterType'] == 'PRICE_FILTER')
                tick_size = float(price_filter['tickSize'])
                sell_price_target = avg_buy_price * SELL_MULTIPLIER
                sell_price = round(sell_price_target / tick_size) * tick_size
                price_precision = len(str(tick_size).split('.')[1])
                sell_price_str = f"{sell_price:.{price_precision}f}"

                lot_size_filter = next(f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE')
                step_size_str = lot_size_filter['stepSize']
                step_dec = Decimal(step_size_str)
                min_qty = Decimal(lot_size_filter['minQty'])
                max_precision = len(step_size_str.split('.')[1])

                asset = symbol.replace('USDT', '')
                asset_balance = Decimal(client.get_asset_balance(asset=asset)['free'])
                # Ensure sell quantity matches available balance and step size
                quantity_to_sell = min(executed_qty_dec, asset_balance).quantize(step_dec, rounding=ROUND_DOWN)
                if quantity_to_sell < min_qty:
                    print("Sell quantity too small, cannot place order")
                    buy_order_id = None
                    continue
                qty_str = f"{quantity_to_sell:.{max_precision}f}"

                try:
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
                except Exception as e:
                    print(f"Error placing sell order: {e}")
                    sell_order_id = None

                buy_order_id = None
            elif status not in ['NEW', 'PARTIALLY_FILLED']:
                print(f"Buy order {buy_order_id} no longer active: {status}")
                buy_order_id = None

        # Check for buy condition and place order
        elif buy_condition(df) and sell_order_id is None and buy_order_id is None:
            usdt_balance_str = client.get_asset_balance(asset='USDT')['free']
            usdt_balance_dec = Decimal(usdt_balance_str)

            symbol_info = client.get_symbol_info(symbol)
            price_filter = next(f for f in symbol_info['filters'] if f['filterType'] == 'PRICE_FILTER')
            tick_size = float(price_filter['tickSize'])
            price_precision = len(str(tick_size).split('.')[1])

            lower_band = df['lower_band'].iloc[-1]
            limit_price = BUY_FACTOR * lower_band
            limit_price = round(limit_price / tick_size) * tick_size
            limit_price_str = f"{limit_price:.{price_precision}f}"
            limit_price_dec = Decimal(limit_price_str)

            lot_size_filter = next(f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE')
            step_size_str = lot_size_filter['stepSize']
            step_dec = Decimal(step_size_str)
            min_qty_dec = Decimal(lot_size_filter['minQty'])
            max_precision = len(step_size_str.split('.')[1])

            min_usdt = min_qty_dec * limit_price_dec
            if usdt_balance_dec < min_usdt:
                print(f"Insufficient USDT balance: {usdt_balance_dec} < {min_usdt}")
                continue

            # Maximize USDT usage for buy order
            max_quantity_dec = (usdt_balance_dec / limit_price_dec).quantize(step_dec, rounding=ROUND_DOWN)
            if max_quantity_dec < min_qty_dec:
                print("Quantity too small, cannot place order")
                continue
            qty_str = f"{max_quantity_dec:.{max_precision}f}"

            if testing_mode:
                print(f"{Fore.GREEN}Simulating Limit Buy Order{Style.RESET_ALL}")
                simulated_buy_price = limit_price
                print(f"{Fore.GREEN}Simulated Buy Price: {simulated_buy_price}{Style.RESET_ALL}")
                buy_price = simulated_buy_price
                with open("buy_price.json", "w") as buy_price_file:
                    json.dump(buy_price, buy_price_file)
                sell_price_target = buy_price * SELL_MULTIPLIER
                print(f"{Fore.RED}Simulating placing sell limit order at {sell_price_target}{Style.RESET_ALL}")
            else:
                try:
                    buy_order = client.create_order(
                        symbol=symbol,
                        side='BUY',
                        type='LIMIT',
                        timeInForce='GTC',
                        quantity=qty_str,
                        price=limit_price_str
                    )
                    buy_order_id = buy_order['orderId']
                    print(f"{Fore.GREEN}Placed limit buy order {buy_order_id} at {limit_price_str}{Style.RESET_ALL}")
                except Exception as e:
                    print(f"Error placing buy order: {e}")
                    buy_order_id = None

        print(f"{Fore.BLUE}Sleeping for 1 seconds{Style.RESET_ALL}")
        time.sleep(1)
    except Exception as e:
        print(f"Error: {e}")
        time.sleep(1)
