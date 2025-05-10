import os
import time
import pandas as pd
from binance.client import Client
from colorama import init, Fore, Style
import json
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
init()

# API credentials
API_KEY = os.environ.get('API_KEY')
API_SECRET = os.environ.get('API_SECRET')
client = Client(API_KEY, API_SECRET, testnet=True)  # Set testnet=False for live trading

if not API_KEY or not API_SECRET:
    raise ValueError("Please set API_KEY and API_SECRET in your .env file.")

# Trading parameters
symbol = 'SOLUSDT'
timeframe = '15m'
testing_mode = False
state_file = "trade_state.json"

### State Management
def load_state():
    default_state = {"buy_price": None, "position_open": False}
    try:
        if os.path.exists(state_file):
            with open(state_file, "r") as f:
                state = json.load(f)
                buy_price = state.get("buy_price")
                if buy_price is not None:
                    buy_price = float(buy_price)
                return {"buy_price": buy_price, "position_open": bool(state.get("position_open", False))}
        return default_state
    except Exception as e:
        print(f"Error loading state: {e}. Using default state.")
        return default_state

def save_state(buy_price, position_open):
    try:
        with open(state_file, "w") as f:
            json.dump({"buy_price": buy_price, "position_open": position_open}, f)
    except Exception as e:
        print(f"Error saving state: {e}")

### Strategy Functions
def fetch_candlestick_data():
    """Fetch fresh candlestick data from Binance."""
    klines = client.get_klines(symbol=symbol, interval=timeframe, limit=100)
    df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 
                                       'close_time', 'quote_asset_volume', 'number_of_trades', 
                                       'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
    numeric_columns = ['open', 'high', 'low', 'close']
    df[numeric_columns] = df[numeric_columns].apply(pd.to_numeric, errors='coerce')
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    return df

def bollinger_bands_strategy(df, window=10, num_std_dev=1):
    """Calculate Bollinger Bands using closed candles."""
    if len(df) < window:
        print("Not enough data for Bollinger Bands calculation.")
        return df
    df['rolling_mean'] = df['close'].rolling(window=window).mean()
    df['rolling_std'] = df['close'].rolling(window=window).std()
    df['upper_band'] = df['rolling_mean'] + (df['rolling_std'] * num_std_dev)
    df['lower_band'] = df['rolling_mean'] - (df['rolling_std'] * num_std_dev)
    return df

def buy_condition(df_closed, current_price):
    """Check buy condition with logging."""
    if df_closed.empty or pd.isna(df_closed['lower_band'].iloc[-1]):
        print("Buy Condition Failed: No valid lower band.")
        return False
    lower_band = df_closed['lower_band'].iloc[-1]
    upper_band = df_closed['upper_band'].iloc[-1]
    threshold = 0.99 * lower_band
    print(f"Buy Check: Price={current_price}, Lower={lower_band}, Upper={upper_band}, Threshold={threshold}")
    return current_price <= threshold

def sell_condition(current_price, buy_price):
    """Check sell condition with logging."""
    if buy_price is None:
        return False
    profit = (current_price - buy_price) / buy_price
    print(f"Sell Check: Price={current_price}, Buy={buy_price}, Profit={profit:.4f}")
    return profit >= 0.012

### Main Loop
while True:
    try:
        # Load state
        state = load_state()
        buy_price = state["buy_price"]
        position_open = state["position_open"]

        # Fetch fresh data
        df = fetch_candlestick_data()
        df_closed = df.iloc[:-1].copy()  # Exclude ongoing candle
        df_closed = bollinger_bands_strategy(df_closed)

        # Fetch current price from the in-progress 15m candle
        current_price = df['close'].iloc[-1]
        print(f"Current Price (in-progress candle): {current_price}")

        # Get USDT balance
        free_usdt_balance = float(client.get_asset_balance(asset='USDT')['free'])

        if not position_open:
            if buy_condition(df_closed, current_price) and free_usdt_balance > 1:
                if testing_mode:
                    print(f"{Fore.GREEN}Simulated Buy at {current_price}{Style.RESET_ALL}")
                    buy_price = current_price
                    position_open = True
                    save_state(buy_price, position_open)
                else:
                    symbol_info = client.get_symbol_info(symbol)
                    lot_size_filter = next(f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE')
                    step_size = float(lot_size_filter['stepSize'])
                    precision = len(lot_size_filter['stepSize'].split('.')[1])
                    quantity = round((free_usdt_balance / current_price // step_size) * step_size, precision)
                    if quantity > 0:
                        order = client.create_order(symbol=symbol, side='BUY', type='MARKET', quantity=quantity)
                        buy_price = float(order['fills'][0]['price'])
                        position_open = True
                        save_state(buy_price, position_open)
                        print(f"{Fore.GREEN}Buy Executed at {buy_price}{Style.RESET_ALL}")
                    else:
                        print("Insufficient balance for buy.")
        else:
            if sell_condition(current_price, buy_price):
                if testing_mode:
                    print(f"{Fore.RED}Simulated Sell at {current_price}{Style.RESET_ALL}")
                    buy_price = None
                    position_open = False
                    save_state(buy_price, position_open)
                else:
                    sol_balance = float(client.get_asset_balance(asset='SOL')['free'])
                    if sol_balance > 0:
                        symbol_info = client.get_symbol_info(symbol)
                        lot_size_filter = next(f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE')
                        step_size = float(lot_size_filter['stepSize'])
                        precision = len(lot_size_filter['stepSize'].split('.')[1])
                        quantity = round((sol_balance // step_size) * step_size, precision)
                        if quantity > 0:
                            order = client.create_order(symbol=symbol, side='SELL', type='MARKET', quantity=quantity)
                            sell_price = float(order['fills'][0]['price'])
                            buy_price = None
                            position_open = False
                            save_state(buy_price, position_open)
                            print(f"{Fore.RED}Sell Executed at {sell_price}{Style.RESET_ALL}")
                        else:
                            print("No SOL to sell.")
                    else:
                        print("No SOL balance.")

        print(f"{Fore.BLUE}Sleeping for 1s{Style.RESET_ALL}")
        time.sleep(1)

    except Exception as e:
        print(f"Error: {e}")
        time.sleep(1)

