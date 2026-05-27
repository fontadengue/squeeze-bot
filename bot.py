import os
import time
import requests
import numpy as np
from datetime import datetime

TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SYMBOL    = "XDGUSD"
INTERVAL  = 60
CHECK_SEC = 300

BB_LEN  = 20
BB_MULT = 2.0
KC_LEN  = 20
KC_MULT = 1.5
MOM_LEN = 20

def get_candles():
    url = "https://api.kraken.com/0/public/OHLC"
    r = requests.get(url, params={"pair": SYMBOL, "interval": INTERVAL}, timeout=10)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise Exception(f"Kraken error: {data['error']}")
    key = list(data["result"].keys())[0]
    raw = data["result"][key][-150:]
    opens  = np.array([float(c[1]) for c in raw])
    highs  = np.array([float(c[2]) for c in raw])
    lows   = np.array([float(c[3]) for c in raw])
    closes = np.array([float(c[4]) for c in raw])
    return opens, highs, lows, closes

def sma(arr, n):
    return np.array([np.mean(arr[i-n:i]) for i in range(n, len(arr)+1)])

def ema(arr, n):
    result = np.zeros(len(arr))
    k = 2 / (n + 1)
    result[0] = arr[0]
    for i in range(1, len(arr)):
        result[i] = arr[i] * k + result[i-1] * (1 - k)
    return result

def stdev(arr, n):
    return np.array([np.std(arr[i-n:i], ddof=0) for i in range(n, len(arr)+1)])

def linreg_last(arr, n):
    results = []
    x = np.arange(n)
    for i in range(n, len(arr)+1):
        y = arr[i-n:i]
        coef = np.polyfit(x, y, 1)
        results.append(np.polyval(coef, n-1))
    return np.array(results)

def calc_momentum(opens, highs, lows, closes):
    # BB
    basis_bb = sma(closes, BB_LEN)
    dev_bb   = stdev(closes, BB_LEN) * BB_MULT
    upper_bb = basis_bb + dev_bb
    lower_bb = basis_bb - dev_bb

    # Alinear desde BB_LEN
    cl = closes[BB_LEN:]
    hi = highs[BB_LEN:]
    lo = lows[BB_LEN:]
    op = opens[BB_LEN:]

    # KC
    tr = np.maximum(
        highs[1:] - lows[1:],
        np.maximum(np.abs(highs[1:] - closes[:-1]),
                   np.abs(lows[1:]  - closes[:-1]))
    )
    tr_full = np.concatenate([[tr[0]], tr])
    atr_kc  = ema(tr_full, KC_LEN)[BB_LEN:]
    basis_kc = ema(closes, KC_LEN)[BB_LEN:]
    upper_kc = basis_kc + KC_MULT * atr_kc
    lower_kc = basis_kc - KC_MULT * atr_kc

    # Momentum
    size = len(cl)
    hh = np.array([np.max(hi[max(0,i-MOM_LEN+1):i+1]) for i in range(size)])
    ll = np.array([np.min(lo[max(0,i-MOM_LEN+1):i+1]) for i in range(size)])
    mid_hl = (hh + ll) / 2
    mid_bb = (upper_bb + lower_bb) / 2
    delta  = cl - (mid_hl + mid_bb) / 2
    mom    = linreg_last(delta, MOM_LEN)

    # Alinear opens/closes con mom
    offset = MOM_LEN - 1
    return mom, op[offset:], cl[offset:]

def check_signal(mom, op_al, cl_al):
    if len(mom) < 3:
        return False, {}
    cur   = mom[-1]
    prev  = mom[-2]
    prev2 = mom[-3]
    is_orange    = cur  < 0 and cur  > prev
    was_red      = prev < 0 and prev < prev2
    green_candle = cl_al[-1] > op_al[-1]
    signal = was_red and is_orange and green_candle
    info = {
        "mom_cur":  round(cur,  8),
        "mom_prev": round(prev, 8),
        "close":    round(cl_al[-1], 6),
        "green":    green_candle,
    }
    return signal, info

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }, timeout=10)
    return r.ok

def main():
    print(f"[{datetime.now():%H:%M:%S}] Bot iniciado")
    send_telegram(
        "🤖 <b>Squeeze Bot activo</b>\n"
        "📊 Par: DOGE/USDT — Kraken\n"
        "⏰ Timeframe: 1H\n"
        "🎯 Señal: Momentum Rojo → Naranja + Vela Verde"
    )
    last_signal_close = None

    while True:
        try:
            opens, highs, lows, closes = get_candles()
            mom, op_al, cl_al = calc_momentum(opens, highs, lows, closes)
            signal, info = check_signal(mom, op_al, cl_al)
            now = datetime.now().strftime("%H:%M:%S")

            if signal and last_signal_close != cl_al[-1]:
                last_signal_close = cl_al[-1]
                send_telegram(
                    f"🟢 <b>SEÑAL DE COMPRA — DOGE/USDT</b>\n"
                    f"⏰ Timeframe: 1H\n"
                    f"📈 Squeeze Momentum: ROJO → NARANJA\n"
                    f"🕯️ Vela verde confirmada\n"
                    f"💰 Precio: <b>{info['close']}</b> USDT\n"
                    f"📊 Momentum: {info['mom_cur']}"
                )
                print(f"[{now}] ✅ SEÑAL enviada | precio: {info['close']}")
            else:
                print(f"[{now}] Sin señal | mom: {info.get('mom_cur','?')} | verde: {info.get('green','?')}")

        except Exception as e:
            print(f"[{datetime.now():%H:%M:%S}] Error: {e}")

        time.sleep(CHECK_SEC)

if __name__ == "__main__":
    main()
