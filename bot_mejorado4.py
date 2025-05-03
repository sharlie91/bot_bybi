from pybit.unified_trading import HTTP
import pandas as pd
import numpy as np
import time
import logging
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trading_bot.log'),
        logging.StreamHandler()
    ]
)

# Parámetros estratégicos
RISK_PERCENT = 1
TP_MULTIPLIER = 2
VOLATILITY_THRESHOLD = 0.5
COOLDOWN_PERIOD = 300
MINIMUM_BALANCE = 4  # Saldo mínimo requerido para operar

class TradingBot:
    def __init__(self):
        self.api_key = "8eTibTFVF3eBprPvjo"
        self.api_secret = "ejLpGJuYNYNIMf5W7sKs0NHvlsDfvFxkVTBz"
        self.symbol = "OMUSDT"
        self.timeframe = "5"
        self.client = None
        self.last_trade_time = 0
        self.ticksize = 0.0
        self.precision_step = 0.0
        self.scala_precio = 0
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
            
            if not response['result']['list']:
                raise Exception("Instrumento no encontrado")
            
            info = response['result']['list'][0]
            
            # Extracción y validación de parámetros
            self.ticksize = self.safe_float_conversion(
                info.get('priceFilter', {}).get('tickSize', '0.0')
            )
            self.scala_precio = self.safe_int_conversion(
                info.get('priceScale', '0')
            )
            self.precision_step = self.safe_float_conversion(
                info.get('lotSizeFilter', {}).get('qtyStep', '0.0')
            )
            
            if any(val <= 0 for val in [self.ticksize, self.scala_precio, self.precision_step]):
                raise Exception("Parámetros del instrumento inválidos")
                
        except Exception as e:
            logging.error(f"Error crítico en instrumentos: {str(e)}")
            raise

    def safe_float_conversion(self, value):
        try:
            return float(str(value).strip()) if str(value).strip() else 0.0
        except:
            return 0.0

    def safe_int_conversion(self, value):
        try:
            return int(str(value).strip()) if str(value).strip() else 0
        except:
            return 0

    def get_usdt_balance(self):
        try:
            response = self.client.get_wallet_balance(
                accountType="UNIFIED",
                coin="USDT"
            )
            
            if response['retCode'] != 0:
                logging.error(f"Error API balance: {response['retMsg']}")
                return 0.0

            # Validación de estructura de respuesta
            account_list = response['result'].get('list', [])
            if not account_list:
                logging.warning("Estructura de cuenta no válida")
                return 0.0
                
            coin_list = account_list[0].get('coin', [])
            if not coin_list:
                logging.warning("No se encontraron monedas en la cuenta")
                return 0.0
                
            usdt_data = next(
                (item for item in coin_list if item.get('coin') == 'USDT'),
                None
            )
            
            if not usdt_data:
                logging.warning("USDT no encontrado en el balance")
                return 0.0
                
            # Usar walletBalance como respaldo
            balance_str = usdt_data.get('availableBalance') or usdt_data.get('walletBalance', '')
            
            if not str(balance_str).strip():
                logging.warning(f"Saldo vacío. Wallet Balance: {usdt_data.get('walletBalance', '0.0')}")
                return 0.0
                
            return float(balance_str)
            
        except Exception as e:
            logging.error(f"Error crítico obteniendo balance: {str(e)}")
            return 0.0

    def obtener_datos_historicos(self, limit=200):
        try:
            response = self.client.get_kline(
                symbol=self.symbol,
                interval=self.timeframe,
                limit=limit
            )
            
            if response['retCode'] != 0:
                logging.error(f"Error histórico: {response['retMsg']}")
                return pd.DataFrame()

            raw_data = response['result'].get('list', [])
            if not raw_data:
                logging.warning("Datos históricos vacíos")
                return pd.DataFrame()

            # Procesamiento seguro de datos
            data = pd.DataFrame(raw_data, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'
            ])
            
            # Conversión de tipos con validación
            data['timestamp'] = pd.to_datetime(
                pd.to_numeric(data['timestamp'], errors='coerce'), 
                unit='ms'
            )
            
            numeric_cols = ['open', 'high', 'low', 'close']
            for col in numeric_cols:
                data[col] = pd.to_numeric(data[col], errors='coerce')
                
            return data.iloc[::-1].dropna()

        except Exception as e:
            logging.error(f"Error procesando datos: {str(e)}")
            return pd.DataFrame()

    def calcular_bandas_bollinger(self, data, ventana=20, desviacion=2):
        try:
            data['MA'] = data['close'].rolling(ventana).mean()
            data['STD'] = data['close'].rolling(ventana).std()
            data['Upper'] = data['MA'] + (data['STD'] * desviacion)
            data['Lower'] = data['MA'] - (data['STD'] * desviacion)
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
        except:
            logging.error("Error en precisión numérica")
            return 0.0

    def size_posicion(self, precio, stop_loss):
        try:
            balance = self.get_usdt_balance()
            
            # Validación de saldo mínimo
            if balance < MINIMUM_BALANCE:
                logging.warning(f"Balance insuficiente: {balance:.2f} USDT")
                return 0.0
                
            riesgo_usdt = balance * RISK_PERCENT / 100
            diferencia = abs(precio - stop_loss)
            
            # Validación de diferencia de precio
            if diferencia <= self.ticksize:
                logging.error(f"Diferencia precio/SL menor que tick size ({self.ticksize})")
                return 0.0
                
            return max(
                self.calcular_precision(riesgo_usdt / diferencia, self.precision_step),
                0.0
            )
            
        except Exception as e:
            logging.error(f"Error cálculo tamaño posición: {str(e)}")
            return 0.0

    def gestionar_orden(self, side, precio_entrada, stop_loss):
        try:
            # Validación estricta de parámetros
            if any(not isinstance(x, (int, float)) or x <= 0 
                   for x in [precio_entrada, stop_loss]):
                raise ValueError("Precios inválidos")
                
            size = self.size_posicion(precio_entrada, stop_loss)
            if size <= 0:
                logging.warning("Tamaño de posición inválido, omitiendo orden")
                return
                
            # Cálculo de niveles con validación
            precio_limit = self.calcular_precision(precio_entrada, self.ticksize)
            take_profit = self.calcular_precision(
                precio_entrada + (precio_entrada - stop_loss) * TP_MULTIPLIER,
                self.ticksize
            )
            stop_loss = self.calcular_precision(stop_loss, self.ticksize)
            
            # Validación final de niveles
            if any(val <= 0 for val in [precio_limit, take_profit, stop_loss]):
                raise ValueError("Niveles de orden inválidos")
                
            # Envío de orden
            order = self.client.place_order(
                category="linear",
                symbol=self.symbol,
                side=side,
                orderType="Limit",
                qty=str(round(size, 8)),
                price=str(precio_limit),
                takeProfit=str(take_profit),
                stopLoss=str(stop_loss),
                positionIdx=0,
                timeInForce="PostOnly"
            )
            
            if order['retCode'] == 0:
                self.last_trade_time = time.time()
                logging.info(f"Orden exitosa - ID: {order['result']['orderId']}")
            else:
                logging.error(f"Error en orden: {order['retMsg']}")
                
        except Exception as e:
            logging.error(f"Error gestión de orden: {str(e)}")

    def ejecutar_estrategia(self):
        if time.time() - self.last_trade_time < COOLDOWN_PERIOD:
            return
            
        try:
            # Obtención y validación de datos
            data = self.obtener_datos_historicos()
            if len(data) < 50 or data.empty:
                logging.warning("Datos insuficientes para análisis")
                return
                
            # Obtención de precio actual
            ticker = self.client.get_tickers(category='linear', symbol=self.symbol)
            if ticker['retCode'] != 0:
                logging.error(f"Error ticker: {ticker['retMsg']}")
                return
                
            if not ticker['result']['list']:
                logging.error("Datos de precio vacíos")
                return
                
            last_price = self.safe_float_conversion(
                ticker['result']['list'][0].get('lastPrice', '0'))
            
            if last_price <= 0:
                raise ValueError("Precio actual inválido")
            
            # Cálculo de indicadores
            bollinger = self.calcular_bandas_bollinger(data)
            if bollinger is None:
                return
                
            atr = self.calcular_atr(data)
            if atr <= 0:
                logging.warning("ATR no válido, omitiendo señal")
                return
                
            # Filtro de volatilidad
            volatility_ratio = (bollinger['Upper'] - bollinger['Lower']) / bollinger['MA']
            if volatility_ratio < (VOLATILITY_THRESHOLD / 100):
                logging.info("Volatilidad por debajo del umbral requerido")
                return
                
            # Generación de señales
            if (last_price < bollinger['Lower'] and 
                data['close'].iloc[-2] > data['Lower'].iloc[-2]):
                self.gestionar_orden('Buy', last_price, last_price - atr)
                
            elif (last_price > bollinger['Upper'] and 
                  data['close'].iloc[-2] < data['Upper'].iloc[-2]):
                self.gestionar_orden('Sell', last_price, last_price + atr)
                
        except Exception as e:
            logging.error(f"Error en ejecución de estrategia: {str(e)}")

    def monitorear_posiciones(self):
        try:
            positions = self.client.get_positions(
                category="linear",
                symbol=self.symbol
            )
            
            if positions['retCode'] != 0:
                logging.error(f"Error consultando posiciones: {positions['retMsg']}")
                return False
                
            if positions['result']['list']:
                size = self.safe_float_conversion(
                    positions['result']['list'][0]['size']
                )
                return size > 0
            return False
            
        except Exception as e:
            logging.error(f"Error en monitoreo: {str(e)}")
            return False

    def run(self):
        logging.info("Iniciando bot de trading...")
        while True:
            try:
                if not self.monitorear_posiciones():
                    self.ejecutar_estrategia()
                time.sleep(10)
            except Exception as e:
                logging.error(f"Error general del sistema: {str(e)}")
                time.sleep(60)
                self.initialize_client()

if __name__ == "__main__":
    bot = TradingBot()
    bot.run()