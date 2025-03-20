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

buy_price = None
sell_price = None
testing_mode = False
historical_data = []
buy_order_id = None
sell_order_id = None

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

while True:
    try:
        klines = client.get_klines(symbol=symbol, interval=timeframe, limit=100)
        historical_data = klines

        df = pd.DataFrame(historical_data, columns=[
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

        if os.path.exists("buy_price.json"):
            try:
                with open("buy_price.json", "r") as buy_price_file:
                    buy_price = json.load(buy_price_file)
                    print(f"Buy Price: {buy_price}")
            except (json.JSONDecodeError, IOError) as e:
                print(f"Error reading buy_price.json: {e}")
                buy_price = None
        if os.path.exists("sell_price.json"):
            try:
                with open("sell_price.json", "r") as sell_price_file:
                    sell_price = json.load(sell_price_file)
                    print(f"Sell Price: {sell_price}")
            except (json.JSONDecodeError, IOError) as e:
                print(f"Error reading sell_price.json: {e}")
                sell_price = None

        def bollinger_bands_strategy(df, window=BB_WINDOW, num_std_dev=BB_STD_DEV):
            df['rolling_mean'] = df['close'].rolling(window=window).mean()
            df['rolling_std'] = df['close'].rolling(window=window).std()
            df['upper_band'] = df['rolling_mean'] + (df['rolling_std'] * num_std_dev)
            df['lower_band'] = df['rolling_mean'] - (df['rolling_std'] * num_std_dev)
            df['signal'] = 0
            df.loc[(df['close'] <= df['lower_band']) & (df['close'] < df['rolling_mean']), 'signal'] = 1
            return df

        df = bollinger_bands_strategy(df)

        free_usdt_balance = float(client.get_asset_balance(asset='USDT')['free'])

        def buy_condition():
            if df['close'].iloc[-1] <= BUY_FACTOR * df['lower_band'].iloc[-1]:
                return True
            else:
                print(f"Target Buy Price: {BUY_FACTOR * df['lower_band'].iloc[-1]}")
                print("Buy condition not met")
                return False

        if sell_order_id is not None:
            try:
                order = client.get_order(symbol=symbol, orderId=sell_order_id)
                status = order['status']
                if status == 'FILLED':
                    avg_sell_price = get_average_fill_price(order)
                    print(f"{Fore.RED}Sell order filled at {avg_sell_price}{Style.RESET_ALL}")
                    try:
                        with open("sell_price.json", "w") as sell_price_file:
                            json.dump(avg_sell_price, sell_price_file)
                    except IOError as e:
                        print(f"Error writing sell_price.json: {e}")
                    sell_order_id = None
                    buy_price = None
                    try:
                        with open("buy_price.json", "w") as buy_price_file:
                            json.dump(buy_price, buy_price_file)
                    except IOError as e:
                        print(f"Error writing buy_price.json: {e}")
                elif status not in ['NEW', 'PARTIALLY_FILLED']:
                    print(f"Sell order {sell_order_id} no longer active: {status}")
                    sell_order_id = None
            except Exception as e:
                print(f"Error checking sell order: {e}")
                sell_order_id = None

        elif buy_order_id is not None:
            try:
                order = client.get_order(symbol=symbol, orderId=buy_order_id)
                status = order['status']
                if status == 'FILLED':
                    executed_qty_str = order['executedQty']
                    executed_qty_dec = Decimal(executed_qty_str)
                    avg_buy_price = get_average_fill_price(order)
                    print(f"{Fore.GREEN}Buy order filled at {avg_buy_price}{Style.RESET_ALL}")
                    buy_price = avg_buy_price
                    try:
                        with open("buy_price.json", "w") as buy_price_file:
                            json.dump(buy_price, buy_price_file)
                    except IOError as e:
                        print(f"Error writing buy_price.json: {e}")

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

                    quantity = executed_qty_dec.quantize(step_dec, rounding=ROUND_DOWN)
                    print(f"Calculated sell quantity: {quantity}, min_qty: {min_qty}, step_size: {step_dec}")

                    if quantity < min_qty:
                        print("Sell quantity too small, cannot place order")
                        buy_order_id = None
                        continue

                    asset = symbol.replace('USDT', '')
                    asset_balance = Decimal(client.get_asset_balance(asset=asset)['free'])
                    print(f"Available {asset} balance: {asset_balance}")
                    if asset_balance < quantity:
                        print(f"Insufficient {asset} balance for sell order: {asset_balance} < {quantity}")
                        buy_order_id = None
                        continue

                    qty_str = f"{quantity:.{max_precision}f}"

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
            except Exception as e:
                print(f"Error checking buy order: {e}")
                buy_order_id = None

        elif buy_condition() and sell_order_id is None and buy_order_id is None:
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

            max_quantity_dec = usdt_balance_dec / limit_price_dec
            quantity_to_buy_dec = (max_quantity_dec // step_dec) * step_dec

            if quantity_to_buy_dec < min_qty_dec:
                print("Quantity too small, cannot place order")
                continue

            qty_str = f"{quantity_to_buy_dec:.{max_precision}f}"

            if testing_mode:
                print(f"{Fore.GREEN}Simulating Limit Buy Order{Style.RESET_ALL}")
                simulated_buy_price = limit_price
                print(f"{Fore.GREEN}Simulated Buy Price: {simulated_buy_price}{Style.RESET_ALL}")
                buy_price = simulated_buy_price
                try:
                    with open("buy_price.json", "w") as buy_price_file:
                        json.dump(buy_price, buy_price_file)
                except IOError as e:
                    print(f"Error writing buy_price.json: {e}")
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
