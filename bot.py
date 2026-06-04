import os
import time
import requests
import numpy as np
from datetime import datetime
import pytz

TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SYMBOL   = "XDGUSD"
INTERVAL = 60

BB_LEN  = 20
BB_MULT = 2.0
KC_LEN  = 20
KC_MULT = 1.5
MOM_LEN = 20
ATR_LEN = 13
ATR_MULT = 1.5

CHECK_MINUTES = {50, 59}
TZ = pytz.timezone("America/Argentina/Buenos_Aires")

def get_candles():
    url = "https://api.kraken.com/0/public/OHLC"
    r = requests.get(url, params={"pair": SYMBOL, "interval": INTERVAL}, timeout=10)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise Exception(f"Kraken error: {data['error']}")
    key = list(data["result"].keys())[0]
    raw = data["result"][key][-200:]
    opens  = np.array([float(c[1]) for c in raw])
    highs  = np.array([float(c[2]) for c in raw])
    lows   = np.array([float(c[3]) for c in raw])
    closes = np.array([float(c[4]) for c in raw])
    return opens, highs, lows, closes

def get_bingx_price():
    url = "https://open-api.bingx.com/openApi/swap/v2/quote/price"
    r = requests.get(url, params={"symbol": "DOGE-USDT"}, timeout=10)
    r.raise_for_status()
    data = r.json()
    return float(data["data"]["price"])

def ema(arr, n):
    result = np.zeros(len(arr))
    k = 2 / (n + 1)
    result[0] = arr[0]
    for i in range(1, len(arr)):
        result[i] = arr[i] * k + result[i-1] * (1 - k)
    return result

def calc_atr(highs, lows, closes, n):
    tr = np.zeros(len(closes))
    tr[0] = highs[0] - lows[0]
    for i in range(1, len(closes)):
        tr[i] = max(highs[i] - lows[i],
                    abs(highs[i] - closes[i-1]),
                    abs(lows[i]  - closes[i-1]))
    atr = ema(tr, n)
    return atr[-1]

def linreg_series(arr, n):
    results = []
    x = np.arange(n)
    for i in range(n - 1, len(arr)):
        y = arr[i - n + 1:i + 1]
        coef = np.polyfit(x, y, 1)
        results.append(np.polyval(coef, n - 1))
    return np.array(results)

def calc_momentum(opens, highs, lows, closes):
    N = len(closes)
    bb_basis = np.array([np.mean(closes[i-BB_LEN:i]) for i in range(BB_LEN, N+1)])
    bb_std   = np.array([np.std(closes[i-BB_LEN:i], ddof=0) for i in range(BB_LEN, N+1)])
    upper_bb = bb_basis + BB_MULT * bb_std
    lower_bb = bb_basis - BB_MULT * bb_std

    tr = np.zeros(N)
    tr[0] = highs[0] - lows[0]
    for i in range(1, N):
        tr[i] = max(highs[i] - lows[i],
                    abs(highs[i] - closes[i-1]),
                    abs(lows[i]  - closes[i-1]))

    atr_kc   = ema(tr, KC_LEN)
    basis_kc = ema(closes, KC_LEN)
    upper_kc = basis_kc + KC_MULT * atr_kc
    lower_kc = basis_kc - KC_MULT * atr_kc

    start = BB_LEN - 1
    cl = closes[start:]
    hi = highs[start:]
    lo = lows[start:]
    op = opens[start:]
    ub = upper_bb
    lb = lower_bb

    L = len(cl)
    hh     = np.array([np.max(hi[max(0,i-MOM_LEN+1):i+1]) for i in range(L)])
    ll     = np.array([np.min(lo[max(0,i-MOM_LEN+1):i+1]) for i in range(L)])
    mid_hl = (hh + ll) / 2
    mid_bb = (ub + lb) / 2
    delta  = cl - (mid_hl + mid_bb) / 2
    mom    = linreg_series(delta, MOM_LEN)

    trim = MOM_LEN - 1
    return mom, op[trim:], cl[trim:]

def check_signal(mom, op_al, cl_al):
    if len(mom) < 3:
        return False, {}
    cur   = mom[-1]
    prev  = mom[-2]
    prev2 = mom[-3]
    is_orange    = cur  < 0 and cur  > prev
    was_red      = prev < 0 and prev < prev2
    green_candle = float(cl_al[-1]) > float(op_al[-1])
    signal = was_red and is_orange and green_candle
    return signal, {}

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }, timeout=10)
    if not r.ok:
        print(f"Telegram error: {r.text}")
    return r.ok

def seconds_until_next_check():
    now = datetime.now(TZ)
    current_minute = now.minute
    current_second = now.second
    future = sorted([m for m in CHECK_MINUTES if m > current_minute])
    if future:
        next_minute = future[0]
        wait = (next_minute - current_minute) * 60 - current_second
    else:
        next_minute = min(CHECK_MINUTES)
        minutes_left = (60 - current_minute) + next_minute
        wait = minutes_left * 60 - current_second
    return max(wait, 1)

def main():
    print(f"[{datetime.now(TZ):%H:%M:%S}] Bot iniciado")
    send_telegram(
        "🤖 <b>Squeeze Bot activo</b>\n"
        "📊 Análisis: DOGE/USDT — Kraken 1H\n"
        "📐 ATR 13 períodos\n"
        "💰 Precio señal: BingX perpetuo\n"
        f"🕐 Revisiones: minutos {sorted(CHECK_MINUTES)} de cada hora (GMT-3)"
    )

    last_signal_close = None

    while True:
        wait = seconds_until_next_check()
        now = datetime.now(TZ)
        print(f"[{now:%H:%M:%S}] Próxima revisión en {wait}s")
        time.sleep(wait)

        try:
            now = datetime.now(TZ)
            opens, highs, lows, closes = get_candles()
            mom, op_al, cl_al = calc_momentum(opens, highs, lows, closes)
            signal, _ = check_signal(mom, op_al, cl_al)

            if signal and last_signal_close != cl_al[-1]:
                last_signal_close = cl_al[-1]

                # ATR con datos de Kraken
                atr_value = calc_atr(highs, lows, closes, ATR_LEN)

                # Vela actual en desarrollo: open y close de la última vela
                current_open  = float(opens[-1])
                current_close = float(closes[-1])
                candle_size   = abs(current_close - current_open)
                one_third     = candle_size / 3

                # Precio de entrada: BingX - 1/3 de la vela actual
                bingx_price  = get_bingx_price()
                entry_price  = round(bingx_price - one_third, 6)
                stop_loss    = round(entry_price - (atr_value * ATR_MULT), 6)
                take_profit  = round(entry_price + (atr_value * ATR_MULT), 6)

                msg = (
                    f"Entrada: {entry_price}\n"
                    f"SL: {stop_loss}\n"
                    f"TP: {take_profit}"
                )
                send_telegram(msg)
                print(f"[{now:%H:%M:%S}] SEÑAL | Entrada: {entry_price} | SL: {stop_loss} | TP: {take_profit} | ATR: {round(atr_value,6)}")
            else:
                print(f"[{now:%H:%M:%S}] Sin señal | mom: {mom[-1]:.8f} | verde: {float(cl_al[-1]) > float(op_al[-1])}")

        except Exception as e:
            print(f"[{datetime.now(TZ):%H:%M:%S}] Error: {e}")

if __name__ == "__main__":
    main()
