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

# Initialize the Binance client and set it to testnet
client = Client(API_KEY, API_SECRET)
client.API_URL = 'https://testnet.binance.vision/api'  # Use testnet endpoint for REST calls

# Define the trading pair; note that the timeframe is no longer used for candle data.
symbol = 'SOLUSDT'

# Set to True for testing (simulate orders), False for live trading
testing_mode = False

# Initialize buy_price and sell_price
buy_price = None
sell_price = None

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Fetch initial historical data (using 15-minute klines as a baseline for our rolling window)
def fetch_historical_klines(symbol, interval, lookback):
    try:
        klines = client.get_historical_klines(symbol, interval, lookback)
        df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume',
                                             'close_time', 'qav', 'num_trades', 'taker_base_vol',
                                             'taker_quote_vol', 'ignore'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        df = df[['open', 'high', 'low', 'close', 'volume']].astype(float)
        return df
    except Exception as e:
        logging.error(f"Error fetching historical klines: {e}")
        return pd.DataFrame()

# Fetch some historical data to build the initial window
historical_data = fetch_historical_klines(symbol, '15m', '2 days ago UTC')

# For market data, you can either use:
# Option A: Production WebSocket endpoint (market data is public)
ws_url = f"wss://stream.binance.com:9443/ws/{symbol.lower()}@trade"
# Option B: If a testnet websocket endpoint is available for spot, use that instead:
# ws_url = f"wss://testnet.binance.vision/ws/{symbol.lower()}@trade"

def on_message(ws, message):
    global buy_price, sell_price, historical_data

    try:
        data = json.loads(message)
    except Exception as e:
        logging.error(f"Error parsing message: {e}")
        return

    # Parse the live trade data
    trade_time = data.get('T')
    trade_price = float(data.get('p', 0))
    trade_volume = float(data.get('q', 0))
    current_price = trade_price  # Live price

    logging.info(f"Live Trade Price: {current_price}")

    # Append this trade as a new "candle" row to our historical data.
    new_row = pd.DataFrame([{
        'timestamp': pd.to_datetime(trade_time, unit='ms'),
        'open': trade_price,
        'high': trade_price,
        'low': trade_price,
        'close': trade_price,
        'volume': trade_volume
    }])
    new_row.set_index('timestamp', inplace=True)
    historical_data = pd.concat([historical_data, new_row])
    historical_data = historical_data.tail(500)

    # Calculate Bollinger Bands using a 20-point rolling window
    historical_data['rolling_mean'] = historical_data['close'].rolling(window=20).mean()
    historical_data['rolling_std'] = historical_data['close'].rolling(window=20).std()
    historical_data['upper_band'] = historical_data['rolling_mean'] + (historical_data['rolling_std'] * 1)
    historical_data['lower_band'] = historical_data['rolling_mean'] - (historical_data['rolling_std'] * 1)

    # Generate signal: 1 indicates a potential buy signal.
    historical_data['signal'] = 0
    historical_data.loc[
        (historical_data['close'] <= historical_data['lower_band']) & 
        (historical_data['close'] < historical_data['rolling_mean']),
        'signal'
    ] = 1

    # Retrieve free USDT balance (this call now goes to the testnet)
    try:
        free_usdt_balance = float(client.get_asset_balance(asset='USDT')['free'])
    except Exception as e:
        logging.error(f"Error fetching USDT balance: {e}")
        return

    logging.info(f"Current Live Price: {current_price}")
    logging.info(f"Buy Price: {buy_price}")
    logging.info(f"Sell Price: {sell_price}")

    # Buy condition: if the signal is 1 and live price is at or below our threshold relative to the lower band.
    buy_threshold = 0.99
    if historical_data['signal'].iloc[-1] == 1 and free_usdt_balance > 1 and buy_price is None:
        if current_price <= buy_threshold * historical_data['lower_band'].iloc[-1]:
            if testing_mode:
                logging.info(f"Simulating Buy Order at {current_price}")
                buy_price = current_price
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
                        quantity_to_buy = free_usdt_balance / current_price
                        if quantity_to_buy < min_qty:
                            logging.warning("Calculated quantity is below minimum allowed. Skipping buy.")
                        else:
                            quantity_to_buy = min(quantity_to_buy, max_qty)
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
            logging.info("Buy condition not met based on live price and Bollinger lower band.")

    # Sell condition: if we have bought and the live price has risen by at least 1.2%
    if buy_price is not None:
        price_difference = (current_price - buy_price) / buy_price
        if price_difference >= 0.012:
            try:
                sol_balance = float(client.get_asset_balance(asset='SOL')['free'])
                if sol_balance > 0:
                    if testing_mode:
                        logging.info(f"Simulating Sell Order at {current_price}")
                        with open("simulated_trades.log", "a") as f:
                            f.write(f"Sell at {current_price}\n")
                        buy_price = None
                    else:
                        symbol_info = client.get_symbol_info(symbol)
                        lot_size_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'), None)
                        if lot_size_filter:
                            quantity_step_size = float(lot_size_filter['stepSize'])
                            min_qty = float(lot_size_filter['minQty'])
                            max_qty = float(lot_size_filter['maxQty'])
                            quantity_to_sell = sol_balance
                            if quantity_to_sell < min_qty:
                                logging.warning("Calculated sell quantity is below minimum allowed. Skipping sell.")
                            else:
                                quantity_to_sell = min(quantity_to_sell, max_qty)
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
            logging.info("Sell condition not met yet.")

def on_error(ws, error):
    logging.error(f"WebSocket Error: {error}")

def on_close(ws, close_status_code, close_msg):
    logging.info("WebSocket Closed")

def on_open(ws):
    logging.info("WebSocket connection opened")

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
            time.sleep(1)

if __name__ == "__main__":
    connect_ws()
