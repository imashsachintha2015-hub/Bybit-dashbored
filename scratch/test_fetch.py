import urllib.request
import json
import ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

url = "https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=5&limit=200"
try:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
        data = json.loads(resp.read().decode())
        print("Bybit API status:", data.get('retCode'), "Candles fetched:", len(data.get('result', {}).get('list', [])))
except Exception as e:
    print("Bybit fetch failed:", e)
    # Try OKX
    try:
        okx_url = "https://www.okx.com/api/v5/market/candles?instId=BTC-USDT-SWAP&bar=5m&limit=100"
        req = urllib.request.Request(okx_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            print("OKX API status:", data.get('code'), "Candles fetched:", len(data.get('data', [])))
    except Exception as e2:
        print("OKX fetch failed:", e2)
