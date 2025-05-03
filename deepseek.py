from pybit.unified_trading import HTTP
import pandas as pd
import numpy as np
import time
import logging

# Configuración hiper-agresiva (¡EXTREMO RIESGO!)
api_key = "8eTibTFVF3eBprPvjo"
api_secret = "AejLpGJuYNYNIMf5W7sKs0NHvlsDfvFxkVTBz"
symbol = "1000BONKUSDT"  # Moneda volátil
timeframe = "1"  # Intervalo de 1 minuto
usdt_amount = 10
leverage = 20  # Máximo apalancamiento

# Parámetros de riesgo ajustados
tp_percent = 15  # Take Profit 15%
sl_percent = 5   # Stop Loss 5%
martingale_factor = 1.8  # Factor Martingala

client = HTTP(api_key=api_key, api_secret=api_secret, testnet=False)

# Configurar apalancamiento
client.set_leverage(
    category="linear",
    symbol=symbol,
    buyLeverage=str(leverage),
    sellLeverage=str(leverage)
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_volatility(data):
    """Calcula la volatilidad promedio"""
    return np.mean(data['high'] - data['low'])

def dynamic_position_size(equity, volatility):
    """Tamaño de posición basado en volatilidad"""
    risk = 0.15  # 15% de equity por trade
    return (equity * risk) / volatility

def aggressive_trading_strategy():
    equity = usdt_amount
    trade_count = 0
    loss_streak = 0
    
    while equity < 100 and trade_count < 300:  # Límite de 300 operaciones
        try:
            # Obtener datos en tiempo real
            ticker = client.get_tickers(category='linear', symbol=symbol)
            current_price = float(ticker['result']['list'][0]['lastPrice'])
            
            # Obtener datos históricos
            data = client.get_kline(
                symbol=symbol, 
                interval=timeframe, 
                limit=50
            )
            df = pd.DataFrame(data['result']['list'], columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'
            ]).astype(float)
            
            # Calcular indicadores
            df['RSI'] = calculate_rsi(df['close'], 14)
            df['VWAP'] = calculate_vwap(df)
            volatility = get_volatility(df)
            
            # Calcular tamaño de posición
            position_size = dynamic_position_size(equity, volatility) * (martingale_factor ** loss_streak)
            position_size = min(position_size, equity * leverage)
            
            # Estrategia de momentum
            last_rsi = df['RSI'].iloc[-1]
            price_relation = current_price / df['VWAP'].iloc[-1]
            
            if last_rsi < 35 and price_relation < 0.98:
                # Señal de compra
                execute_trade("Buy", position_size, current_price)
                equity = manage_trade("Buy", current_price, position_size)
                loss_streak = 0
                trade_count +=1
                
            elif last_rsi > 65 and price_relation > 1.02:
                # Señal de venta
                execute_trade("Sell", position_size, current_price)
                equity = manage_trade("Sell", current_price, position_size)
                loss_streak = 0
                trade_count +=1
                
            else:
                time.sleep(10)
                
        except Exception as e:
            logging.error(f"Error: {e}")
            time.sleep(30)

def execute_trade(side, qty, price):
    """Ejecuta orden con manejo de errores"""
    try:
        order = client.place_order(
            category="linear",
            symbol=symbol,
            side=side,
            orderType="Market",
            qty=round(qty, 3),
            timeInForce="ImmediateOrCancel"
        )
        logging.info(f"Orden ejecutada: {order}")
    except Exception as e:
        logging.error(f"Fallo en orden: {e}")

def manage_trade(direction, entry_price, qty):
    """Manejo agresivo de TP/SL"""
    try:
        if direction == "Buy":
            tp_price = entry_price * (1 + tp_percent/100)
            sl_price = entry_price * (1 - sl_percent/100)
            tp_side = "Sell"
        else:
            tp_price = entry_price * (1 - tp_percent/100)
            sl_price = entry_price * (1 + sl_percent/100)
            tp_side = "Buy"
        
        # Colocar TP/SL
        client.set_trading_stop(
            category="linear",
            symbol=symbol,
            takeProfit=tp_price,
            stopLoss=sl_price,
            tpTriggerBy="LastPrice",
            slTriggerBy="LastPrice"
        )
        
        # Monitorear resultado
        while True:
            position = client.get_positions(category="linear", symbol=symbol)['result']['list'][0]
            if float(position['size']) == 0:
                break
            time.sleep(1)
            
        new_equity = float(client.get_wallet_balance(accountType="CONTRACT")['result']['list'][0]['equity'])
        return new_equity
        
    except Exception as e:
        logging.error(f"Error managing trade: {e}")
        return usdt_amount

def calculate_rsi(prices, period):
    """Cálculo de RSI optimizado"""
    delta = prices.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calculate_vwap(df):
    """Cálculo de VWAP"""
    return (df['close'] * df['volume']).cumsum() / df['volume'].cumsum()

if __name__ == "__main__":
    logging.info("Iniciando estrategia hiper-agresiva")
    aggressive_trading_strategy()