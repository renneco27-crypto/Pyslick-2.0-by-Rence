"""text_io -- safe UTF-8 reads, writes, and prints.

Never produces BOM. Never produces mojibake. Every module that
reads or prints user-facing text should import from here instead
of calling open() directly.
"""

from pathlib import Path

BOM = chr(0xFEFF)
REPLACEMENT = chr(0xFFFD)


def safe_read(path):
    """BOM-tolerant UTF-8 strict reader.

    utf-8-sig strips a leading BOM if present. errors=strict throws
    on genuinely corrupt bytes rather than silently producing mojibake.

    Returns (text, error_str_or_None).
    """
    try:
        text = Path(path).read_text(encoding="utf-8-sig", errors="strict")
        return text, None
    except UnicodeDecodeError as e:
        return "", "non-UTF-8 bytes at offset %d: %s" % (e.start, e.reason)
    except Exception as e:
        return "", str(e)


def safe_read_lines(path):
    """Same as safe_read but returns a list of lines."""
    text, err = safe_read(path)
    if err:
        return [], err
    return text.splitlines(), None


def verify_printable(line):
    """True if the line has no mojibake or stray BOM."""
    return REPLACEMENT not in line and BOM not in line


def verify_file(path):
    """Read a file and return (ok, message)."""
    text, err = safe_read(path)
    if err:
        return False, err
    if BOM in text:
        return False, "BOM found mid-file"
    if REPLACEMENT in text:
        return False, "replacement character U+FFFD present"
    return True, "ok"


def safe_print(*args, **kwargs):
    """print() that refuses to emit lines containing mojibake."""
    text = " ".join(str(a) for a in args)
    if not verify_printable(text):
        return
    print(*args, **kwargs)


def safe_print_lines(lines):
    """Print each line, skipping any that contain mojibake."""
    for line in lines:
        if verify_printable(line):
            print(line)


DOCSTRING_MARKERS = (chr(34) * 3, chr(39) * 3)


def enclosing_comment(lines, hit_idx, max_lookback=5):
    """Walk upward from a hit to find the nearest comment block."""
    buf = []
    for i in range(hit_idx - 1, max(0, hit_idx - max_lookback) - 1, -1):
        s = lines[i].strip()
        if not s:
            continue
        if s.startswith(("#", "//", "/*", "*")) or s.startswith(DOCSTRING_MARKERS):
            buf.insert(0, lines[i])
        else:
            break
    return buf


def safe_write(path, text):
    """Write text as UTF-8 with no BOM."""
    Path(path).write_text(text, encoding="utf-8")
