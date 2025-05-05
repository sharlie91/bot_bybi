from pybit.unified_trading import HTTP
import pandas as pd
import numpy as np
import math
import time
import logging
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP, InvalidOperation

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
        try:
            response = self.client.get_instruments_info(
                category="linear", 
                symbol=self.symbol
            )
            if response['retCode'] != 0:
                raise Exception(f"Error instrumentos: {response['retMsg']}")
            
            info = response['result']['list'][0]
            
            # Validar y convertir ticksize
            self.ticksize = self.safe_float_conversion(
                info['priceFilter'].get('tickSize', '0.0')
            )
            self.scala_precio = self.safe_int_conversion(
                info.get('priceScale', '0')
            )
            self.precision_step = self.safe_float_conversion(
                info['lotSizeFilter'].get('qtyStep', '0.0')
            )
            
        except (KeyError, IndexError) as e:
            logging.error(f"Error carga instrumentos: {str(e)}")
            raise

    def safe_float_conversion(self, value):
        try:
            return float(value) if str(value).strip() != '' else 0.0
        except ValueError:
            return 0.0

    def safe_int_conversion(self, value):
        try:
            return int(value) if str(value).strip() != '' else 0
        except ValueError:
            return 0

    def get_usdt_balance(self):
        try:
            response = self.client.get_wallet_balance(
                accountType="UNIFIED",
                coin="USDT"
            )
            if response['retCode'] != 0:
                raise Exception(f"Error balance: {response['retMsg']}")
            
            # Validación en profundidad de la estructura de respuesta
            balance_data = response['result']['list'][0]['coin'][0]
            balance_str = balance_data.get('availableToWithdraw', '')
            
            if not balance_str.strip():
                raise ValueError("Saldo no disponible")
                
            return float(balance_str)
            
        except (KeyError, IndexError, ValueError) as e:
            logging.error(f"Error obteniendo balance: {str(e)}")
            return 0.0
        except Exception as e:
            logging.error(f"Error general balance: {str(e)}")
            return 0.0

    def obtener_datos_historicos(self, limit=200):
        try:
            response = self.client.get_kline(
                symbol=self.symbol,
                interval=self.timeframe,
                limit=limit
            )
        
            if response['retCode'] != 0:
                logging.error(f"Error API histórico: {response['retMsg']}")
                return pd.DataFrame()

            raw_data = response['result']['list']
            if not raw_data:
                logging.warning("Datos históricos vacíos")
                return pd.DataFrame()

            data = pd.DataFrame(raw_data, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'
            ])
            
            # Conversión segura de tipos
            data['timestamp'] = pd.to_numeric(data['timestamp'], errors='coerce').astype('int64')
            data['timestamp'] = pd.to_datetime(data['timestamp'], unit='ms')
            
            numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'turnover']
            for col in numeric_cols:
                data[col] = pd.to_numeric(data[col], errors='coerce')
            
            return data.iloc[::-1].reset_index(drop=True).dropna()

        except Exception as e:
            logging.error(f"Error procesamiento datos: {str(e)}")
            return pd.DataFrame()
    
    def calcular_bandas_bollinger(self, data, ventana=20, desviacion=2):
        try:
            data['MA'] = data['close'].rolling(ventana).mean()
            data['STD'] = data['close'].rolling(ventana).std()
            data['Upper'] = data['MA'] + data['STD'] * desviacion
            data['Lower'] = data['MA'] - data['STD'] * desviacion
            return data.iloc[-1]
        except Exception as e:
            logging.error(f"Error cálculo Bollinger: {str(e)}")
            return None

    def calcular_atr(self, data, period=14):
        try:
            high_low = data['high'] - data['low']
            high_close = np.abs(data['high'] - data['close'].shift())
            low_close = np.abs(data['low'] - data['close'].shift())
            tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            return tr.rolling(period).mean().iloc[-1]
        except Exception as e:
            logging.error(f"Error cálculo ATR: {str(e)}")
            return 0.0

    def calcular_precision(self, value, step):
        try:
            value_dec = Decimal(str(value))
            step_dec = Decimal(str(step))
            return float(value_dec.quantize(step_dec, rounding=ROUND_HALF_UP))
        except (ValueError, InvalidOperation) as e:
            logging.error(f"Error precisión: {value} - {step}: {str(e)}")
            return 0.0

    def size_posicion(self, precio, stop_loss):
        try:
            balance = self.get_usdt_balance()
            if balance <= 0:
                logging.warning("Balance no disponible")
                return 0.0
                
            riesgo_usdt = balance * RISK_PERCENT / 100
            diferencia = abs(precio - stop_loss)
            
            if diferencia <= 0:
                logging.error("Diferencia precio/stopLoss inválida")
                return 0.0
                
            size = riesgo_usdt / diferencia
            return self.calcular_precision(size, self.precision_step)
            
        except Exception as e:
            logging.error(f"Error cálculo tamaño posición: {str(e)}")
            return 0.0

    def gestionar_orden(self, side, precio_entrada, stop_loss):
        try:
            if precio_entrada <= 0 or stop_loss <= 0:
                raise ValueError("Precios inválidos")
                
            size = self.size_posicion(precio_entrada, stop_loss)
            if size <= 0:
                logging.warning("Tamaño de posición inválido")
                return
                
            precio_limit = self.calcular_precision(precio_entrada, self.ticksize)
            take_profit = self.calcular_precision(
                precio_entrada + (precio_entrada - stop_loss) * TP_MULTIPLIER, 
                self.ticksize
            )
            stop_loss = self.calcular_precision(stop_loss, self.ticksize)
            
            if any(val <= 0 for val in [precio_limit, take_profit, stop_loss]):
                raise ValueError("Valores de orden inválidos")
                
            order = self.client.place_order(
                category="linear",
                symbol=self.symbol,
                side=side,
                orderType="Limit",
                qty=str(size),
                price=str(precio_limit),
                takeProfit=str(take_profit),
                stopLoss=str(stop_loss),
                positionIdx=0,
                timeInForce="PostOnly"
            )
            
            if order['retCode'] == 0:
                self.last_trade_time = time.time()
                logging.info(f"Orden exitosa: {order['result']}")
            else:
                logging.error(f"Error orden: {order['retMsg']}")
                
        except Exception as e:
            logging.error(f"Error gestión orden: {str(e)}")

    def ejecutar_estrategia(self):
        if time.time() - self.last_trade_time < COOLDOWN_PERIOD:
            return
            
        try:
            # Validación de datos históricos
            data = self.obtener_datos_historicos()
            if data.empty or len(data) < 20:
                logging.warning("Datos insuficientes para análisis")
                return
                
            # Validación precio actual
            ticker = self.client.get_tickers(
                category='linear',
                symbol=self.symbol
            )
            if ticker['retCode'] != 0:
                logging.error(f"Error ticker: {ticker['retMsg']}")
                return
                
            if not ticker['result']['list']:
                logging.error("Datos de precio vacíos")
                return
                
            last_price_str = ticker['result']['list'][0].get('lastPrice', '')
            if not last_price_str.strip():
                logging.error("Precio no disponible")
                return
                
            current_price = self.safe_float_conversion(last_price_str)
            if current_price <= 0:
                raise ValueError("Precio actual inválido")
            
            # Cálculos técnicos
            bollinger = self.calcular_bandas_bollinger(data)
            if bollinger is None:
                return
                
            atr = self.calcular_atr(data)
            if atr <= 0:
                logging.warning("ATR inválido")
                return
                
            # Filtro de volatilidad
            volatility_ratio = (bollinger['Upper'] - bollinger['Lower']) / bollinger['MA']
            if volatility_ratio < VOLATILITY_THRESHOLD / 100:
                logging.info("Volatilidad insuficiente")
                return
                
            # Validación señales
            if current_price < bollinger['Lower'] and \
               data['close'].iloc[-2] > data['Lower'].iloc[-2]:
                stop_loss = current_price - atr
                self.gestionar_orden('Buy', current_price, stop_loss)
                
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
            )
            if positions['retCode'] != 0:
                logging.error(f"Error posiciones: {positions['retMsg']}")
                return False
                
            if positions['result']['list']:
                position_size = self.safe_float_conversion(
                    positions['result']['list'][0]['size']
                )
                return position_size > 0
            return False
            
        except Exception as e:
            logging.error(f"Error monitoreo posiciones: {str(e)}")
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
