"""
PySlick Debugging Playbook — verbatim. Do not summarize, do not paraphrase.
This is the source of truth for how to debug. If the source changes, re-copy.
"""

PLAYBOOK_TEXT = r"""# PySlick Debugging Playbook

**Written for any AI or human joining a codebase with bugs.**

---

## 0. Before you start — two questions

1. **What bugs are we fixing?** Ask. Don't drift.
2. **Has this already been fixed?** Check git history across all branches before writing a line.

---

## 1. The one principle

**The data is the truth. The code is a claim.**

Every non-trivial bug lives in the *data* — the CSV, the IndexedDB record, the Supabase row, the CSV column alignment. Reading more source code never finds it. Dumping the data finds it in seconds.

When the symptom is "wrong text appears," "field is empty," "values are reversed," or "one thing dropped" — go to the data first. Not the code.

---

## 2. The three-layer model

| Layer | Example | Test |
|---|---|---|
| **Source** | Prompt, parser, renderer | Read the file, run pyslick |
| **Data** | CSV, IndexedDB, Supabase row, request body | Dump it and look |
| **Render** | JSX, CSS, formatting | Inspect element, screenshot |

**Never fix the wrong layer.** The definition reversal was not a render bug — the renderer faithfully showed what it was given. It was a *data* bug (AI wrote label into `front`). Patching the renderer would have looked like a fix and made the next bug harder.

**Workflow:**
1. Screenshot the symptom
2. Find the raw data behind it
3. Compare data to expectation
4. If data is wrong -> find its writer
5. If data is right -> find its reader
6. Fix the writer, not the reader

---

## 3. Probe order — ask the system, don't guess

| Probe | Tool | Answers |
|---|---|---|
| **Pre-work** | `pyslick pre-work "<task>" --file <file>` | Has someone already fixed this? |
| **Screenshot** | — | What does the user actually see? |
| **curl** | `curl.exe <url>` | What's actually deployed? |
| **adb / Chrome console** | `adb logcat`, `chrome://inspect` | What does the runtime say? |
| **Dump the data** | IndexedDB, Supabase, CSV, request body | Is the value wrong, or just rendered wrong? |
| **graphify** | `pyslick graphify callers <fn>` | Who writes this value? |
| **git history** | `git log --all --grep="<keyword>"` | When did this change? |
| **git show** | `git show <sha> -- <file>` | Did the commit do what its message claims? |
| **Fix the writer** | — | — |
| **Round-trip verify** | re-grep + `git diff` | Did the edit actually land? |

**Three tools, three jobs:**
- **graphify** — data flow, function-to-function relationships
- **pyslick** — semantic function grep, AST-level navigation
- **PowerShell** — exact lines, byte-level inspection

---

## 4. Pre-work — check git before you start

**Never start a fix without checking whether the fix already exists somewhere.**

    pyslick pre-work "<task description>" --file <path>
    pyslick git-search "<keyword>"
    pyslick git-branches <file>
    pyslick git-prs

**If any hit -> read the diff before writing your own fix.**

    git show <sha> -- <file>
    git log HEAD..origin/main -- <file>
    gh pr view <number>

**The rule:** check git history across **all branches** before starting.

---

## 5. PowerShell rules (never violate)

**Read:**  `[System.IO.File]::ReadAllText($p, UTF8Encoding($false,$true))`
**Write:** `[System.IO.File]::WriteAllText($p, $t, UTF8Encoding($false))`

Never `Set-Content`, `Out-File`, `>`, `>>`, `Add-Content`.

**Grep before edit.** Never `Replace(old, new)` without confirming `old` exists byte-for-byte.

**Verify after write.** Re-read the file and confirm the new text landed. Skipping this hides silently-failed edits.

**Backup first.** Undo is `Copy-Item $bak\* back`, not `git reset`.

**Save scripts to disk, don't paste.** Every `.ps1` called by another needs `exit 0` at the end.

**`else` cannot start a new statement.** Same line as the closing `}` of `if`.

**Detect CRLF vs LF before matching.** Java files: CRLF. TypeScript: LF. Never assume.

---

## 6. The debugging ladder (in order)

1. **Pre-work check.** `pyslick pre-work "<task>" --file <file>`. Has anyone already fixed this?
2. **Reproduce.** Screenshot. No fix without a live symptom.
3. **Dump the data.** IndexedDB, Supabase, CSV export, request body.
4. **Compare to expectation.** Wrong value, missing, or shifted?
5. **Trace the writer.** `pyslick graphify callers <fn>` or `Select-String`.
6. **Check history.** `git log --all --grep="<keyword>"` and `git show <sha> -- <file>`.
7. **Fix at the source.** Prompt, parser, or the thing that wrote the bad value.
8. **Round-trip verify.** Delete the bad data, regenerate, confirm.

Every session bug went through all eight. Every time we skipped a step, we lost an hour.

---

## 7. Tools — three per job

### pyslick

    pyslick graphify "getDeck"
    pyslick graphify callers getDeck
    pyslick graphify flow reloadDeck getDeck
    pyslick graphify orphans
    pyslick grep "serwist"
    pyslick lines src\db\deckRepository.ts 50-120
    pyslick playbook
    pyslick oneshot "<query>"

### standalone graphify

    graphify god-nodes --top 30
    graphify explain "<node>"
    graphify path "A" "B"
    graphify affected "X" --depth 5
    graphify update .

Point PySlick at the standalone graph:

    $env:GRAPHIFY_GRAPH = (Resolve-Path "graphify-out\graph.json").Path

### grep performance rules

1. Stream results as discovered. Don't wait for a full scan.
2. Scan filenames first, then grep contents.
3. Use comment blocks as contextual anchors — 2-3 lines above the code.
4. Exclude: node_modules, .git, dist, build, __pycache__, graphify-out, .pyslick, coverage.
5. Small files first — sort by size ascending.
6. All CPU cores — ProcessPoolExecutor(max_workers=os.cpu_count()).
7. Shell out to `rg` (ripgrep) when available.
8. C++ core later if profiling shows it matters.

---

## 8. Git as a probe

    git status
    git --no-pager diff
    git log --oneline -20
    git log --all --oneline -- <file>
    git log --all --oneline --grep='offline' --grep='serwist' -i
    git log --format='%h %s%n%b' -5
    git blame -L 140,160 -- <file>
    git show <sha> -- <file>
    git cherry-pick <sha>

Always `git show` the suspect commit before assuming it did what its message says.

Never `git add -A` when `.pyslick/` is dirty. Stage explicit files.

---

## 9. Data-inspection techniques

### IndexedDB dump

    const dbs = await indexedDB.databases()
    const req = indexedDB.open('StitchDB')
    req.onsuccess = () => {
      const db = req.result
      db.transaction(['decks'], 'readonly').objectStore('decks').getAll().onsuccess = (e) => {
        for (const d of e.target.result) {
          (d.quizItems || []).filter(q => q.mode === 'true_false').forEach(q =>
            console.log(JSON.stringify({ title: d.title, statement: q.statement, correct: q.correct }))
          )
        }
      }
    }

### Supabase schema

    SELECT column_name, data_type FROM information_schema.columns
    WHERE table_name = 'decks' ORDER BY ordinal_position;

Never trust zodSchemas.ts as source of truth. Query the DB.

### Download the data

    const blob = new Blob([JSON.stringify(window.__messages, null, 2)], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = 'dump.json'
    a.click()

---

## 10. Live site + device probing

    curl.exe "https://www.studyup.cloud-ip.cc/sw.js" | Select-String 'skipWaiting'

Iterate on localhost:3000, verify final on prod.

Chrome mobile: chrome://inspect/#devices after USB debugging on.

    adb logcat -s chromium:* | Select-String 'studyup|api|ERR_'
    adb shell dumpsys connectivity | Select-String 'VALIDATED|Transports'

NET_CAPABILITY_VALIDATED, not transport. Plain transport check returns true on captive-portal WiFi with no upstream.

---

## 11. Communicating with the user

- Ask, don't guess. When three hypotheses exist, ask before editing.
- Show evidence. Paste the grep output, the file line, the diff.
- Plain English when asked.
- One change at a time.
- Fix the cause, not the symptom.
- State uncertainty explicitly.
- Think in architecture, not patches.

---

## 12. Anti-patterns that cost hours

| Anti-pattern | Cost | Fix |
|---|---|---|
| Reading the whole file | Finds nothing | Select-String or pyslick grep |
| Guessing the file | Wrong fix | grep first |
| Pasting multi-line scripts | Half-applied edits | Save .ps1, run with & |
| Missing exit 0 at script end | False failure | Add exit 0 explicitly |
| else on its own line | Parse error | Same line as closing } |
| Set-Content / > / Out-File | BOM, encoding corruption | [System.IO.File]::WriteAllText |
| errors="replace" in reads | Silent mojibake | errors="strict" + utf-8-sig |
| Trusting code over data | Real bug invisible | Dump the data first |
| Skipping post-write verify | Silent no-op edits | re-read + Contains check |
| Assuming CRLF/LF | Anchors fail | Detect and normalize |
| Fixing the renderer | Bug returns | Fix the writer |
| Ignoring git history | Reinvents fixes | git log --all --grep first |
| "I fixed it" without a test | Trust lost | Screenshot or GTFO |

---

## 13. PySlick features referenced in this playbook

| Command | Purpose |
|---|---|
| pyslick playbook | Print this document |
| pyslick oneshot "<query>" | Fast streamed search, no mojibake, no BOM |
| pyslick pre-work "<task>" --file <path> | Git-aware pre-debug check |
| pyslick git-search "<keyword>" | Commit messages across all branches |
| pyslick git-branches <file> | Branches with unmerged changes to a file |
| pyslick git-prs | Open PRs (needs gh CLI) |
| pyslick orphans | Functions defined but never referenced |
| pyslick graphify callers <fn> | Who calls this function |
| pyslick graphify flow <A> <B> | Data flow from A to B |
| pyslick comment-scan | Comment blocks, no mojibake, no false markers |

---

## 14. When in doubt, ask these three questions

1. Where does this data come from? (Prompt? Parser? User input? Cache?)
2. What does the raw data actually contain? (Dump it. Don't guess.)
3. When did this change? (git log around the file; git show the suspect commits.)

If you cannot answer all three, you do not yet know where the bug is. Do not edit.

---

## 15. The only rule that never changed

**Fix at the source. Verify with a round trip. Never commit without a diff review.**

Everything else is detail.

---

The rule underneath all of it: every one of these is a way to ask the system a direct question. Ask the system. Don't ask yourself what the code probably does.
"""

HANDOFF_TEXT = r"""# Debugging Handoff — Tools & Techniques

What we actually did, how we used each tool, and the techniques worth keeping.
This is the "how" companion to the "rules." Read this when you already know
the principles and need to *reach* the bug.

## The stance (30 seconds)

Probe externally. Localize the layer. Then edit.

We never found a bug by reading more source. We found it by dumping data,
curling the site, inspecting the phone, checking git history, or querying
the DB directly. Every technique is a probe — a way to get ground truth
without guessing.

## 1. graphify — pinpoint before you read

    pyslick graphify "getDeck"
    pyslick graphify callers getDeck
    pyslick graphify flow reloadDeck getDeck

Turns "where does front get written?" from a 20-minute grep hunt into a
5-second answer. Use it to find the writer, not read the file.

## 2. PowerShell — the ground-truth workhorse

    Get-ChildItem src -Recurse -Include *.ts,*.tsx |
      Select-String -Pattern "Why this step works"

    [System.IO.File]::ReadAllBytes("public\manifest.json")[0..3]

Read/write without corrupting encoding — see the playbook's PowerShell rules.

The one rule that saves hours: grep -> replace -> re-grep -> git diff.

## 3. curl the live site

Prod is a probe. Answers "what is actually deployed?"

    curl.exe "https://www.studyup.cloud-ip.cc/sw.js" | Select-String 'skipWaiting'

Rule: iterate on localhost, verify on prod.

## 4. Chrome mobile inspection

    navigator.serviceWorker.getRegistrations().then(r => console.log(r.length, r))

IndexedDB dump — the technique that found the "Cell Theory True False" bug
after an hour of source reading.

## 5. adb — drive and observe the real app

    adb logcat -s chromium:* | Select-String 'studyup|api|ERR_'
    adb shell dumpsys connectivity | Select-String 'VALIDATED|Transports'

NET_CAPABILITY_VALIDATED, not transport.

## 6. git — history is a probe

    git log --all --grep='offline' --grep='serwist' -i
    git blame -L 140,160 -- src/components/MathFormattedText.tsx

The T/F text bug looked like a code change. git show proved the commit was
CSS-only — which pushed us to the data layer.

## 7. Supabase — query the DB, don't trust the code

    SELECT column_name, data_type FROM information_schema.columns
    WHERE table_name = 'decks' ORDER BY ordinal_position;

When schema and code disagree, the DB wins.

## 8. The composite technique — layer localization

1. Screenshot the symptom
2. curl the deployed asset
3. adb logcat or Chrome console
4. Dump the data — is the value wrong, or just rendered wrong?
5. graphify callers / flow — find the writer
6. git show / log — when did it change?
7. Fix the writer, re-grep, git diff, round-trip

Each probe eliminates a layer.

## 9. Quick reference

| Question | Tool |
|---|---|
| Where is this symbol used? | pyslick graphify callers <fn> |
| What did this commit really change? | git show <sha> -- <file> |
| What's deployed right now? | curl.exe <url> |
| What does the app log on-device? | adb logcat -s chromium:* |
| What does the DB actually store? | Supabase information_schema query |
| What's in client state? | IndexedDB dump in DevTools console |
| Did my edit land? | re-grep + git --no-pager diff |
| Did I already fix this? | git log --all --grep + commit bodies |

Always verify if changes landed via PowerShell.

---

The rule underneath all of it: every one of these is a way to ask the system
a direct question. Ask the system. Don't ask yourself what the code probably does.
"""


def print_playbook(which: str = "main") -> None:
    if which == "handoff":
        print(HANDOFF_TEXT)
    elif which == "all":
        print(PLAYBOOK_TEXT)
        print("\n" + "=" * 78 + "\n")
        print(HANDOFF_TEXT)
    else:
        print(PLAYBOOK_TEXT)

# ---------------------------------------------------------------------------
# Operating directives (appended by fix_batch1.py)
# ---------------------------------------------------------------------------
OPERATING_DIRECTIVES_APPENDED = True

OPERATING_DIRECTIVES = """
OPERATING DIRECTIVES:
  1. NEVER exit on code 0. No ceremonial success exit. Scripts fall
     through, or exit non-zero on failure only.
  2. STOP YAPPING. No preamble. No postscript. No narration. Code plus
     the minimum prose needed to use it. One question max if blocked.
  3. PLAIN ENGLISH. I know tech, I do not want jargon. Explain like I
     am smart but new to this. Short sentences. No acronym soup.
"""
