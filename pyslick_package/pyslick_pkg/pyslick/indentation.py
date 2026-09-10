import sys

def analyze_indentation_scopes(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    stack = []
    errors = []
    in_multi_comment = False
    in_string = False
    string_char = ''

    for line_idx, line in enumerate(lines):
        line_num = line_idx + 1
        stripped = line.strip()

        if not stripped:
            continue

        indent = len(line) - len(line.lstrip())
        i = 0

        while i < len(line):
            char = line[i]
            next_char = line[i+1] if i + 1 < len(line) else ''

            if in_multi_comment:
                if char == '*' and next_char == '/':
                    in_multi_comment = False
                    i += 2
                    continue
                i += 1
                continue

            if not in_string and char == '/' and next_char == '/':
                break

            if not in_string and char == '/' and next_char == '*':
                in_multi_comment = True
                i += 2
                continue

            if char in ('"', "'", '`') and (i == 0 or line[i-1] != '\\'):
                if not in_string:
                    in_string = True
                    string_char = char
                elif string_char == char:
                    in_string = False

            if not in_string and not in_multi_comment:
                if char == '{':
                    stack.append({
                        'line': line_num,
                        'col': i + 1,
                        'indent': indent,
                        'snippet': stripped[:50]
                    })
                elif char == '}':
                    if not stack:
                        errors.append(f"[Line {line_num}, Col {i+1}] Stray closing '}}' found (no matching '{{').")
                    else:
                        opened = stack.pop()
                        is_leading_brace = (i == indent)
                        if is_leading_brace and indent != opened['indent']:
                            errors.append(
                                f"[Line {line_num}] SCOPE DRIFT / INDENTATION MISMATCH:\n"
                                f"   Closing '}}' (indent {indent}) closed '{{' from Line {opened['line']} (indent {opened['indent']}).\n"
                                f"   Opened Block: \"{opened['snippet']}...\"\n"
                                f"   Check for a missing or extra '}}' between Line {opened['line']} and Line {line_num}.\n"
                            )

            i += 1

    if stack:
        for opened in stack:
            errors.append(
                f"[Line {opened['line']}] Unclosed '{{' (indent {opened['indent']}).\n"
                f"   Opened Block: \"{opened['snippet']}...\""
            )

    return errors

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python indent_scope_checker.py <path_to_file>")
        sys.exit(1)

    target_file = sys.argv[1]
    results = analyze_indentation_scopes(target_file)

    if not results:
        print("OK: No scope or brace mismatches detected!")
    else:
        for err in results:
            print(err)
