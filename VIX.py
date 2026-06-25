from pathlib import Path
import os
import threading
import time

from dotenv import load_dotenv
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract

try:
    from LINE import send_line_message
except ImportError as exc:
    raise ImportError('找不到 LINE.py，請確認 VIX.py 與 LINE.py 在同一資料夾') from exc

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / '.env')

IB_HOST = os.getenv('IB_HOST', '127.0.0.1')
IB_PORT = int(os.getenv('IB_PORT', '4002'))
IB_CLIENT_ID = int(os.getenv('IB_CLIENT_ID', '12'))
VIX_LOW = float(os.getenv('VIX_LOW', '20'))
VIX_HIGH = float(os.getenv('VIX_HIGH', '30'))
POLL_SECONDS = int(os.getenv('POLL_SECONDS', '10'))


class VixApp(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.vix_price = None
        self.last_error = None

    def tickPrice(self, reqId, tickType, price, attrib):
        if price and price > 0:
            self.vix_price = float(price)

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=''):
        self.last_error = (reqId, errorCode, errorString)
        print(f'IB錯誤 reqId={reqId}, code={errorCode}, msg={errorString}')


def make_vix_contract():
    c = Contract()
    c.symbol = 'VIX'
    c.secType = 'IND'
    c.exchange = 'CBOE'
    c.currency = 'USD'
    return c


def run_loop(app: VixApp):
    app.run()


def main():
    app = VixApp()
    app.connect(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)

    api_thread = threading.Thread(target=run_loop, args=(app,), daemon=True)
    api_thread.start()

    time.sleep(2)
    app.reqMarketDataType(3)  # 1=live, 2=frozen, 3=delayed, 4=delayed-frozen
    app.reqMktData(1, make_vix_contract(), '', False, False, [])

    last_state = None
    print(f'開始監控 VIX，條件: < {VIX_LOW} 或 > {VIX_HIGH}')

    try:
        while True:
            if app.vix_price is not None:
                vix = app.vix_price
                state = 'normal'
                if vix < VIX_LOW:
                    state = 'low'
                elif vix > VIX_HIGH:
                    state = 'high'

                print(f'目前 VIX = {vix:.2f}, state = {state}')

                if state != last_state and state in ('low', 'high'):
                    msg = (
                        f'VIX 警報\n'
                        f'目前 VIX = {vix:.2f}\n'
                        f'觸發條件：VIX < {VIX_LOW} 或 VIX > {VIX_HIGH}'
                    )
                    send_line_message(msg)
                    print('已發送 LINE 告警')
                    last_state = state
                elif state == 'normal':
                    last_state = 'normal'

            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print('停止監控')
    finally:
        try:
            app.cancelMktData(1)
        except Exception:
            pass
        app.disconnect()


if __name__ == '__main__':
    main()