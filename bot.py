# Bot de Trading Memecoins sur Bitget avec Suivi des Whales
import os
import requests
import hmac
import hashlib
import time
import threading

# Configuration API Bitget (les clés doivent être dans les variables d'environnement)
API_KEY = os.getenv('BITGET_API_KEY')
SECRET_KEY = os.getenv('BITGET_SECRET_KEY')
PASSPHRASE = os.getenv('BITGET_PASSPHRASE')
BASE_URL = "https://api.bitget.com"

# Configuration API Telegram
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# Gestion des erreurs de configuration
if not all([API_KEY, SECRET_KEY, PASSPHRASE]):
    raise ValueError("Les clés API Bitget ne sont pas configurées correctement.")

# Fonction de signature des requêtes Bitget
def sign_request(path, method, params=None):
    timestamp = str(int(time.time() * 1000))
    msg = timestamp + method + path + (params if params else "")
    signature = hmac.new(SECRET_KEY.encode(), msg.encode(), hashlib.sha256).hexdigest()

    headers = {
        'ACCESS-KEY': API_KEY,
        'ACCESS-SIGN': signature,
        'ACCESS-TIMESTAMP': timestamp,
        'ACCESS-PASSPHRASE': PASSPHRASE,
        'Content-Type': 'application/json'
    }

    return headers

# Fonction pour envoyer une notification Telegram
def send_telegram_message(message):
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {'chat_id': TELEGRAM_CHAT_ID, 'text': message}
        requests.post(url, data=data)

# Fonction pour recevoir les configurations depuis Telegram
def configure_from_telegram():
    send_telegram_message("🔧 Envoyez votre configuration : /set take_profit stop_loss (ex: /set 5 2)")

# Fonction de traitement des messages Telegram reçus
def handle_telegram_update(update):
    message = update.get("message", {}).get("text", "")
    if message.startswith("/set"):
        parts = message.split()
        if len(parts) == 3:
            tp = float(parts[1])
            sl = float(parts[2])
            send_telegram_message(f"✅ Configuration mise à jour : TP {tp}% | SL {sl}%")
            return tp, sl

    return 5, 2  # Par défaut : TP +5%, SL -2%

# Fonction de trading de base (scalping) avec achat/vente
def get_technical_indicators(token):
    endpoint = f"{BASE_URL}/api/spot/v1/market/candles?symbol={token}&period=1m&limit=50"
    response = requests.get(endpoint)
    if response.status_code != 200:
        print("Erreur lors de la récupération des chandeliers.")
        return None

    data = response.json().get('data', [])
    closes = [float(c[4]) for c in data[::-1]]  # Derniers prix de clôture

    if len(closes) < 14:
        return None

    # RSI 14
    gains = [closes[i] - closes[i - 1] for i in range(1, len(closes)) if closes[i] > closes[i - 1]]
    losses = [closes[i - 1] - closes[i] for i in range(1, len(closes)) if closes[i] < closes[i - 1]]
    avg_gain = sum(gains[-14:]) / 14 if len(gains) >= 14 else 0
    avg_loss = sum(losses[-14:]) / 14 if len(losses) >= 14 else 1  # éviter division par 0
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    # EMA 12 et EMA 26
    ema12 = sum(closes[-12:]) / 12
    ema26 = sum(closes[-26:]) / 26

    # Volume moyen (à partir des chandeliers)
    volumes = [float(c[5]) for c in data[::-1]]
    avg_volume = sum(volumes[-14:]) / 14

    return {
        'rsi': rsi,
        'ema12': ema12,
        'ema26': ema26,
        'avg_volume': avg_volume,
        'last_close': closes[-1]
    }

def trade_scalping(token, amount):
    indicators = get_technical_indicators(token)
    if not indicators:
        print("❌ Impossible de récupérer les indicateurs pour cette paire.")
        send_telegram_message(f"❌ Indicateurs indisponibles pour {token}, le bot ignore cette paire.")
        return

    rsi = indicators['rsi']
    ema12 = indicators['ema12']
    ema26 = indicators['ema26']

    if rsi > 70:
        print(f"❌ RSI trop élevé ({rsi:.2f}). Surachat probable.")
        send_telegram_message(f"❌ RSI trop élevé ({rsi:.2f}) sur {token}, pas d'achat.")
        return
    if ema12 < ema26:
        print(f"❌ Tendance baissière (EMA12 < EMA26). Trade ignoré.")
        send_telegram_message(f"❌ Tendance baissière détectée sur {token}, pas d'achat.")
        return
    print(f"Scalping sur {token} avec {amount} USDT")
    configure_from_telegram()

    take_profit, stop_loss = 5, 2
    url = f"{BASE_URL}/api/spot/v1/trade/orders"

    # Achat
    order_data = {"symbol": token, "side": "buy", "type": "market", "quantity": str(amount / 10)}
    headers = sign_request("/api/spot/v1/trade/orders", "POST", str(order_data))
    response = requests.post(url, json=order_data, headers=headers)

    if response.status_code == 200:
        print("Achat réussi.")
        send_telegram_message(f"✅ Achat de {token} à {amount} USDT réussi.")
        daily_report.append(f"🟢 ACHAT - {token} | {amount} USDT")

        # Vente automatique avec take-profit et stop-loss
        take_profit_price = amount * (1 + take_profit / 100)
        stop_loss_price = amount * (1 - stop_loss / 100)

        while True:
            current_price = float(requests.get(f"{BASE_URL}/api/spot/v1/market/ticker?symbol={token}").json()['data'][0]['last'])
            if current_price >= take_profit_price or current_price <= stop_loss_price:
                sell_data = {"symbol": token, "side": "sell", "type": "market", "quantity": str(amount / 10)}
                sell_response = requests.post(url, json=sell_data, headers=headers)
                send_telegram_message(f"✅ Vente de {token} à {current_price} USDT.")
                daily_report.append(f"🔴 VENTE - {token} | {current_price:.4f} USDT")
                break
            time.sleep(1)
    else:
        print("Erreur d'achat.", response.text)

# Fonction principale
from datetime import datetime

daily_report = []

def main():
    try:
        res = requests.get(f"{BASE_URL}/api/spot/v1/market/tickers")
        if res.status_code == 200:
            paires = [item['symbol'] for item in res.json().get('data', []) if 'USDT' in item['symbol'] and any(mem in item['symbol'] for mem in ['PEPE', 'DOGE', 'WIF', 'FLOKI', 'SHIB', 'BONK'])]
            print(f"🔍 Analyse de {len(paires)} memecoins...")

            for token in paires:
                try:
                    trade_scalping(token, 100)
                except Exception as err:
                    print(f"Erreur pendant le trade de {token}: {err}")
                    continue
        else:
            print("❌ Impossible de récupérer la liste des paires disponibles.")
    except Exception as e:
        print(f"Erreur lors de l'analyse des paires : {e}")  # Exemple avec 100 USDT

if __name__ == "__main__":
    while True:
    try:
        now = datetime.now()
        if now.hour == 23 and now.minute < 5 and daily_report:
            report = "
".join(daily_report)
            send_telegram_message(f"📊 Rapport de la journée :
{report}")
            daily_report.clear()
            main()
        except Exception as e:
            print(f"Erreur détectée : {e}. Redémarrage dans 5 secondes...")
            send_telegram_message(f"⚠️ Erreur : {e}. Le bot redémarre dans 5s.")
            time.sleep(5)
