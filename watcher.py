#!/usr/bin/env python3
"""
Surveillant INTRADAY crypto Kraken - "TOUT VERT" -> proposition de trade par Telegram.

- Donnees publiques Kraken uniquement (AUCUNE cle API, aucun acces a ton argent).
- Memes criteres que ton scanner TradingView : EMA 9/21/50, RSI 14, Bollinger %B,
  volume, tendance 15m + 4h, regime BTC 4h.
- Lecture sur BOUGIE CLOTUREE (pas de clignotement).
- Quand une crypto passe tout vert, envoie une proposition complete : entree, SL, TP, quantite.
- N'execute RIEN : tu valides et tu passes l'ordre toi-meme sur Kraken.

Secrets attendus en variables d'environnement :
  TELEGRAM_TOKEN   = le token de ton bot Telegram
  TELEGRAM_CHAT_ID = l'identifiant de ta conversation avec le bot
"""

import os
import json
import time
import sys
import requests
import pandas as pd

# ----------------------- REGLAGES -----------------------
# Liste Kraken (USDC privilegie, USD en repli). BTC en 1er = regime.
CRYPTOS = [
    "XBTUSDC", "ETHUSDC", "SOLUSDC", "XRPUSDC", "ADAUSDC",
    "LINKUSDC", "AVAXUSDC", "DOTUSD", "LTCUSDC", "BCHUSDC",
    "XLMUSD", "UNIUSD", "NEARUSD", "APTUSD", "SUIUSD",
    "TRXUSD", "HYPEUSD",
]

CAPITAL      = 3500.0   # taille de ta poche jeu (USDC)
RISK_PCT     = 1.0      # risque par trade, en %
ATR_MULT     = 2.0      # stop = ATR x ce facteur (intraday)
RR           = 2.0      # objectif = risque x ce ratio

RSI_MIN      = 45.0
RSI_MAX      = 68.0
BB_MAX_B     = 0.85     # %B max (anti-etire)
VOL_MULT     = 1.0      # volume mini (x moyenne 20)
VOL_REQUIRED = True     # volume exige pour "tout vert" (comme ton scanner intraday)
USE_REGIME   = True     # BTC 4h haussier requis

STATE_FILE   = "state.json"
KRAKEN_URL   = "https://api.kraken.com/0/public/OHLC"
# --------------------------------------------------------


def fetch_ohlc(pair, interval):
    """Recupere les bougies OHLC Kraken. interval en minutes (15 ou 240)."""
    r = requests.get(KRAKEN_URL, params={"pair": pair, "interval": interval}, timeout=20)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise ValueError(f"{pair}: {data['error']}")
    result = data["result"]
    key = next(k for k in result.keys() if k != "last")
    rows = result[key]
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "vwap", "volume", "count"])
    for c in ["open", "high", "low", "close", "vwap", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def ema(series, n):
    return series.ewm(span=n, adjust=False).mean()


def rma(series, n):
    # Moyenne de Wilder (comme TradingView ta.rma / ta.rsi / ta.atr)
    return series.ewm(alpha=1.0 / n, adjust=False).mean()


def rsi(series, n=14):
    delta = series.diff()
    up = delta.clip(lower=0.0)
    down = (-delta).clip(lower=0.0)
    rs = rma(up, n) / rma(down, n)
    return 100.0 - 100.0 / (1.0 + rs)


def atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return rma(tr, n)


def trend_up(df):
    """Tendance haussiere : EMA 9>21>50 empilees ET cloture > EMA50, sur bougie cloturee."""
    ef = ema(df["close"], 9)
    es = ema(df["close"], 21)
    el = ema(df["close"], 50)
    i = -2  # derniere bougie CLOTUREE
    return bool(ef.iloc[i] > es.iloc[i] and es.iloc[i] > el.iloc[i] and df["close"].iloc[i] > el.iloc[i])


def analyse(pair):
    """Retourne un dict d'etat + plan de trade pour une crypto, ou None si indisponible."""
    d15 = fetch_ohlc(pair, 15)
    d4h = fetch_ohlc(pair, 240)
    if len(d15) < 60 or len(d4h) < 60:
        return None

    i = -2  # bougie cloturee sur le 15m
    close = d15["close"].iloc[i]
    ef = ema(d15["close"], 9)
    es = ema(d15["close"], 21)
    el = ema(d15["close"], 50)
    r = rsi(d15["close"], 14)
    mid = d15["close"].rolling(20).mean()
    std = d15["close"].rolling(20).std(ddof=0)
    upper = mid + 2.0 * std
    lower = mid - 2.0 * std
    pctb = (close - lower.iloc[i]) / (upper.iloc[i] - lower.iloc[i])
    vol = d15["volume"].iloc[i]
    vol_avg = d15["volume"].rolling(20).mean().iloc[i]
    a = atr(d15, 14).iloc[i]

    tr15 = bool(ef.iloc[i] > es.iloc[i] and es.iloc[i] > el.iloc[i] and close > el.iloc[i])
    tr4h = trend_up(d4h)
    vok = bool(vol > vol_avg * VOL_MULT)
    nex = bool(pctb < BB_MAX_B)
    rok = bool(RSI_MIN <= r.iloc[i] <= RSI_MAX)

    entree = min(close, ef.iloc[i])
    risk_u = ATR_MULT * a
    sl = entree - risk_u
    tp = entree + risk_u * RR
    sl_pct = (entree - sl) / entree * 100.0
    tp_pct = (tp - entree) / entree * 100.0
    qte = (CAPITAL * RISK_PCT / 100.0) / risk_u if risk_u > 0 else None

    return {
        "pair": pair, "close": close, "tr15": tr15, "tr4h": tr4h,
        "vok": vok, "nex": nex, "rok": rok, "rsi": r.iloc[i], "pctb": pctb,
        "entree": entree, "sl": sl, "tp": tp, "sl_pct": sl_pct, "tp_pct": tp_pct,
        "qte": qte,
    }


def is_green(a, regime_ok):
    g = a["tr15"] and a["tr4h"] and a["nex"] and a["rok"] and regime_ok
    if VOL_REQUIRED:
        g = g and a["vok"]
    return bool(g)


def nom(pair):
    base = pair.replace("USDC", "").replace("USD", "")
    return "BTC" if base == "XBT" else base


def fmt_proposal(a):
    p = nom(a["pair"])
    q = f"{a['qte']:.4f}" if a["qte"] else "—"
    return (
        f"🟢 TOUT VERT — {p}\n\n"
        f"Prix actuel : {a['close']:.6g}\n"
        f"➡️ Entrée idéale : {a['entree']:.6g}\n"
        f"🛑 Stop (SL) : {a['sl']:.6g}  (-{a['sl_pct']:.1f}%)\n"
        f"🎯 Objectif (TP) : {a['tp']:.6g}  (+{a['tp_pct']:.1f}%)\n"
        f"📦 Quantité (risque {RISK_PCT:.0f}%) : {q} {p}\n"
        f"RSI {a['rsi']:.0f} · %B {a['pctb']:.2f}\n\n"
        f"⚠️ Vérifie et passe l'OCO toi-même sur Kraken. Rien n'est exécuté automatiquement."
    )


def send_telegram(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("TELEGRAM_TOKEN / TELEGRAM_CHAT_ID manquants — message non envoye :\n" + text)
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=20)
    except Exception as e:
        print(f"Echec envoi Telegram : {e}")


def load_state():
    try:
        with open(STATE_FILE) as f:
            return set(json.load(f).get("green", []))
    except Exception:
        return set()


def save_state(green_set):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"green": sorted(green_set), "updated": int(time.time())}, f)
    except Exception as e:
        print(f"Echec ecriture state : {e}")


def main():
    prev_green = load_state()

    # 1) Regime BTC (4h) — BTC est le 1er de la liste
    try:
        btc = analyse(CRYPTOS[0])
        regime_ok = (not USE_REGIME) or (btc is not None and btc["tr4h"])
    except Exception as e:
        print(f"Erreur BTC : {e}")
        regime_ok = False

    now_green = set()
    for pair in CRYPTOS:
        try:
            a = analyse(pair)
        except Exception as e:
            print(f"  {pair}: indisponible ({e})")
            continue
        if a is None:
            continue
        if is_green(a, regime_ok):
            now_green.add(pair)
            # Notifier seulement au PASSAGE au vert (pas a chaque bougie tant que vert)
            if pair not in prev_green:
                send_telegram(fmt_proposal(a))
                print(f"  {nom(pair)}: NOUVEAU TOUT VERT -> notifie")
            else:
                print(f"  {nom(pair)}: toujours vert (deja notifie)")

    regime_txt = "VERT" if regime_ok else "ROUGE (aucun trade)"
    print(f"Regime BTC 4h : {regime_txt} | verts : {sorted(nom(p) for p in now_green) or 'aucun'}")
    save_state(now_green)


if __name__ == "__main__":
    main()
