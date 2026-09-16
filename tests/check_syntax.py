import sys

def parse_js(filename):
    with open(filename, 'r', encoding='utf-8') as f:
        code = f.read()

    i = 0
    n = len(code)
    stack = []
    mode_stack = ['CODE']
    line = 1
    pairs = {')': '(', '}': '{', ']': '['}
    last_sig = ''

    while i < n:
        c = code[i]
        if c == '\n':
            line += 1
            i += 1
            continue

        if c.isspace():
            i += 1
            continue

        # Comments
        if mode_stack[-1] != 'TEMPLATE' and c == '/' and i + 1 < n:
            if code[i+1] == '/':
                i += 2
                while i < n and code[i] != '\n':
                    i += 1
                continue
            elif code[i+1] == '*':
                i += 2
                while i + 1 < n and not (code[i] == '*' and code[i+1] == '/'):
                    if code[i] == '\n':
                        line += 1
                    i += 1
                i += 2
                continue
            # Regex literal check
            elif last_sig in ('(', '=', ':', ',', '[', '!', '&', '|', '?', '{', ';', 'return'):
                # Regex literal: skip until unescaped /
                i += 1
                in_re_bracket = False
                while i < n:
                    if code[i] == '\\':
                        i += 2
                        continue
                    if code[i] == '[':
                        in_re_bracket = True
                    elif code[i] == ']':
                        in_re_bracket = False
                    elif code[i] == '/' and not in_re_bracket:
                        i += 1
                        break
                    if code[i] == '\n':
                        line += 1
                    i += 1
                # skip flags
                while i < n and code[i] in 'gimsuy':
                    i += 1
                last_sig = 'REGEX'
                continue

        # Strings in code mode
        if mode_stack[-1] != 'TEMPLATE' and (c == '"' or c == "'"):
            quote = c
            i += 1
            while i < n and code[i] != quote:
                if code[i] == '\\':
                    i += 2
                    continue
                if code[i] == '\n':
                    line += 1
                i += 1
            i += 1
            last_sig = 'STRING'
            continue

        # Template literal start or end
        if c == '`':
            if mode_stack[-1] == 'TEMPLATE':
                mode_stack.pop()
                last_sig = 'TEMPLATE'
            else:
                mode_stack.append('TEMPLATE')
            i += 1
            continue

        # Inside template literal
        if mode_stack[-1] == 'TEMPLATE':
            if c == '\\':
                i += 2
                continue
            if c == '$' and i + 1 < n and code[i+1] == '{':
                stack.append(('${', line))
                mode_stack.append('CODE')
                last_sig = '${'
                i += 2
                continue
            i += 1
            continue

        # Code mode brackets
        if c in '([{':
            stack.append((c, line))
            last_sig = c
        elif c in ')]}':
            if not stack:
                print(f"ERROR {filename}:{line}: unexpected '{c}'")
                return False
            top, top_line = stack.pop()
            if top == '${':
                if c != '}':
                    print(f"ERROR {filename}:{line}: expected '}}' to close '${{', got '{c}'")
                    return False
                if mode_stack and mode_stack[-1] == 'CODE':
                    mode_stack.pop()
            else:
                if pairs[c] != top:
                    print(f"ERROR {filename}:{line}: mismatched '{c}', opened with '{top}' at line {top_line}")
                    return False
            last_sig = c
        else:
            last_sig = c

        i += 1

    if stack:
        for s, l in stack:
            print(f"ERROR {filename}:{l}: unclosed '{s}'")
        return False
    if len(mode_stack) > 1:
        print(f"ERROR {filename}: unclosed template literal")
        return False

    print(f"PASS: {filename}")
    return True

files = [
    'agents/indicators.js',
    'agents/playbooks.js',
    'agents/position-manager.js',
    'agents/risk-governor.js',
    'masis-engine.js',
    'server/live-engine.js',
    'app.js'
]

ok = True
for f in files:
    if not parse_js(f):
        ok = False

sys.exit(0 if ok else 1)
