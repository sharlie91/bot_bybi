from pybit.unified_trading import HTTP
import pandas as pd
import numpy as np
import math
import time
import logging
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

# Configuración inicial
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trading_bot.log'),
        logging.StreamHandler()
    ]
)

# Configuración de riesgo
RISK_PERCENT = 1  # 1% del balance por operación
TP_MULTIPLIER = 2  # Relación riesgo:beneficio 1:2
VOLATILITY_THRESHOLD = 0.5  # % de la banda para filtrar volatilidad
COOLDOWN_PERIOD = 300  # 5 minutos entre operaciones

class TradingBot:
    def __init__(self):
        self.api_key = ""
        self.api_secret = ""
        self.symbol = "XRPUSDT"
        self.timeframe = "5"
        self.client = None
        self.last_trade_time = 0
        self.position_info = {}
        self.ticksize = None
        self.precision_step = None
        self.scala_precio = None
        self.initialize_client()
        self.load_instrument_info()
        
    def initialize_client(self):
        self.client = HTTP(
            api_key=self.api_key,
            api_secret=self.api_secret,
            testnet=False
        )
        
    def load_instrument_info(self):
        info = self.client.get_instruments_info(
            category="linear", 
            symbol=self.symbol
        )['result']['list'][0]
        
        self.ticksize = float(info['priceFilter']['tickSize'])
        self.scala_precio = int(info['priceScale'])
        self.precision_step = float(info['lotSizeFilter']['qtyStep'])
        
    def get_usdt_balance(self):
        response = self.client.get_wallet_balance(
            accountType="UNIFIED",
            coin="USDT"
        )
        if response['retCode'] == 0:
            return float(response['result']['list'][0]['coin'][0]['availableToWithdraw'])
        raise Exception(f"Error balance: {response}")

    def obtener_datos_historicos(self, limit=200):
        try:
            response = self.client.get_kline(
                symbol=self.symbol,
                interval=self.timeframe,
                limit=limit
            )
        
            if response['retCode'] != 0:
                logging.error(f"Error API: {response}")
                return pd.DataFrame()

            raw_data = response['result']['list']
            if not raw_data:
                logging.warning("Datos históricos vacíos")
                return pd.DataFrame()

        # Conversión segura a tipo numérico primero
            data = pd.DataFrame(raw_data, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'
            ])
            
            # Convertir timestamp a numérico explícitamente
            data['timestamp'] = pd.to_numeric(data['timestamp'], errors='coerce')
            data['timestamp'] = pd.to_datetime(data['timestamp'], unit='ms')
        
            # Convertir demás columnas numéricas
            numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'turnover']
            data[numeric_cols] = data[numeric_cols].apply(
                pd.to_numeric, errors='coerce', axis=1
            )
        
            return data.iloc[::-1].reset_index(drop=True).dropna()

        except Exception as e:
            logging.error(f"Error procesamiento datos: {str(e)}")
            return pd.DataFrame()
    
    def calcular_bandas_bollinger(self, data, ventana=20, desviacion=2):
        data['MA'] = data['close'].rolling(ventana).mean()
        data['STD'] = data['close'].rolling(ventana).std()
        data['Upper'] = data['MA'] + data['STD'] * desviacion
        data['Lower'] = data['MA'] - data['STD'] * desviacion
        return data.iloc[-1]

    def calcular_atr(self, data, period=14):
        high_low = data['high'] - data['low']
        high_close = np.abs(data['high'] - data['close'].shift())
        low_close = np.abs(data['low'] - data['close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return tr.rolling(period).mean().iloc[-1]

    def calcular_precision(self, value, step):
        value_dec = Decimal(str(value))
        step_dec = Decimal(str(step))
        return float(value_dec.quantize(step_dec, rounding=ROUND_HALF_UP))

    def size_posicion(self, precio, stop_loss):
        balance = self.get_usdt_balance()
        riesgo_usdt = balance * RISK_PERCENT / 100
        diferencia = abs(precio - stop_loss)
        return self.calcular_precision(riesgo_usdt / diferencia, self.precision_step)

    def gestionar_orden(self, side, precio_entrada, stop_loss):
        try:
            size = self.size_posicion(precio_entrada, stop_loss)
            if size <= 0:
                return
                
            order = self.client.place_order(
                category="linear",
                symbol=self.symbol,
                side=side,
                orderType="Limit",
                qty=str(size),
                price=str(self.calcular_precision(precio_entrada, self.ticksize)),
                takeProfit=str(self.calcular_precision(
                    precio_entrada + (precio_entrada - stop_loss) * TP_MULTIPLIER, 
                    self.ticksize
                )),
                stopLoss=str(self.calcular_precision(stop_loss, self.ticksize)),
                positionIdx=0,
                timeInForce="PostOnly"
            )
            
            if order['retCode'] == 0:
                self.last_trade_time = time.time()
                logging.info(f"Orden exitosa: {order}")
            else:
                logging.error(f"Error orden: {order}")
                
        except Exception as e:
            logging.error(f"Error gestionar orden: {str(e)}")

    def ejecutar_estrategia(self):
        if time.time() - self.last_trade_time < COOLDOWN_PERIOD:
            return
            
        try:
            data = self.obtener_datos_historicos()
            current_price = float(self.client.get_tickers(
                category='linear',
                symbol=self.symbol
            )['result']['list'][0]['lastPrice'])
            
            bollinger = self.calcular_bandas_bollinger(data)
            atr = self.calcular_atr(data)
            
            # Filtro de volatilidad
            volatility_ratio = (bollinger['Upper'] - bollinger['Lower']) / bollinger['MA']
            if volatility_ratio < VOLATILITY_THRESHOLD / 100:
                logging.info("Volatilidad insuficiente")
                return
                
            # Señal de compra
            if current_price < bollinger['Lower'] and \
               data['close'].iloc[-2] > data['Lower'].iloc[-2]:
                stop_loss = current_price - atr
                self.gestionar_orden('Buy', current_price, stop_loss)
                
            # Señal de venta
            elif current_price > bollinger['Upper'] and \
                 data['close'].iloc[-2] < data['Upper'].iloc[-2]:
                stop_loss = current_price + atr
                self.gestionar_orden('Sell', current_price, stop_loss)
                
        except Exception as e:
            logging.error(f"Error estrategia: {str(e)}")

    def monitorear_posiciones(self):
        try:
            positions = self.client.get_positions(
                category="linear",
                symbol=self.symbol
            )['result']['list']
            
            if positions and float(positions[0]['size']) > 0:
                logging.info("Posición activa detectada")
                return True
            return False
            
        except Exception as e:
            logging.error(f"Error monitoreo: {str(e)}")
            return False

    def run(self):
        logging.info("Iniciando bot de trading...")
        while True:
            try:
                if not self.monitorear_posiciones():
                    self.ejecutar_estrategia()
                time.sleep(10)
                
            except Exception as e:
                logging.error(f"Error general: {str(e)}")
                time.sleep(60)
                self.initialize_client()

if __name__ == "__main__":
    bot = TradingBot()
    bot.run()
