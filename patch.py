import json
import re

def sanitize_mermaid(mermaid_text):
    lines = mermaid_text.split('\n')
    new_lines = []
    for line in lines:
        if '-->' in line or '-.->' in line:
            # find all nodes in the line
            # Node pattern: ID[Text] or ID{Text} or ID((Text))
            # Actually, let's just use regex to find [text], {text}
            
            # For brackets []
            line = re.sub(r'([A-Za-z0-9_]+)\[([^"\]]+)\]', r'\1["\2"]', line)
            # For braces {}
            line = re.sub(r'([A-Za-z0-9_]+)\{([^"\}]+)\}', r'\1{"\2"}', line)
            # For parens ()
            line = re.sub(r'([A-Za-z0-9_]+)\(([^"\)]+)\)', r'\1("\2")', line)
        
        new_lines.append(line)
    return '\n'.join(new_lines)

with open('submission.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

for covenant, item in data.get('answers', {}).get('P1', {}).items():
    if 'graph_mermaid' in item:
        item['graph_mermaid'] = sanitize_mermaid(item['graph_mermaid'])

with open('submission.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=4)

print("Sanitized completely!")
