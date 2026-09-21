import urllib.request
import json
import time

print("Checking Vercel deployment status at https://bybit-dashbored.vercel.app...")
for i in range(5):
    try:
        req = urllib.request.Request(
            'https://bybit-dashbored.vercel.app/api/agent/target-mode',
            headers={'User-Agent': 'MASIS/3.0'}
        )
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read().decode())
            mode = data.get('strategy_mode')
            status = data.get('status')
            print(f"Poll {i+1}: Strategy Mode = {mode} | Status = {status}")
            if mode == 'SWING_RUNNER':
                print("SUCCESS: Vercel has deployed the updated SWING_RUNNER code!")
                break
    except Exception as e:
        print(f"Poll {i+1} failed: {e}")
    time.sleep(5)

# Also check gatekeeper decisions endpoint
try:
    req = urllib.request.Request(
        'https://bybit-dashbored.vercel.app/api/agent/gatekeeper-decisions',
        headers={'User-Agent': 'MASIS/3.0'}
    )
    with urllib.request.urlopen(req, timeout=6) as r:
        dec_data = json.loads(r.read().decode())
        print(f"Vercel gatekeeper decisions endpoint: count = {dec_data.get('count')}")
except Exception as e:
    print(f"Gatekeeper decisions endpoint check: {e}")
