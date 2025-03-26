import os
import time
import pandas as pd
from binance.client import Client
from colorama import init, Fore, Style
import json
from dotenv import load_dotenv
from decimal import Decimal, ROUND_DOWN

load_dotenv()
init()

API_KEY = os.environ.get('API_KEY')
API_SECRET = os.environ.get('API_SECRET')
if not API_KEY or not API_SECRET:
    raise ValueError("Please set BINANCE_API_KEY and BINANCE_API_SECRET environment variables.")

client = Client(API_KEY, API_SECRET, testnet=True)

symbol = 'SOLUSDT'
timeframe = '15m'
BB_WINDOW = 10
BB_STD_DEV = 1
BUY_FACTOR = 0.99
SELL_MULTIPLIER = 1.012

have_position = False
holding_quantity = Decimal('0')
buy_price = None
sell_price_target = None

def get_average_fill_price(order):
    fills = order.get('fills', [])
    if not fills:
        return 0
    total_qty = Decimal('0')
    total_cost = Decimal('0')
    for fill in fills:
        qty = Decimal(fill['qty'])
        price = Decimal(fill['price'])
        total_qty += qty
        total_cost += qty * price
    return float(total_cost / total_qty) if total_qty > 0 else 0

# Load state from files at startup
if os.path.exists("buy_price.json"):
    try:
        with open("buy_price.json", "r") as buy_price_file:
            buy_price = json.load(buy_price_file)
            if buy_price is not None:
                have_position = True
                sell_price_target = buy_price * SELL_MULTIPLIER
                print(f"Loaded Buy Price: {buy_price}, Sell Target: {sell_price_target}")
    except (json.JSONDecodeError, IOError) as e:
        print(f"Error reading buy_price.json: {e}")
        buy_price = None
if os.path.exists("sell_price.json") and have_position:
    try:
        with open("sell_price.json", "r") as sell_price_file:
            sell_price = json.load(sell_price_file)
            if sell_price is not None:
                print(f"Loaded Sell Price: {sell_price}")
    except (json.JSONDecodeError, IOError) as e:
        print(f"Error reading sell_price.json: {e}")

while True:
    try:
        # Fetch historical data for Bollinger Bands
        klines = client.get_klines(symbol=symbol, interval=timeframe, limit=100)
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

        # Calculate Bollinger Bands
        df['rolling_mean'] = df['close'].rolling(window=BB_WINDOW).mean()
        df['rolling_std'] = df['close'].rolling(window=BB_WINDOW).std()
        df['upper_band'] = df['rolling_mean'] + (df['rolling_std'] * BB_STD_DEV)
        df['lower_band'] = df['rolling_mean'] - (df['rolling_std'] * BB_STD_DEV)

        # Get current market price
        current_price = float(client.get_symbol_ticker(symbol=symbol)['price'])
        print(f"Current Price: {current_price}")

        # Get symbol info for filters
        symbol_info = client.get_symbol_info(symbol)
        lot_size_filter = next(f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE')
        step_size_str = lot_size_filter['stepSize']
        step_dec = Decimal(step_size_str)
        min_qty_dec = Decimal(lot_size_filter['minQty'])
        max_precision = len(step_size_str.split('.')[1])

        # Sell logic
        if have_position:
            if current_price >= sell_price_target:
                qty_str = f"{holding_quantity:.{max_precision}f}"
                try:
                    sell_order = client.create_order(
                        symbol=symbol,
                        side='SELL',
                        type='MARKET',
                        quantity=qty_str
                    )
                    avg_sell_price = get_average_fill_price(sell_order)
                    print(f"{Fore.RED}Market sell order filled at {avg_sell_price}{Style.RESET_ALL}")
                    with open("sell_price.json", "w") as sell_price_file:
                        json.dump(avg_sell_price, sell_price_file)
                    # Reset state
                    have_position = False
                    holding_quantity = Decimal('0')
                    buy_price = None
                    sell_price_target = None
                    with open("buy_price.json", "w") as buy_price_file:
                        json.dump(None, buy_price_file)
                except Exception as e:
                    print(f"Error placing sell order: {e}")
            else:
                print(f"Waiting to sell at {sell_price_target}, current price: {current_price}")

        # Buy logic
        elif current_price <= BUY_FACTOR * df['lower_band'].iloc[-1]:
            usdt_balance_str = client.get_asset_balance(asset='USDT')['free']
            usdt_balance_dec = Decimal(usdt_balance_str)
            quantity_to_buy_dec = usdt_balance_dec / Decimal(str(current_price))
            quantity_to_buy_dec = (quantity_to_buy_dec // step_dec) * step_dec

            if quantity_to_buy_dec < min_qty_dec:
                print(f"Quantity too small: {quantity_to_buy_dec} < {min_qty_dec}")
                continue

            min_usdt = min_qty_dec * Decimal(str(current_price))
            if usdt_balance_dec < min_usdt:
                print(f"Insufficient USDT balance: {usdt_balance_dec} < {min_usdt}")
                continue

            qty_str = f"{quantity_to_buy_dec:.{max_precision}f}"
            try:
                buy_order = client.create_order(
                    symbol=symbol,
                    side='BUY',
                    type='MARKET',
                    quantity=qty_str
                )
                avg_buy_price = get_average_fill_price(buy_order)
                executed_qty = Decimal(buy_order['executedQty'])
                print(f"{Fore.GREEN}Market buy order filled at {avg_buy_price}, quantity: {executed_qty}{Style.RESET_ALL}")
                # Set state
                have_position = True
                holding_quantity = executed_qty
                buy_price = avg_buy_price
                sell_price_target = buy_price * SELL_MULTIPLIER
                with open("buy_price.json", "w") as buy_price_file:
                    json.dump(buy_price, buy_price_file)
                print(f"Set sell target at {sell_price_target}")
            except Exception as e:
                print(f"Error placing buy order: {e}")
        else:
            print(f"Target Buy Price: {BUY_FACTOR * df['lower_band'].iloc[-1]}, current price: {current_price}")

        print(f"{Fore.BLUE}Sleeping for 1 second{Style.RESET_ALL}")
        time.sleep(1)

    except Exception as e:
        print(f"Error: {e}")
        time.sleep(1)
