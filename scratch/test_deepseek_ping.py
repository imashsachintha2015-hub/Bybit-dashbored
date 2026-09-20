import os
import urllib.request
import json

env = {}
with open('.env') as f:
    for line in f:
        line = line.strip()
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip().strip("'\"")

key = env.get('DEEPSEEK_API_KEY')
url = env.get('DEEPSEEK_URL', 'https://api.deepseek.com/v1/chat/completions')
body = json.dumps({
    'model': 'deepseek-chat',
    'messages': [{'role': 'user', 'content': 'You are a crypto trading expert. Reply in JSON: {"status": "ACTIVE", "model": "deepseek-chat"}'}],
    'response_format': {'type': 'json_object'}
}).encode('utf-8')

req = urllib.request.Request(url, data=body, headers={
    'Authorization': f'Bearer {key}',
    'Content-Type': 'application/json',
    'User-Agent': 'MASIS/3.0'
})

try:
    with urllib.request.urlopen(req, timeout=10) as r:
        print("DEEPSEEK SUCCESS:")
        print(r.read().decode())
except Exception as e:
    print("DEEPSEEK ERROR:", e)
