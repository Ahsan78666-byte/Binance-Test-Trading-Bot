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

if not API_KEY or not API_SECRET:
    raise ValueError("Please set BINANCE_API_KEY and BINANCE_API_SECRET environment variables.")

# Initialize the Binance client
client = Client(API_KEY, API_SECRET, testnet=True)

# Define the trading pair and timeframe
symbol = 'SOLUSDT'
timeframe = '15m'

# Initialize buy and sell prices
buy_price = None
sell_price = None

# Set to True for testing, False for live trading
testing_mode = True

def get_lot_size_precision(symbol_info):
    """Retrieve lot size and precision from symbol info."""
    lot_size_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'), None)
    if lot_size_filter:
        step_size = float(lot_size_filter['stepSize'])
        if '.' in lot_size_filter['maxQty']:
            precision = len(lot_size_filter['maxQty'].split('.')[1])
        else:
            precision = 0
        return step_size, precision
    raise ValueError("LOT_SIZE filter not found")

def fetch_klines(symbol, interval, limit=24):
    """Fetch historical candlestick data."""
    klines = client.get_historical_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 
                                       'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 
                                       'taker_buy_quote_asset_volume', 'ignore'])
    df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].astype(float)
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    return df

def bollinger_bands_strategy(df, window=20, num_std_dev=2):
    """Apply Bollinger Bands strategy."""
    df['rolling_mean'] = df['close'].rolling(window=window).mean()
    df['rolling_std'] = df['close'].rolling(window=window).std()
    df['upper_band'] = df['rolling_mean'] + (df['rolling_std'] * num_std_dev)
    df['lower_band'] = df['rolling_mean'] - (df['rolling_std'] * num_std_dev)
    return df

def buy_condition(df):
    """Check if buy condition is met."""
    return df['close'].iloc[-1] <= 0.982 * df['lower_band'].iloc[-1]

def sell_condition(df, buy_price):
    """Check if sell condition is met."""
    if buy_price is None:
        return False
    current_price = df['close'].iloc[-1]
    price_difference = (current_price - buy_price) / buy_price
    return price_difference >= 0.012

while True:
    try:
        # Fetch candlestick data and apply Bollinger Bands strategy
        df = fetch_klines(symbol, timeframe)
        df = bollinger_bands_strategy(df)

        # Buy condition
        if buy_condition(df):
            usdt_balance = float(client.get_asset_balance(asset='USDT')['free'])
            if usdt_balance > 1:
                symbol_info = client.get_symbol_info(symbol)
                step_size, precision = get_lot_size_precision(symbol_info)
                current_price = df['close'].iloc[-1]
                quantity_to_buy = usdt_balance / current_price
                quantity_to_buy = round(quantity_to_buy - (quantity_to_buy % step_size), precision)

                if testing_mode:
                    print(f"{Fore.GREEN}Simulating Buy Order{Style.RESET_ALL}")
                    print(f"Buy Price: {current_price}, Quantity: {quantity_to_buy}")
                    buy_price = current_price
                else:
                    print(f"{Fore.GREEN}Executing Buy Order{Style.RESET_ALL}")
                    order = client.create_order(
                        symbol=symbol,
                        side='BUY',
                        type='MARKET',
                        quantity=quantity_to_buy
                    )
                    buy_price = float(order['fills'][0]['price'])
                    print(f"Buy Order Executed at {buy_price}")

        # Sell condition
        if sell_condition(df, buy_price):
            sol_balance = float(client.get_asset_balance(asset='SOL')['free'])
            if sol_balance > 0:
                symbol_info = client.get_symbol_info(symbol)
                step_size, precision = get_lot_size_precision(symbol_info)
                quantity_to_sell = round(sol_balance - (sol_balance % step_size), precision)

                if testing_mode:
                    print(f"{Fore.RED}Simulating Sell Order{Style.RESET_ALL}")
                    print(f"Sell Price: {df['close'].iloc[-1]}, Quantity: {quantity_to_sell}")
                    buy_price = None
                else:
                    print(f"{Fore.RED}Executing Sell Order{Style.RESET_ALL}")
                    order = client.create_order(
                        symbol=symbol,
                        side='SELL',
                        type='MARKET',
                        quantity=quantity_to_sell
                    )
                    sell_price = float(order['fills'][0]['price'])
                    print(f"Sell Order Executed at {sell_price}")
                    buy_price = None

        # Wait before the next iteration
        print(f"{Fore.BLUE}Sleeping for 1 second{Style.RESET_ALL}")
        time.sleep(1)

    except Exception as e:
        print(f"Error: {e}")


