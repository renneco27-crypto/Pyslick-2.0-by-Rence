import sys
import os
import re

LANGUAGE_NAMES = {
    "javascript", "typescript", "python", "powershell", "bash", "sh", "shell",
    "json", "html", "css", "jsx", "tsx", "js", "ts", "py", "text", "txt", "diff"
}

def clean_fenced_code(text: str) -> str:
    text = text.strip()
    if not text:
        return ""

    fence_match = re.search(r"```(?:[a-zA-Z0-9_\-]+)?\r?\n(.*?)\r?\n```", text, re.DOTALL)
    if fence_match:
        return fence_match.group(1).rstrip("\r\n")

    lines = text.splitlines()
    while lines and (lines[0].strip().lower() in LANGUAGE_NAMES or not lines[0].strip()):
        lines = lines[1:]

    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]

    return "\n".join(lines).rstrip()


def extract_find_replace_pairs(text: str) -> list[dict]:
    lines = text.splitlines()
    pairs: list[dict] = []

    find_re = re.compile(
        r"^\s*(?:\d+[.:]\s*)?(?:FIND|SEARCH|OLD|BEFORE)(?:\s+WITH|\s+BLOCK|\s*\(.*?\))?:?\s*$",
        re.IGNORECASE
    )
    replace_re = re.compile(
        r"^\s*(?:\d+[.:]\s*)?(?:REPLACE(?: WITH)?|NEW|AFTER)(?:\s+WITH|\s+BLOCK|\s*\(.*?\))?:?\s*$",
        re.IGNORECASE
    )
    section_break_re = re.compile(
        r"^\s*(?:Step\s+\d+:|###|##|Verify\s+|Run\s+this|Once\s+applied|pyslick\s+)",
        re.IGNORECASE
    )

    state = None
    cur_find_lines: list[str] = []
    cur_replace_lines: list[str] = []
    fence_count = 0

    for l in lines:
        if find_re.match(l):
            if state == "replace" and cur_find_lines and cur_replace_lines:
                f_clean = clean_fenced_code("\n".join(cur_find_lines))
                r_clean = clean_fenced_code("\n".join(cur_replace_lines))
                if f_clean and r_clean is not None:
                    pairs.append({"find": f_clean, "replace": r_clean})
                cur_find_lines = []
                cur_replace_lines = []
            state = "find"
            cur_find_lines = []
            fence_count = 0
        elif replace_re.match(l):
            if state == "find":
                state = "replace"
                cur_replace_lines = []
                fence_count = 0
        elif state == "replace" and fence_count >= 2 and section_break_re.match(l):
            # We already closed the code fence for replace, and hit a new section
            f_clean = clean_fenced_code("\n".join(cur_find_lines))
            r_clean = clean_fenced_code("\n".join(cur_replace_lines))
            if f_clean and r_clean is not None:
                pairs.append({"find": f_clean, "replace": r_clean})
            cur_find_lines = []
            cur_replace_lines = []
            state = None
        else:
            if l.strip().startswith("```"):
                fence_count += 1
            if state == "find":
                cur_find_lines.append(l)
            elif state == "replace":
                cur_replace_lines.append(l)

    if state == "replace" and cur_find_lines and cur_replace_lines:
        f_clean = clean_fenced_code("\n".join(cur_find_lines))
        r_clean = clean_fenced_code("\n".join(cur_replace_lines))
        if f_clean and r_clean is not None:
            pairs.append({"find": f_clean, "replace": r_clean})

    return pairs

snippet = """
Notice line 21 in your `pyslick grep` output—there's a syntax error caused by PowerShell string escaping:
`logDebug(Error parsing WebSocket payload: \\, 'error');`
Line 21 and line 34 lost their template literal backticks during the `Set-Content` command. Let's fix those two lines so the Chrome extension background service worker doesn't throw a parsing syntax error.
Step 1: Patch `background.js`
Run this command to fix lines 21 and 34:
PowerShell

```
pyslick patchit background.js -f

```

1. FIND:
JavaScript

```
      if (data.prompt || data.text) {
        await processPromptItem(data.prompt || data.text);
      }
    } catch (err) {
      logDebug(Error parsing WebSocket payload: \\, 'error');
    }
  };

```

2. REPLACE WITH:
JavaScript

```
      if (data.prompt || data.text) {
        await processPromptItem(data.prompt || data.text);
      }
    } catch (err) {
      logDebug(`Error parsing WebSocket payload: ${err.message}`, 'error');
    }
  };

```

3. FIND:
JavaScript

```
async function processPromptItem(promptItem) {
  const text = promptItem.text || promptItem.transcript || promptItem;
  logDebug(Processing incoming transcript: "\\...", 'job');

```

4. REPLACE WITH:
JavaScript

```
async function processPromptItem(promptItem) {
  const text = promptItem.text || promptItem.transcript || promptItem;
  logDebug(`Processing incoming transcript: "${text.slice(0, 30)}..."`, 'job');

```

Step 2: Verify Syntax Fix
Verify that the template strings render cleanly:
PowerShell

```
pyslick lines background.js 15 40
pyslick checkpoint

```
"""

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(THIS_DIR, "pyslick"))
import patchit

patches = extract_find_replace_pairs(snippet)
print("Total patches extracted:", len(patches))
for i, p in enumerate(patches, 1):
    print(f"Patch {i}:")
    print("  FIND:\n", p['find'])
    print("  REPLACE:\n", p['replace'])

# Mock target background.js
mock_js = """
// chrome service worker
function onMessage(data) {
  try {
    if (data.prompt || data.text) {
      await processPromptItem(data.prompt || data.text);
    }
  } catch (err) {
    logDebug(Error parsing WebSocket payload: \\, 'error');
  }
};

async function processPromptItem(promptItem) {
  const text = promptItem.text || promptItem.transcript || promptItem;
  logDebug(Processing incoming transcript: "\\...", 'job');
}
"""

modified = mock_js
for i, p in enumerate(patches, 1):
    loc = patchit._locate_block(p['find'], modified)
    assert loc is not None, f"Failed to locate block {i}:\n{p['find']}"
    span, kind = loc
    print(f"Block {i} located via: {kind}")
    reindented = patchit.reindent_to_match(span, p['replace'])
    modified = modified.replace(span, reindented, 1)

print("\n--- Final Patched File ---")
print(modified)
print("\nAll tests passed successfully!")
