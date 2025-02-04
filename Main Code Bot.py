import json
import websocket
import pandas as pd
from binance.client import Client
from dotenv import load_dotenv
import os
import time
import logging

# Load .env file
load_dotenv()

# Load API credentials from environment variables
API_KEY = os.environ.get('API_KEY')
API_SECRET = os.environ.get('API_SECRET')

if not API_KEY or not API_SECRET:
    raise ValueError("Please set API_KEY and API_SECRET environment variables.")

# Initialize the Binance client
client = Client(API_KEY, API_SECRET)

# Define the trading pair and timeframe
symbol = 'SOLUSDT'
timeframe = '15m'

# Set to True for testing, False for live trading
testing_mode = False

# Initialize buy_price and sell_price
buy_price = None
sell_price = None

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Fetch historical kline data
def fetch_historical_klines(symbol, interval, lookback):
    try:
        klines = client.get_historical_klines(symbol, interval, lookback)
        df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        df = df[['open', 'high', 'low', 'close', 'volume']].astype(float)
        return df
    except Exception as e:
        logging.error(f"Error fetching historical klines: {e}")
        return pd.DataFrame()

# Fetch initial historical data
historical_data = fetch_historical_klines(symbol, timeframe, '2 days ago UTC')

# Define WebSocket URL
ws_url = f"wss://stream.binance.com:9443/ws/{symbol.lower()}@kline_{timeframe}"

# WebSocket event handlers
def on_message(ws, message):
    global buy_price, sell_price, historical_data

    logging.info("Received message from WebSocket")

    data = json.loads(message)
    logging.info(f"Parsed JSON data: {data}")

    kline = data['k']
    if kline['x']:  # Only consider closed candles
        logging.info("Processing closed kline data")

        open_time = kline['t']
        open_price = float(kline['o'])
        high_price = float(kline['h'])
        low_price = float(kline['l'])
        close_price = float(kline['c'])
        volume = float(kline['v'])

        # Append new kline data to historical data
        new_row = pd.DataFrame([{
            'timestamp': pd.to_datetime(open_time, unit='ms'),
            'open': open_price,
            'high': high_price,
            'low': low_price,
            'close': close_price,
            'volume': volume
        }])
        new_row.set_index('timestamp', inplace=True)
        historical_data = pd.concat([historical_data, new_row])

        # Keep only the last 500 rows for calculation efficiency
        historical_data = historical_data.tail(max(500, 10))

        # Implement Bollinger Bands strategy
        historical_data['rolling_mean'] = historical_data['close'].rolling(window=20).mean()
        historical_data['rolling_std'] = historical_data['close'].rolling(window=20).std()
        historical_data['upper_band'] = historical_data['rolling_mean'] + (historical_data['rolling_std'] * 1)
        historical_data['lower_band'] = historical_data['rolling_mean'] - (historical_data['rolling_std'] * 1)
        historical_data['signal'] = 0  # 0 means do nothing
        historical_data.loc[(historical_data['close'] <= historical_data['lower_band']) & (historical_data['close'] < historical_data['rolling_mean']), 'signal'] = 1

        # Retrieve free USDT balance
        try:
            free_usdt_balance = float(client.get_asset_balance(asset='USDT')['free'])
        except Exception as e:
            logging.error(f"Error fetching USDT balance: {e}")
            return

        # Print current state
        logging.info(f"Current Price: {close_price}")
        logging.info(f"Buy Price: {buy_price}")
        logging.info(f"Sell Price: {sell_price}")

        # Buy condition
        buy_threshold = 0.99  # Configurable threshold
        if historical_data['signal'].iloc[-1] == 1 and free_usdt_balance > 1:
            if historical_data['close'].iloc[-1] <= buy_threshold * historical_data['lower_band'].iloc[-1]:
                if testing_mode:
                    logging.info(f"Simulating Buy Order at {historical_data['close'].iloc[-1]}")
                    buy_price = historical_data['close'].iloc[-1]
                    # Log simulated trade
                    with open("simulated_trades.log", "a") as f:
                        f.write(f"Buy at {buy_price}\n")
                else:
                    try:
                        symbol_info = client.get_symbol_info(symbol)
                        lot_size_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'), None)
                        if lot_size_filter:
                            quantity_step_size = float(lot_size_filter['stepSize'])
                            min_qty = float(lot_size_filter['minQty'])
                            max_qty = float(lot_size_filter['maxQty'])
                            current_price = float(client.get_symbol_ticker(symbol=symbol)['price'])
                            quantity_to_buy = free_usdt_balance / current_price
                            quantity_to_buy = max(min(quantity_to_buy, max_qty), min_qty)  # Ensure quantity is within bounds
                            quantity_to_buy -= quantity_to_buy % quantity_step_size
                            quantity_to_buy = round(quantity_to_buy, len(str(quantity_step_size).split('.')[1]))
                            order = client.create_order(
                                symbol=symbol,
                                side='BUY',
                                type='MARKET',
                                quantity=quantity_to_buy
                            )
                            buy_price = float(order['fills'][0]['price'])
                            logging.info(f"Buy Order Executed at Price: {buy_price}")
                        else:
                            logging.error("LOT_SIZE filter not found in symbol info.")
                    except Exception as e:
                        logging.error(f"Error executing buy order: {e}")
            else:
                logging.info("Buy condition not met")

        # Sell condition
        if buy_price is not None:
            current_price = historical_data['close'].iloc[-1]
            price_difference = (current_price - buy_price) / buy_price
            if price_difference >= 0.012:
                try:
                    sol_balance = float(client.get_asset_balance(asset='SOL')['free'])
                    if sol_balance > 0:
                        if testing_mode:
                            logging.info(f"Simulating Sell Order at {historical_data['close'].iloc[-1]}")
                            buy_price = None
                            # Log simulated trade
                            with open("simulated_trades.log", "a") as f:
                                f.write(f"Sell at {historical_data['close'].iloc[-1]}\n")
                        else:
                            symbol_info = client.get_symbol_info(symbol)
                            lot_size_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'), None)
                            if lot_size_filter:
                                quantity_step_size = float(lot_size_filter['stepSize'])
                                min_qty = float(lot_size_filter['minQty'])
                                max_qty = float(lot_size_filter['maxQty'])
                                quantity_to_sell = sol_balance
                                quantity_to_sell = max(min(quantity_to_sell, max_qty), min_qty)  # Ensure quantity is within bounds
                                quantity_to_sell -= quantity_to_sell % quantity_step_size
                                quantity_to_sell = round(quantity_to_sell, len(str(quantity_step_size).split('.')[1]))
                                order = client.create_order(
                                    symbol=symbol,
                                    side='SELL',
                                    type='MARKET',
                                    quantity=quantity_to_sell
                                )
                                sell_price = float(order['fills'][0]['price'])
                                logging.info(f"Sell Order Executed at Price: {sell_price}")
                                buy_price = None
                            else:
                                logging.error("LOT_SIZE filter not found in symbol info.")
                except Exception as e:
                    logging.error(f"Error executing sell order: {e}")
            else:
                logging.info("Sell condition not met")
    else:
        logging.info("Kline data is not closed yet")

def on_error(ws, error):
    logging.error(f"WebSocket Error: {error}")

def on_close(ws, close_status_code, close_msg):
    logging.info("WebSocket Closed")

def on_open(ws):
    logging.info("WebSocket connection opened")

# WebSocket connection with auto-reconnect logic
def connect_ws():
    retry_count = 0
    max_retries = 10
    while retry_count < max_retries:
        try:
            ws = websocket.WebSocketApp(ws_url,
                                        on_open=on_open,
                                        on_message=on_message,
                                        on_error=on_error,
                                        on_close=on_close)
            ws.run_forever()
        except Exception as e:
            logging.error(f"WebSocket connection error: {e}")
            retry_count += 1
            time.sleep(1)  # Wait for 1 seconds before retrying

if __name__ == "__main__":
    connect_ws()
