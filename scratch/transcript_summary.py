import json
with open(r'C:\Users\dilushika\.gemini\antigravity-ide\brain\4812da55-2b2b-4007-9533-ca4c556b2182\.system_generated\logs\transcript.jsonl', 'r', encoding='utf-8') as f:
    lines = f.readlines()
for l in lines[-12:]:
    d = json.loads(l)
    print(d.get('step_index'), d.get('source'), d.get('type'), str(d.get('content'))[:100], [c.get('name') for c in d.get('tool_calls', [])])
