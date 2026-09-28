from pathlib import Path
import shutil, subprocess, sys
from datetime import datetime

ROOT = Path.cwd()
BK = ROOT / ".pyslick" / "backups" / ("idxcache-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
BK.mkdir(parents=True, exist_ok=True)

p = ROOT / "text_index.py"
raw = p.read_bytes()
if raw.startswith(b"\xef\xbb\xbf"):
    raw = raw[3:]
text = raw.decode("utf-8", errors="strict")
eol = "\r\n" if "\r\n" in text else "\n"

if "_load_cached_index" in text:
    print("already patched")
    sys.exit(0)

# Find the definition of build_text_index.
anchor = "def build_text_index(root: str = \".\") -> dict:"
if anchor not in text:
    print("build_text_index not found")
    sys.exit(1)

# Insert cache helpers before build_text_index.
helpers = '''import pickle
import hashlib as _hashlib
import time as _time_mod


def _index_cache_dir(root: str = ".") -> Path:
    """Where the index cache lives for this repo."""
    d = Path(root) / ".pyslick" / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file_signature(root: str = ".") -> tuple:
    """Return a hash of (path, mtime_ns, size) for every file we would index.

    Cheap: uses os.scandir + os.stat, no content reads. If the signature
    matches a saved cache, the cache is valid.
    """
    sig_parts = []
    for path in sorted(collect_files(root)):
        try:
            st = os.stat(path)
            sig_parts.append("%s:%d:%d" % (path, st.st_mtime_ns, st.st_size))
        except OSError:
            continue
    return tuple(sig_parts)


def _sig_hash(sig: tuple) -> str:
    h = _hashlib.sha256()
    for part in sig:
        h.update(part.encode("utf-8", errors="replace"))
        h.update(b"\\n")
    return h.hexdigest()


def _load_cached_index(root: str = "."):
    """Return (index, sig_hash) if cache is valid, else (None, current_sig_hash)."""
    sig = _file_signature(root)
    sig_hash = _sig_hash(sig)
    cache_file = _index_cache_dir(root) / "text_index.pkl"
    if not cache_file.is_file():
        return None, sig_hash
    try:
        with open(cache_file, "rb") as f:
            payload = pickle.load(f)
    except Exception:
        return None, sig_hash
    if not isinstance(payload, dict):
        return None, sig_hash
    if payload.get("sig_hash") != sig_hash:
        return None, sig_hash
    return payload.get("index"), sig_hash


def _save_cached_index(root: str, index: dict, sig_hash: str) -> None:
    cache_file = _index_cache_dir(root) / "text_index.pkl"
    try:
        with open(cache_file, "wb") as f:
            pickle.dump({"sig_hash": sig_hash, "index": index}, f, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:
        pass


'''

text = text.replace(anchor, helpers + anchor, 1)

# Now modify build_text_index's body to check cache first.
old_body_head = '''def build_text_index(root: str = ".") -> dict:
    """Walk the repo and build an in-memory BM25 index.

    Shape:
      {
        "postings": {token: {filepath: tf}},
        "doc_len":  {filepath: int},
        "avg_len":  float,
        "n_docs":   int,
      }
    """
    files = collect_files(root)'''

new_body_head = '''def build_text_index(root: str = ".", use_cache: bool = True) -> dict:
    """Walk the repo and build an in-memory BM25 index.

    If use_cache=True (default), the index is cached to
    <root>/.pyslick/cache/text_index.pkl and reloaded when no source file
    has changed (mtime_ns + size signature). Cache miss rebuilds as before.

    Shape:
      {
        "postings": {token: {filepath: tf}},
        "doc_len":  {filepath: int},
        "avg_len":  float,
        "n_docs":   int,
      }
    """
    if use_cache:
        cached, sig_hash = _load_cached_index(root)
        if cached is not None:
            return cached
    else:
        sig_hash = None

    files = collect_files(root)'''

if old_body_head not in text:
    print("build_text_index body head not found")
    sys.exit(1)
text = text.replace(old_body_head, new_body_head, 1)

# Modify the return of build_text_index to save cache.
old_return = '''    return {
        "postings": {t: dict(f) for t, f in postings.items()},
        "doc_len": doc_len,
        "avg_len": avg_len,
        "n_docs": n_docs,
    }'''

new_return = '''    result = {
        "postings": {t: dict(f) for t, f in postings.items()},
        "doc_len": doc_len,
        "avg_len": avg_len,
        "n_docs": n_docs,
    }
    if use_cache:
        if sig_hash is None:
            sig_hash = _sig_hash(_file_signature(root))
        _save_cached_index(root, result, sig_hash)
    return result'''

if old_return not in text:
    print("build_text_index return block not found")
    sys.exit(1)
text = text.replace(old_return, new_return, 1)

shutil.copy2(p, BK / p.name)
p.write_bytes(text.encode("utf-8"))

r = subprocess.run([sys.executable, "-m", "py_compile", str(p)],
                   capture_output=True, text=True)
if r.returncode != 0:
    shutil.copy2(BK / p.name, p)
    print("py_compile failed, rolled back:")
    print(r.stderr or r.stdout)
    sys.exit(1)

print("APPLIED. Backups:", BK)
