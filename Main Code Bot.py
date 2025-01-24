import json
import websocket
import pandas as pd
from binance.client import Client
from dotenv import load_dotenv
import os
import smtplib, ssl
import time

# Load .env file
load_dotenv()

# Load API credentials from environment variables
API_KEY = os.environ.get('API_KEY')
API_SECRET = os.environ.get('API_SECRET')

if not API_KEY or not API_SECRET:
    raise ValueError("Please set BINANCE_API_KEY and BINANCE_API_SECRET environment variables.")

# Initialize the Binance client
client = Client(API_KEY, API_SECRET)

# Define the trading pair and timeframe
symbol = 'solusdt'
timeframe = '15m'

# Initialize buy_price and sell_price
buy_price = None
sell_price = None

# Set to True for testing, False for live trading
testing_mode = False

# Define WebSocket URL
ws_url = f"wss://stream.binance.com:9443/ws/{symbol}@kline_{timeframe}"

def on_message(ws, message):
    global buy_price, sell_price

    print("Received message from WebSocket")

    data = json.loads(message)
    print(f"Parsed JSON data: {data}")

    kline = data['k']
    if kline['x']:  # Only consider closed candles
        print("Processing closed kline data")

        open_time = kline['t']
        open_price = float(kline['o'])
        high_price = float(kline['h'])
        low_price = float(kline['l'])
        close_price = float(kline['c'])
        volume = float(kline['v'])

        # Create a DataFrame for calculations
        df = pd.DataFrame([{
            'timestamp': open_time,
            'open': open_price,
            'high': high_price,
            'low': low_price,
            'close': close_price,
            'volume': volume
        }])

        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)

        # Implement Bollinger Bands strategy
        df['rolling_mean'] = df['close'].rolling(window=8).mean()
        df['rolling_std'] = df['close'].rolling(window=8).std()
        df['upper_band'] = df['rolling_mean'] + (df['rolling_std'] * 1)
        df['lower_band'] = df['rolling_mean'] - (df['rolling_std'] * 1)
        df['signal'] = 0  # 0 means do nothing
        df.loc[(df['close'] <= df['lower_band']) & (df['close'] < df['rolling_mean']), 'signal'] = 1

        # Retrieve free USDT balance
        free_usdt_balance = float(client.get_asset_balance(asset='USDT')['free'])

        # Print current state
        print(f"Current Price: {close_price}")
        print(f"Buy Price: {buy_price}")
        print(f"Sell Price: {sell_price}")

        # Buy condition
        if df['signal'].iloc[-1] == 1 and free_usdt_balance > 1:
            if df['close'].iloc[-1] <= 0.986 * df['lower_band'].iloc[-1]:
                if testing_mode:
                    print(f"Simulating Buy Order at {df['close'].iloc[-1]}")
                    buy_price = df['close'].iloc[-1]
                else:
                    # Execute real buy order
                    symbol_info = client.get_symbol_info('SOLUSDT')
                    lot_size_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'), None)
                    if lot_size_filter:
                        quantity_step_size = float(lot_size_filter['stepSize'])
                        max_precision = len(lot_size_filter['maxQty'].split('.')[1])
                        solusdt_ticker = client.get_symbol_ticker(symbol='SOLUSDT')
                        current_sol_price = float(solusdt_ticker['price'])
                        usdt_balance = float(client.get_asset_balance(asset='USDT')['free'])
                        quantity_to_buy = usdt_balance / current_sol_price
                        quantity_to_buy -= quantity_to_buy % quantity_step_size
                        quantity_to_buy = round(quantity_to_buy, max_precision)
                        order = client.create_order(
                            symbol='SOLUSDT',
                            side='BUY',
                            type='MARKET',
                            quantity=quantity_to_buy
                        )
                        buy_price = order['fills'][0]['price']
                        print(f"Buy Order Executed at Price: {buy_price}")
                    else:
                        print("LOT_SIZE filter not found in symbol info.")
            else:
                print("Buy condition not met")

        # Sell condition
        if buy_price is not None:
            current_price = df['close'].iloc[-1]
            buy_price_float = float(buy_price)
            price_difference = (current_price - buy_price_float) / buy_price_float
            if price_difference >= 0.012:
                sol_balance = float(client.get_asset_balance(asset='SOL')['free'])
                if sol_balance > 0:
                    if testing_mode:
                        print(f"Simulating Sell Order at {df['close'].iloc[-1]}")
                        buy_price = None
                    else:
                        symbol_info = client.get_symbol_info('SOLUSDT')
                        lot_size_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'), None)
                        if lot_size_filter:
                            quantity_step_size = float(lot_size_filter['stepSize'])
                            max_precision = len(lot_size_filter['maxQty'].split('.')[1])
                            quantity_to_sell = sol_balance
                            quantity_to_sell -= quantity_to_sell % quantity_step_size
                            quantity_to_sell = round(quantity_to_sell, max_precision)
                            order = client.create_order(
                                symbol='SOLUSDT',
                                side='SELL',
                                type='MARKET',
                                quantity=quantity_to_sell
                            )
                            sell_price = order['fills'][0]['price']
                            print(f"Sell Order Executed at Price: {sell_price}")
                            buy_price = None
                        else:
                            print("LOT_SIZE filter not found in symbol info.")
            else:
                print("Sell condition not met")
    else:
        print("Kline data is not closed yet")

def on_error(ws, error):
    print(f"WebSocket Error: {error}")

def on_close(ws, close_status_code, close_msg):
    print("WebSocket Closed")

def on_open(ws):
    print("WebSocket connection opened")

# WebSocket connection with auto-reconnect logic
def connect_ws():
    ws = websocket.WebSocketApp(ws_url,
                                on_open=on_open,
                                on_message=on_message,
                                on_error=on_error,
                                on_close=on_close)
    while True:
        try:
            ws.run_forever()
        except Exception as e:
            print(f"WebSocket connection error: {e}")
        time.sleep(1)  # wait for 1 seconds before attempting to reconnect

if __name__ == "__main__":
    connect_ws()



