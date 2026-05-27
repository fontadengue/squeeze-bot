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
    raw = data["result"][key][-200:]
    opens  = np.array([float(c[1]) for c in raw])
    highs  = np.array([float(c[2]) for c in raw])
    lows   = np.array([float(c[3]) for c in raw])
    closes = np.array([float(c[4]) for c in raw])
    return opens, highs, lows, closes

def ema(arr, n):
    result = np.zeros(len(arr))
    k = 2 / (n + 1)
    result[0] = arr[0]
    for i in range(1, len(arr)):
        result[i] = arr[i] * k + result[i-1] * (1 - k)
    return result

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

    # --- Bollinger Bands ---
    bb_basis = np.array([np.mean(closes[i-BB_LEN:i]) for i in range(BB_LEN, N+1)])
    bb_std   = np.array([np.std(closes[i-BB_LEN:i], ddof=0) for i in range(BB_LEN, N+1)])
    upper_bb = bb_basis + BB_MULT * bb_std
    lower_bb = bb_basis - BB_MULT * bb_std
    # bb arrays tienen longitud N - BB_LEN + 1... usamos desde índice BB_LEN-1
    # Alineamos: índice i en bb corresponde a closes[BB_LEN-1 + i]

    # --- True Range ---
    tr = np.zeros(N)
    tr[0] = highs[0] - lows[0]
    for i in range(1, N):
        tr[i] = max(highs[i] - lows[i],
                    abs(highs[i] - closes[i-1]),
                    abs(lows[i]  - closes[i-1]))

    # --- KC ---
    atr_kc   = ema(tr, KC_LEN)
    basis_kc = ema(closes, KC_LEN)
    upper_kc = basis_kc + KC_MULT * atr_kc
    lower_kc = basis_kc - KC_MULT * atr_kc

    # --- Alinear todo desde BB_LEN-1 ---
    start = BB_LEN - 1
    cl = closes[start:]
    hi = highs[start:]
    lo = lows[start:]
    op = opens[start:]
    ub = upper_bb   # longitud: N - BB_LEN + 1 = len(cl)
    lb = lower_bb
    ukc = upper_kc[start:]
    lkc = lower_kc[start:]

    L = len(cl)  # todos tienen la misma longitud ahora

    # --- Momentum delta ---
    hh    = np.array([np.max(hi[max(0,i-MOM_LEN+1):i+1]) for i in range(L)])
    ll    = np.array([np.min(lo[max(0,i-MOM_LEN+1):i+1]) for i in range(L)])
    mid_hl = (hh + ll) / 2
    mid_bb = (ub + lb) / 2
    delta  = cl - (mid_hl + mid_bb) / 2

    mom = linreg_series(delta, MOM_LEN)
    # mom tiene longitud L - MOM_LEN + 1
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
    info = {
        "mom_cur":  round(float(cur),      8),
        "mom_prev": round(float(prev),     8),
        "close":    round(float(cl_al[-1]),6),
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
    if not r.ok:
        print(f"Telegram error: {r.text}")
    return r.ok

def main():
    print(f"[{datetime.now():%H:%M:%S}] Bot iniciado")
    ok = send_telegram(
        "🤖 <b>Squeeze Bot activo</b>\n"
        "📊 Par: DOGE/USDT — Kraken\n"
        "⏰ Timeframe: 1H\n"
        "🎯 Señal: Momentum Rojo → Naranja + Vela Verde"
    )
    print(f"Telegram ping: {'OK' if ok else 'FALLO'}")

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
                print(f"[{now}] SEÑAL enviada | precio: {info['close']}")
            else:
                print(f"[{now}] Sin señal | mom: {info.get('mom_cur','?')} | verde: {info.get('green','?')}")

        except Exception as e:
            print(f"[{datetime.now():%H:%M:%S}] Error: {e}")

        time.sleep(CHECK_SEC)

if __name__ == "__main__":
    main()
