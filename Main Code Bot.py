import os
import time
import pandas as pd
from binance.client import Client
from colorama import init, Fore, Style
import json
from dotenv import load_dotenv

# Initialize environment and Binance client
load_dotenv()
init()

API_KEY = os.environ.get('API_KEY')
API_SECRET = os.environ.get('API_SECRET')

client = Client(API_KEY, API_SECRET, testnet=True)

if not API_KEY or not API_SECRET:
    raise ValueError("Please set API_KEY and API_SECRET in your .env file.")

# Trading parameters
symbol = 'SOLUSDT'
timeframe = '15m'
testing_mode = False
buy_order_id = None
sell_order_id = None

# Function to calculate average fill price
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

# Bollinger Bands calculation
def bollinger_bands_strategy(df, window=10, num_std_dev=1):
    df['rolling_mean'] = df['close'].rolling(window=window).mean()
    df['rolling_std'] = df['close'].rolling(window=window).std()
    df['upper_band'] = df['rolling_mean'] + (df['rolling_std'] * num_std_dev)
    df['lower_band'] = df['rolling_mean'] - (df['rolling_std'] * num_std_dev)
    return df

# Buy condition
def buy_condition():
    return df['close'].iloc[-1] <= 0.985 * df['lower_band'].iloc[-1]

# Main trading loop
while True:
    try:
        # Fetch klines and set up DataFrame
        klines = client.get_klines(symbol=symbol, interval=timeframe, limit=100)
        df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
        numeric_columns = ['open', 'high', 'low', 'close', 'volume']
        df[numeric_columns] = df[numeric_columns].apply(pd.to_numeric, errors='coerce')
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        
        # Apply Bollinger Bands
        df = bollinger_bands_strategy(df)
        
        # Get free USDT balance
        free_usdt_balance = float(client.get_asset_balance(asset='USDT')['free'])
        
        # Check buy order status
        if buy_order_id is not None:
            order = client.get_order(symbol=symbol, orderId=buy_order_id)
            status = order['status']
            if status == 'FILLED':
                executed_qty = float(order['executedQty'])
                if executed_qty > 0:
                    avg_buy_price = get_average_fill_price(order)
                    print(f"{Fore.GREEN}Buy order filled at {avg_buy_price} for {executed_qty} SOL{Style.RESET_ALL}")
                    with open("buy_price.json", "w") as buy_price_file:
                        json.dump(avg_buy_price, buy_price_file)
                    
                    # Calculate sell price for 1.2% profit
                    sell_price = avg_buy_price * 1.012
                    symbol_info = client.get_symbol_info(symbol)
                    price_filter = next(f for f in symbol_info['filters'] if f['filterType'] == 'PRICE_FILTER')
                    tick_size = float(price_filter['tickSize'])
                    sell_price = round(sell_price / tick_size) * tick_size
                    sell_price_str = f"{sell_price:.{len(str(tick_size).split('.')[1])}f}"
                    
                    # Use executed quantity for sell order
                    lot_size_filter = next(f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE')
                    step_size = float(lot_size_filter['stepSize'])
                    qty_str = f"{executed_qty:.{len(str(step_size).split('.')[1])}f}"
                    
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
                    print(f"{Fore.RED}Placed sell order {sell_order_id} at {sell_price_str}{Style.RESET_ALL}")
                buy_order_id = None
            elif status not in ['NEW', 'PARTIALLY_FILLED']:
                print(f"Buy order {buy_order_id} no longer active: {status}")
                buy_order_id = None
        
        # Check sell order status
        elif sell_order_id is not None:
            order = client.get_order(symbol=symbol, orderId=sell_order_id)
            status = order['status']
            if status == 'FILLED':
                avg_sell_price = get_average_fill_price(order)
                print(f"{Fore.RED}Sell order filled at {avg_sell_price}{Style.RESET_ALL}")
                with open("sell_price.json", "w") as sell_price_file:
                    json.dump(avg_sell_price, sell_price_file)
                sell_order_id = None
            elif status not in ['NEW', 'PARTIALLY_FILLED']:
                print(f"Sell order {sell_order_id} no longer active: {status}")
                sell_order_id = None
        
        # Place new buy order if condition met
        elif buy_condition() and free_usdt_balance > 1:
            if testing_mode:
                simulated_buy_price = df['close'].iloc[-2] * 0.995
                print(f"{Fore.GREEN}Simulating Buy Order at {simulated_buy_price}{Style.RESET_ALL}")
                with open("buy_price.json", "w") as buy_price_file:
                    json.dump(simulated_buy_price, buy_price_file)
            else:
                symbol_info = client.get_symbol_info(symbol)
                price_filter = next(f for f in symbol_info['filters'] if f['filterType'] == 'PRICE_FILTER')
                lot_size_filter = next(f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE')
                
                tick_size = float(price_filter['tickSize'])
                step_size = float(lot_size_filter['stepSize'])
                price_precision = len(str(tick_size).split('.')[1].rstrip('0')) if '.' in str(tick_size) else 0
                qty_precision = len(str(step_size).split('.')[1].rstrip('0')) if '.' in str(step_size) else 0
                
                last_closed_price = df['close'].iloc[-2]
                target_buy_price = last_closed_price * 0.995
                target_buy_price = round(target_buy_price / tick_size) * tick_size
                target_buy_price_str = f"{target_buy_price:.{price_precision}f}"
                
                max_quantity = free_usdt_balance / target_buy_price
                quantity_to_buy = (max_quantity // step_size) * step_size
                quantity_to_buy_str = f"{quantity_to_buy:.{qty_precision}f}"
                
                if quantity_to_buy > 0:
                    print(f"Placing Limit Buy Order: Quantity={quantity_to_buy_str}, Price={target_buy_price_str}")
                    order = client.create_order(
                        symbol=symbol,
                        side='BUY',
                        type='LIMIT',
                        timeInForce='GTC',
                        quantity=quantity_to_buy_str,
                        price=target_buy_price_str
                    )
                    buy_order_id = order['orderId']
                    print(f"{Fore.GREEN}Limit Buy Order Placed: {order}{Style.RESET_ALL}")
                else:
                    print("Insufficient balance to place buy order.")
        
        print(f"{Fore.BLUE}Sleeping for 1 second{Style.RESET_ALL}")
        time.sleep(1)
    
    except Exception as e:
        print(f"Error: {e}")
        time.sleep(1)



