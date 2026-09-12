// ===== DEBUG MODE =====
const DEBUG = true;
window.__miamicoDlogs = [];

function addLog(message) {
  const timestamp = new Date().toLocaleTimeString();
  window.__miamicoDlogs.push(`[${timestamp}] ${message}`);
  if (window.__miamicoDlogs.length > 50) window.__miamicoDlogs.shift();
}
function debugLog(...args) {
  if (!DEBUG) return;
  console.log('%c[MIAMICO DEBUG]', 'background:#FF6B6B;color:white;padding:2px 6px;border-radius:3px;', ...args);
  addLog(`DEBUG: ${args.join(' ')}`);
}
function debugError(...args) {
  if (!DEBUG) return;
  console.error('%c[MIAMICO ERROR]', 'background:#FF6B6B;color:white;padding:2px 6px;border-radius:3px;', ...args);
  addLog(`ERROR: ${args.join(' ')}`);
}
function debugSuccess(...args) {
  if (!DEBUG) return;
  console.log('%c[MIAMICO SUCCESS]', 'background:#4ECDC4;color:white;padding:2px 6px;border-radius:3px;', ...args);
  addLog(`SUCCESS: ${args.join(' ')}`);
}

// ─── Constants ────────────────────────────────────────────────────────────────
const RELAY_URL     = 'http://127.0.0.1:27182/relay';
const RESPONSE_URL  = 'http://127.0.0.1:27182/claude-response';
const TAB_READY_URL = 'http://127.0.0.1:27182/tab-ready';

// ─── skill.md loader ──────────────────────────────────────────────────────────
// Fetched once, prepended to the FIRST turn of every ask session only.
let _skillMd       = null;   // null = not loaded yet
let _skillMdLoaded = false;

async function _loadSkillMd() {
  if (_skillMdLoaded) return _skillMd;
  try {
    const url = chrome.runtime.getURL('skill.md');
    const res = await fetch(url);
    _skillMd = res.ok ? await res.text() : null;
    if (_skillMd) debugSuccess('skill.md loaded (' + _skillMd.length + ' chars)');
    else debugLog('skill.md not found — continuing without it');
  } catch (e) {
    debugError('skill.md fetch error:', e.message);
    _skillMd = null;
  }
  _skillMdLoaded = true;
  return _skillMd;
}

// ─── Message router ───────────────────────────────────────────────────────────
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'injectText') {
    debugLog('Inject request:', request.text);
    injectTextIntoInput(request.text).then(success => sendResponse({ success }));
    return true;
  }
  if (request.action === 'readPage') {
    debugLog('Read-page request');
    sendResponse({ content: readPageContent() });
    return true;
  }
  if (request.action === 'startRelay') {
    startRelayPolling();
    sendResponse({ started: true });
    return true;
  }
  if (request.action === 'stopRelay') {
    stopRelayPolling();
    sendResponse({ stopped: true });
    return true;
  }
});

// ─── RELAY POLLING (pyslick → Claude) ────────────────────────────────────────
// Polls relay.json every 1.5s. When pyslick ask writes a new payload:
//   • First turn  → prepends skill.md before the question
//   • Later turns → sends the pyslick command result as-is
// After Claude responds, harvests BOTH code blocks and plain text,
// POSTs everything back to /claude-response.

let _relayTimer         = null;
let _relayLastSeen      = '';    // last relay payload we acted on (JSON string)
let _waitingForResponse = false;
let _isFirstTurn        = true;  // reset each time relay payload changes

function startRelayPolling() {
  if (_relayTimer) return;
  debugLog('Relay polling started');
  _relayTimer = setInterval(_pollRelay, 1500);
}

function stopRelayPolling() {
  if (!_relayTimer) return;
  clearInterval(_relayTimer);
  _relayTimer         = null;
  _relayLastSeen      = '';
  _waitingForResponse = false;
  _isFirstTurn        = true;
  debugLog('Relay polling stopped');
}

async function _pollRelay() {
  if (_waitingForResponse) return;
  try {
    const res = await _bgFetch(RELAY_URL, { method: 'GET' });
    if (!res.ok) return;
    const text = res.text;
    if (text === _relayLastSeen) return;

    const data   = JSON.parse(text);
    const blocks = data.blocks || [];
    if (!blocks.length) return;

    // New payload — check if this is a brand-new session (question turn)
    const isNewSession = !_relayLastSeen ||
      (blocks[0] && blocks[0].startsWith('QUESTION:'));

    if (isNewSession) _isFirstTurn = true;

    _relayLastSeen      = text;
    _waitingForResponse = true;

    debugSuccess(`Relay: ${blocks.length} block(s) — turn ${_isFirstTurn ? '1 (prepend skill.md)' : 'N'}`);

    // Build the message to inject
    let combined = blocks.join('\n\n');

    // First turn only: prepend skill.md so Claude knows the rules
    if (_isFirstTurn) {
      const skillMd = await _loadSkillMd();
      if (skillMd) {
        combined = skillMd + '\n\n---\n\n' + combined;
        debugSuccess('skill.md prepended to first turn');
      }
      _isFirstTurn = false;
    }

    const injected = await injectTextIntoInput(combined);
    if (!injected) { _waitingForResponse = false; return; }

    await _sleep(300);
    const sent = _sendMessage();
    if (!sent) { _waitingForResponse = false; return; }

    await _waitForClaudeAndRespond();
    _waitingForResponse = false;

  } catch (_) {
    _waitingForResponse = false;
  }
}

function _sendMessage() {
  const sendBtn = document.querySelector('[data-testid="chat-input-send"]');
  if (sendBtn && !sendBtn.disabled) {
    sendBtn.click();
    debugSuccess('✓ Send button clicked');
    return true;
  }
  const editor = document.querySelector('[data-testid="chat-input"]');
  if (editor) {
    editor.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'Enter', code: 'Enter', keyCode: 13,
      bubbles: true, cancelable: true
    }));
    debugLog('✓ Enter keypress dispatched');
    return true;
  }
  debugError('✗ Could not find send button or editor');
  return false;
}

async function _waitForClaudeAndRespond() {
  const MAX_WAIT = 120_000;
  const POLL     = 800;

  // Wait until send button is disabled (streaming started)
  await _pollUntil(() => {
    const btn = document.querySelector('[data-testid="chat-input-send"]');
    return btn && btn.disabled;
  }, 10_000, 200);

  // Wait until send button is enabled again (streaming done)
  await _pollUntil(() => {
    const btn = document.querySelector('[data-testid="chat-input-send"]');
    return btn && !btn.disabled;
  }, MAX_WAIT, POLL);

  // Extra settle so Claude finishes re-rendering
  await _sleep(800);

  debugSuccess('Claude finished responding — harvesting response');

  const codeBlocks = harvestNewCodeBlocks();
  const plainText  = harvestPlainText();

  // Always POST back even if no code blocks — plain text is what ask.py needs
  if (!codeBlocks.length && !plainText) {
    debugLog('Nothing to harvest — skipping POST');
    return;
  }

  try {
    await _bgFetch(RESPONSE_URL, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({
        blocks:    codeBlocks,
        plainText: plainText,
        count:     codeBlocks.length,
      }),
    });
    debugSuccess(`✓ POSTed ${codeBlocks.length} code block(s) + plain text to relay`);
  } catch (err) {
    debugError('POST to relay server failed:', err.message);
  }
}

// ─── Code block harvesting ────────────────────────────────────────────────────
const _sentBlocks = new Set();

function harvestNewCodeBlocks() {
  const newBlocks = [];
  const codeEls = document.querySelectorAll(
    '[data-testid="transcript-list"] pre code'
  );
  codeEls.forEach(el => {
    const text = el.innerText.trim();
    if (!text || _sentBlocks.has(text)) return;
    _sentBlocks.add(text);
    const langClass = [...el.classList].find(c => c.startsWith('language-'));
    const lang = langClass ? langClass.replace('language-', '') : 'code';
    newBlocks.push({ lang, code: text });
  });
  return newBlocks;
}

// ─── Plain text harvesting (NEW) ──────────────────────────────────────────────
// Captures Claude's latest plain-text reply so ask.py can detect
// "I have enough information." and extract bare pyslick commands.
let _lastPlainTextSeen = '';

function harvestPlainText() {
  try {
    // Get all assistant message containers
    const turns = document.querySelectorAll(
      '[data-testid="transcript-list"] [data-testid^="conversation-turn"]'
    );
    if (!turns.length) return '';

    // Take the last one (most recent Claude reply)
    const last = turns[turns.length - 1];

    // Exclude code blocks — we want prose only
    const clone = last.cloneNode(true);
    clone.querySelectorAll('pre').forEach(p => p.remove());

    const text = (clone.innerText || clone.textContent || '').trim();
    if (!text || text === _lastPlainTextSeen) return '';

    _lastPlainTextSeen = text;
    return text;
  } catch (e) {
    debugError('harvestPlainText error:', e.message);
    return '';
  }
}

// ─── INJECT ───────────────────────────────────────────────────────────────────
async function injectTextIntoInput(text) {
  const editor = document.querySelector('[data-testid="chat-input"]')
               || document.querySelector('[role="textbox"][contenteditable="true"]');

  if (editor && editor.contentEditable === 'true') {
    debugSuccess('✓ Found Claude editor');
    return injectIntoClaude(editor, text);
  }

  const selectors = [
    'input[type="text"][placeholder*="message" i]',
    'input[type="text"][placeholder*="chat" i]',
    'textarea[placeholder*="message" i]',
    '[contenteditable="true"]',
    'input[type="text"]',
    'textarea'
  ];
  for (const sel of selectors) {
    const el = document.querySelector(sel);
    if (el) { debugSuccess(`✓ Fallback: ${sel}`); return injectIntoGeneric(el, text); }
  }
  debugError('✗ All injection methods failed');
  return false;
}

function injectIntoClaude(editor, text) {
  try {
    editor.focus();
    const para = editor.querySelector('p') || editor;
    const sel  = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(para);
    range.collapse(false);
    sel.removeAllRanges();
    sel.addRange(range);
    document.execCommand('selectAll', false, null);
    const ok = document.execCommand('insertText', false, text);
    if (ok) { debugSuccess('✓ Injected via execCommand'); return true; }
    return injectViaClipboard(editor, text);
  } catch (err) {
    debugError('Inject error:', err);
    return false;
  }
}

async function injectViaClipboard(editor, text) {
  try {
    await navigator.clipboard.writeText(text);
    editor.focus();
    document.execCommand('selectAll', false, null);
    const ok = document.execCommand('paste', false, null);
    if (ok) { debugSuccess('✓ Injected via clipboard'); return true; }
    debugError('✗ Clipboard paste failed');
    return false;
  } catch (err) { debugError('Clipboard error:', err); return false; }
}

function injectIntoGeneric(element, text) {
  try {
    if (element.tagName === 'INPUT' || element.tagName === 'TEXTAREA') {
      element.focus(); element.value = text;
      element.dispatchEvent(new Event('input', { bubbles: true }));
      element.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    }
    if (element.contentEditable === 'true') {
      element.focus();
      document.execCommand('selectAll', false, null);
      document.execCommand('insertText', false, text);
      return true;
    }
    return false;
  } catch (err) { debugError('Generic inject error:', err); return false; }
}

// ─── READ PAGE (manual) ───────────────────────────────────────────────────────
function readPageContent() {
  try {
    const seen   = new Set();
    const blocks = [];
    const codeEls = document.querySelectorAll('[data-testid="transcript-list"] pre code');
    if (!codeEls.length) {
      const col = document.querySelector('[data-testid="chat-column"]');
      return col ? col.innerText.trim() : '(nothing found on page)';
    }
    codeEls.forEach(el => {
      const text = el.innerText.trim();
      if (!text || seen.has(text)) return;
      seen.add(text);
      const langClass = [...el.classList].find(c => c.startsWith('language-'));
      const lang = langClass ? langClass.replace('language-', '') : 'code';
      blocks.push(`[${lang}]\n${text}`);
    });
    return blocks.length ? blocks.join('\n\n---\n\n') : '(no unique code blocks found)';
  } catch (err) { return `Error: ${err.message}`; }
}

// ─── Helpers ──────────────────────────────────────────────────────────────────
function _sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function _pollUntil(condition, maxMs, intervalMs) {
  const deadline = Date.now() + maxMs;
  while (Date.now() < deadline) {
    if (condition()) return true;
    await _sleep(intervalMs);
  }
  return false;
}

debugLog('=====================================');
debugLog('Miamico Chat Injector loaded');
debugLog('DEBUG MODE: ENABLED');
debugLog('=====================================');

// ─── Auto-start on claude.ai ──────────────────────────────────────────────────
// POST /tab-ready so ask.py knows the tab is live, then begin polling
// immediately — no background.js message needed.
// ─── Background fetch proxy ───────────────────────────────────────────────────
// Routes ALL fetches to 127.0.0.1 through the background service worker so
// claude.ai's CSP cannot block them. Returns { ok, text } or throws.
function _bgFetch(url, options = {}) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(
      {
        action:  'relayFetch',
        url,
        method:  options.method  || 'GET',
        headers: options.headers || {},
        body:    options.body    || undefined,
      },
      response => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
        } else if (response && response.ok) {
          resolve(response);
        } else {
          reject(new Error(response ? response.error : 'no response'));
        }
      }
    );
  });
}

// ─── Tab-ready heartbeat ──────────────────────────────────────────────────────
async function _postTabReady() {
  try {
    await _bgFetch(TAB_READY_URL, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ url: location.href }),
    });
    debugSuccess('✓ /tab-ready heartbeat sent');
  } catch (err) {
    debugError('/tab-ready heartbeat failed:', err.message);
  }
}

// ─── Auto-start on claude.ai ──────────────────────────────────────────────────
(async () => {
  await _postTabReady();
  setInterval(_postTabReady, 15000);
  startRelayPolling();
  debugLog('Relay polling auto-started');
})();
