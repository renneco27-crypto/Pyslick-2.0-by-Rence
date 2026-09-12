document.addEventListener('DOMContentLoaded', function () {
  const messageInput  = document.getElementById('messageInput');
  const injectBtn     = document.getElementById('injectBtn');
  const clearBtn      = document.getElementById('clearBtn');
  const readBtn       = document.getElementById('readBtn');
  const readerPanel   = document.getElementById('readerPanel');
  const readerOutput  = document.getElementById('readerOutput');
  const copyReaderBtn = document.getElementById('copyReaderBtn');
  const closeReaderBtn= document.getElementById('closeReaderBtn');
  const status        = document.getElementById('status');
  const debugToggle   = document.getElementById('debugToggle');
  const debugPanel    = document.getElementById('debugPanel');

  // ── Inject ────────────────────────────────────────────────────────────────
  injectBtn.addEventListener('click', function () {
    const message = messageInput.value.trim();
    if (!message) { showStatus('Please type a message first!', 'error'); return; }

    withActiveTab(tabId => {
      chrome.tabs.sendMessage(tabId, { action: 'injectText', text: message }, function (response) {
        if (chrome.runtime.lastError) {
          showStatus('✗ Content script not loaded on this page', 'error');
          return;
        }
        if (response && response.success) {
          showStatus('✓ Message injected! Ready to send.', 'success');
          messageInput.value = '';
          messageInput.focus();
        } else {
          showStatus("✗ Could not find Claude chat input", 'error');
        }
      });
    });
  });

  // ── Clear ─────────────────────────────────────────────────────────────────
  clearBtn.addEventListener('click', function () {
    messageInput.value = '';
    messageInput.focus();
  });

  // ── Read Page ─────────────────────────────────────────────────────────────
  readBtn.addEventListener('click', function () {
    readBtn.textContent = '⏳ Reading…';
    readBtn.disabled = true;

    withActiveTab(tabId => {
      chrome.tabs.sendMessage(tabId, { action: 'readPage' }, function (response) {
        readBtn.textContent = '📖 Read Page → show conversation';
        readBtn.disabled = false;

        if (chrome.runtime.lastError) {
          showStatus('✗ Content script not loaded on this page', 'error');
          return;
        }

        const content = (response && response.content) ? response.content : '(nothing returned)';
        readerOutput.value = content;
        readerPanel.classList.add('open');
        readerOutput.scrollTop = 0;
      });
    });
  });

  copyReaderBtn.addEventListener('click', function () {
    navigator.clipboard.writeText(readerOutput.value).then(() => {
      copyReaderBtn.textContent = '✓ Copied!';
      setTimeout(() => { copyReaderBtn.textContent = '📋 Copy all'; }, 1800);
    });
  });

  closeReaderBtn.addEventListener('click', function () {
    readerPanel.classList.remove('open');
    readerOutput.value = '';
  });

  // ── Ctrl/Cmd+Enter to inject ───────────────────────────────────────────────
  messageInput.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') injectBtn.click();
  });

  // ── Debug logs ────────────────────────────────────────────────────────────
  debugToggle.addEventListener('click', function () {
    const open = debugPanel.style.display !== 'none';
    debugPanel.style.display = open ? 'none' : 'block';
    debugToggle.textContent = open ? '📋 View Debug Logs' : '📋 Hide Debug Logs';
    if (!open) updateDebugLogs();
  });

  function updateDebugLogs() {
    withActiveTab(tabId => {
      chrome.scripting.executeScript(
        { target: { tabId }, func: () => window.__miamicoDlogs ? window.__miamicoDlogs.join('\n') : 'No logs yet.' },
        results => {
          if (results && results[0]) debugPanel.textContent = results[0].result;
        }
      );
    });
  }

  // ── Helpers ───────────────────────────────────────────────────────────────
  function withActiveTab(cb) {
    chrome.tabs.query({ active: true, currentWindow: true }, tabs => {
      if (tabs[0]) cb(tabs[0].id);
    });
  }

  function showStatus(message, type) {
    status.textContent = message;
    status.className = `status show ${type}`;
    setTimeout(() => status.classList.remove('show'), 3500);
  }

  messageInput.focus();
});

// ── Auto-relay: poll transcript and POST to pyslick when it changes ─────────
// Runs as soon as popup opens. Polls every 2s. When Claude finishes responding
// and the transcript changed since last send, POSTs to /claude-response.
// Resets lastSent whenever relay.json gets a new turn so we never skip a reply.
(function startAutoRelay() {
  let _lastSentTranscript = '';
  let _lastRelayBlocks    = '';

  function withActiveTab(cb) {
    chrome.tabs.query({ active: true, currentWindow: true }, tabs => {
      if (tabs[0]) cb(tabs[0].id);
    });
  }

  async function tick() {
    // 1. Check if ask.py wrote a new relay turn — if so reset so we re-send
    try {
      const r = await fetch('http://127.0.0.1:27182/relay', { cache: 'no-store' });
      if (r.ok) {
        const data = await r.json();
        const key  = JSON.stringify(data.blocks || []);
        if (key !== _lastRelayBlocks) {
          _lastRelayBlocks    = key;
          _lastSentTranscript = ''; // new question — allow fresh send
        }
      }
    } catch (_) {}

    // 2. Read last Claude message from the page
    withActiveTab(tabId => {
      chrome.scripting.executeScript(
        {
          target: { tabId },
          func: () => {
            // Check if Claude is still streaming
            const btn = document.querySelector('[data-testid="chat-input-send"]');
            if (btn && btn.disabled) return { streaming: true, text: '' };

            const turns = document.querySelectorAll(
              '[data-testid="transcript-list"] [data-testid^="conversation-turn"]'
            );
            if (!turns.length) return { streaming: false, text: '' };
            const last  = turns[turns.length - 1];
            const clone = last.cloneNode(true);
            clone.querySelectorAll('pre').forEach(p => p.remove());
            return { streaming: false, text: (clone.innerText || clone.textContent || '').trim() };
          }
        },
        results => {
          if (!results || !results[0]) return;
          const { streaming, text } = results[0].result;
          if (streaming || !text || text === _lastSentTranscript) return;

          _lastSentTranscript = text;

          fetch('http://127.0.0.1:27182/claude-response', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ blocks: [], plainText: text, count: 0 }),
          }).catch(() => {});
        }
      );
    });
  }

  setInterval(tick, 2000);
}());
