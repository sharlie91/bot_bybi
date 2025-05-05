from pybit.unified_trading import HTTP
import pandas as pd
import math
from decimal import Decimal, ROUND_DOWN, ROUND_FLOOR
import time

# Configuración de la API
api_key = "8eTibTFVF3eBprPvjo"
api_secret = "ejLpGJuYNYNIMf5W7sKs0NHvlsDfvFxkVTBz"
timeframe = "5"  # Intervalo de tiempo
usdt = 10  # Cantidad de dólares para abrir posición

tp_porcent = 0.5  # Take profit porcentaje
sl_porcent = 1  # Stop loss porcentaje
volatility_threshold = 0.005  # Umbral mínimo de volatilidad (0.5%)

# Lista de criptomonedas a evaluar
cryptos = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", 
    "XRPUSDT", "ADAUSDT", "DOGEUSDT", "DOTUSDT",
    "WLDUSDT", "AVAXUSDT", "1000BONKUSDT"
]

client = HTTP(api_key=api_key, api_secret=api_secret, testnet=False)

def obtener_datos_historicos(symbol, interval, limite=200):
    """Obtener datos de las velas"""
    response = client.get_kline(symbol=symbol, interval=interval, limit=limite)
    if "result" in response:
        data = pd.DataFrame(response['result']['list']).astype(float)
        data[0] = pd.to_datetime(data[0], unit='ms')
        data.set_index(0, inplace=True)
        data = data[::-1].reset_index(drop=True)
        return data
    else:
        raise Exception(f"Error al obtener datos históricos para {symbol}: " + str(response))

def calcular_atr(data, period=14):
    """Calcular el Average True Range (ATR) como medida de volatilidad"""
    high = data[2]
    low = data[3]
    close = data[4]
    
    tr = pd.DataFrame()
    tr['h-l'] = high - low
    tr['h-pc'] = abs(high - close.shift(1))
    tr['l-pc'] = abs(low - close.shift(1))
    tr['tr'] = tr.max(axis=1)
    
    atr = tr['tr'].rolling(period).mean()
    return atr.iloc[-1]

def calcular_bandas_bollinger(data, ventana=20, desviacion=2):
    """Calcular bandas de Bollinger"""
    data['MA'] = data[4].rolling(window=ventana).mean()
    data['UpperBand'] = data['MA'] + (data[4].rolling(window=ventana).std() * desviacion)
    data['LowerBand'] = data['MA'] - (data[4].rolling(window=ventana).std() * desviacion)
    return data.iloc[-1]

def evaluar_criptomonedas():
    """Evaluar todas las criptomonedas y devolver la mejor oportunidad"""
    oportunidades = []
    
    for symbol in cryptos:
        try:
            # Obtener datos y configuración del símbolo
            step = client.get_instruments_info(category="linear", symbol=symbol)
            if not step['result']['list']:
                continue
                
            ticksize = float(step['result']['list'][0]['priceFilter']['tickSize'])
            scala_precio = int(step['result']['list'][0]["priceScale"])
            precision_step = float(step['result']['list'][0]["lotSizeFilter"]["qtyStep"])
            
            # Obtener datos históricos
            data = obtener_datos_historicos(symbol, timeframe)
            current_price = float(client.get_tickers(category='linear', symbol=symbol)['result']['list'][0]['lastPrice'])
            
            # Calcular métricas
            current_atr = calcular_atr(data)
            atr_percentage = current_atr / current_price
            bollinger = calcular_bandas_bollinger(data)
            
            # Determinar señal
            signal = None
            if current_price >= bollinger['UpperBand']:
                signal = "Sell"
            elif current_price <= bollinger['LowerBand']:
                signal = "Buy"
                
            # Solo considerar si supera el umbral de volatilidad
            if atr_percentage >= volatility_threshold and signal:
                oportunidades.append({
                    'symbol': symbol,
                    'price': current_price,
                    'volatility': atr_percentage,
                    'signal': signal,
                    'ticksize': ticksize,
                    'scala_precio': scala_precio,
                    'precision_step': precision_step
                })
                
        except Exception as e:
            print(f"Error evaluando {symbol}: {e}")
            continue
    
    # Ordenar por mayor volatilidad primero
    if oportunidades:
        oportunidades.sort(key=lambda x: x['volatility'], reverse=True)
        return oportunidades[0]
    return None

def qty_precision(qty, precision):
    qty = math.floor(qty / precision) * precision
    return qty

def qty_step(price, ticksize, scala_precio):
    precision = Decimal(f"{10 ** scala_precio}")
    tickdec = Decimal(f"{ticksize}")
    precio_final = (Decimal(f"{price}") * precision) / precision
    precide = precio_final.quantize(Decimal(f"{1 / precision}"), rounding=ROUND_FLOOR)
    operaciondec = (precide / tickdec).quantize(Decimal('1'), rounding=ROUND_FLOOR) * tickdec
    result = float(operaciondec)
    return result

def crear_orden(symbol, side, order_type, qty):
    response = client.place_order(
        category="linear",
        symbol=symbol,
        side=side,
        orderType=order_type,
        qty=qty,
        timeInForce="GoodTillCancel"
    )
    print("Orden creada con éxito:", response)

def establecer_stop_loss(symbol, sl, ticksize, scala_precio):
    sl = qty_step(sl, ticksize, scala_precio)
    order = client.set_trading_stop(
        category="linear",
        symbol=symbol,
        stopLoss=sl,
        slTriggerB="LastPrice",
        positionIdx=0
    )
    return order

def establecer_take_profit(symbol, tp, side, qty, ticksize, scala_precio):
    price = qty_step(tp, ticksize, scala_precio)
    order = client.place_order(
        category="linear",
        symbol=symbol,
        side=side,
        orderType="Limit",
        reduceOnly=True,
        qty=qty,
        price=price
    )
    return order

stop = False
tipo = ""
qty = 0
current_symbol = None
ticksize = None
scala_precio = None

while True:
    try:
        # Primero verificar si hay posición abierta
        posiciones = client.get_positions(category="linear", settleCoin="USDT")  # Especificamos settleCoin
        posicion_abierta = None
        
        for pos in posiciones['result']['list']:
            if float(pos['size']) != 0:
                posicion_abierta = pos
                break
                
        if posicion_abierta:
            current_symbol = posicion_abierta['symbol']
            print(f"Hay una posición abierta en {current_symbol}")
            
            # Obtener parámetros del símbolo actual si no los tenemos
            if ticksize is None or scala_precio is None:
                step = client.get_instruments_info(category="linear", symbol=current_symbol)
                if step['result']['list']:
                    ticksize = float(step['result']['list'][0]['priceFilter']['tickSize'])
                    scala_precio = int(step['result']['list'][0]["priceScale"])
                    precision_step = float(step['result']['list'][0]["lotSizeFilter"]["qtyStep"])
            
            if not stop and ticksize is not None and scala_precio is not None:
                precio_de_entrada = float(posicion_abierta['avgPrice'])
                if posicion_abierta['side'] == 'Buy':
                    stop_loss_price = precio_de_entrada * (1 - sl_porcent / 100)
                    take_profit_price = precio_de_entrada * (1 + tp_porcent / 100)
                    establecer_stop_loss(current_symbol, stop_loss_price, ticksize, scala_precio)
                    establecer_take_profit(current_symbol, take_profit_price, "Sell", qty, ticksize, scala_precio)
                    print("Stop loss y Take profit activados")
                    stop = True
                else:
                    stop_loss_price = precio_de_entrada * (1 + sl_porcent / 100)
                    take_profit_price = precio_de_entrada * (1 - tp_porcent / 100)
                    establecer_stop_loss(current_symbol, stop_loss_price, ticksize, scala_precio)
                    establecer_take_profit(current_symbol, take_profit_price, "Buy", qty, ticksize, scala_precio)
                    print("Stop loss y Take profit activados")
                    stop = True
        else:
            stop = False
            qty = 0
            current_symbol = None
            ticksize = None
            scala_precio = None
            
            # Evaluar todas las criptomonedas
            mejor_oportunidad = evaluar_criptomonedas()
            
            if mejor_oportunidad:
                print(f"\nMejor oportunidad encontrada: {mejor_oportunidad['symbol']}")
                print(f"Volatilidad: {mejor_oportunidad['volatility']*100:.2f}%")
                print(f"Señal: {mejor_oportunidad['signal']}")
                
                current_symbol = mejor_oportunidad['symbol']
                ticksize = mejor_oportunidad['ticksize']
                scala_precio = mejor_oportunidad['scala_precio']
                precision_step = mejor_oportunidad['precision_step']
                
                # Calcular cantidad
                qty = usdt / mejor_oportunidad['price']
                qty = qty_precision(qty, precision_step)
                if qty.is_integer():
                    qty = int(qty)
                    
                print(f"Cantidad de monedas: {qty}")
                
                # Crear orden
                crear_orden(current_symbol, mejor_oportunidad['signal'], "Market", qty)
                tipo = "long" if mejor_oportunidad['signal'] == "Buy" else "short"
            else:
                print("No se encontraron oportunidades que cumplan los criterios. Volatilidad insuficiente.")
                
        time.sleep(10)  # Esperar 1 minuto entre evaluaciones
        
    except Exception as e:
        print(f"Error en el bot: {e}")
        time.sleep(60)