import sys
import os
import re

def check_jsx_tags(file_path):
    if not os.path.exists(file_path):
        print(f"Error: File not found: {file_path}")
        return False

    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    content = re.sub(r'<svg[\s>].*?</svg>', '<svg />', content, flags=re.DOTALL)
    content = re.sub(r'<script[\s>].*?</script>', '', content, flags=re.DOTALL)
    content = re.sub(r'<style[\s>].*?</style>', '', content, flags=re.DOTALL)

    def collapse_multiline_tags(text):
        result = []
        lines = text.split('\n')
        i = 0
        while i < len(lines):
            line = lines[i]
            open_count = len(re.findall(r'<[^/!]', line))
            close_count = len(re.findall(r'(?<!=)>', line))
            if open_count > close_count and not line.strip().startswith('//'):
                combined = line
                while i + 1 < len(lines) and '>' not in lines[i + 1].split('//')[0]:
                    i += 1
                    combined += ' ' + lines[i].strip()
                if i + 1 < len(lines):
                    i += 1
                    combined += ' ' + lines[i].strip()
                result.append(combined)
            else:
                result.append(line)
            i += 1
        return '\n'.join(result)

    content = collapse_multiline_tags(content)
    lines = content.splitlines()

    tag_stack = []
    errors = []

    VOID_TAGS = {
        'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
        'link', 'meta', 'param', 'source', 'track', 'wbr',
        'path', 'circle', 'rect', 'line', 'polygon', 'polyline',
        'ellipse', 'stop', 'use', 'defs', 'clipPath',
        'linearGradient', 'radialGradient', 'animate', 'animateTransform'
    }

    TS_GENERIC_TYPES = {
        'string', 'number', 'boolean', 'any', 'unknown', 'never', 'void',
        'object', 'symbol', 'bigint', 'undefined', 'null', 't', 'k', 'v', 'u', 'r'
    }

    in_multi_comment = False

    for line_num, line in enumerate(lines, 1):
        clean = line.strip()

        if in_multi_comment:
            if '*/' in clean:
                in_multi_comment = False
                clean = clean.split('*/', 1)[1]
            else:
                continue

        if '/*' in clean:
            if '*/' not in clean:
                in_multi_comment = True
                clean = clean.split('/*', 1)[0]
            else:
                clean = re.sub(r'/\*.*?\*/', '', clean)

        clean = re.sub(r'//.*$', '', clean)
        clean = re.sub(r'="[^"]*"', '="X"', clean)
        clean = re.sub(r"='[^']*'", "='X'", clean)

        tag_matches = re.finditer(r'(</?[a-zA-Z0-9_.:-]+(?:\s[^>]*)?>|</>)', clean)

        for match in tag_matches:
            tag_str = match.group(1).strip()

            if tag_str == '<>' or (tag_str.startswith('<React.Fragment') and not tag_str.endswith('/>')):
                tag_stack.append(('fragment', line_num, tag_str))
            elif tag_str == '</>' or tag_str.startswith('</React.Fragment'):
                if not tag_stack:
                    errors.append(f"[Line {line_num}] Stray closing fragment '{tag_str}'")
                else:
                    top = tag_stack.pop()
                    if top[0] != 'fragment':
                        errors.append(f"[Line {line_num}] Tag Mismatch: expected '</{top[0]}>' (opened at Line {top[1]}), but found '{tag_str}'")
            elif tag_str.endswith('/>'):
                continue
            elif tag_str.startswith('</'):
                m = re.match(r'</([a-zA-Z0-9_.:-]+)', tag_str)
                if m:
                    tag_name = m.group(1)
                    if tag_name.lower() in TS_GENERIC_TYPES:
                        continue
                    if not tag_stack:
                        errors.append(f"[Line {line_num}] Stray closing tag '</{tag_name}>' (no matching opening tag)")
                    else:
                        top = tag_stack.pop()
                        if top[0] != tag_name:
                            errors.append(
                                f"[Line {line_num}] Tag Mismatch:\n"
                                f"    Found closing '</{tag_name}>'\n"
                                f"    Expected closing '</{top[0]}>' (opened at Line {top[1]}: {top[2][:60]})\n"
                                f"    Check for an unclosed tag between Line {top[1]} and Line {line_num}."
                            )
            elif tag_str.startswith('<') and not tag_str.startswith('<!'):
                m = re.match(r'<([a-zA-Z0-9_.:-]+)', tag_str)
                if m:
                    tag_name = m.group(1)
                    if tag_name.lower() in VOID_TAGS or tag_name.lower() in TS_GENERIC_TYPES:
                        continue
                    tag_stack.append((tag_name, line_num, tag_str))

    if tag_stack:
        for tag in tag_stack:
            errors.append(
                f"[Line {tag[1]}] Unclosed tag '<{tag[0]}>':\n"
                f"    Snippet: \"{tag[2][:80]}\""
            )

    if not errors:
        print(f"OK: No JSX/HTML tag issues detected!")
        return True
    else:
        print(f"[ERROR] Found {len(errors)} JSX / HTML tag issue(s) in: {file_path}\n" + "=" * 70)
        for err in errors:
            print(f"- {err}\n")
        return False

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python jsx_tag_checker.py <path_to_file>")
        sys.exit(1)

    file_path = sys.argv[1]
    success = check_jsx_tags(file_path)
    sys.exit(0 if success else 1)
