chrome.runtime.onInstalled.addListener(() => {
  console.log('Chat Injector extension installed');
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status === 'complete') {
    console.log('Tab loaded:', tab.url);
  }
});

// ─── Relay proxy ──────────────────────────────────────────────────────────────
// content.js runs inside claude.ai's CSP which blocks fetch to 127.0.0.1.
// Background service workers have no such restriction.
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'relayFetch') {
    fetch(request.url, {
      method:  request.method  || 'GET',
      headers: request.headers || {},
      body:    request.body    || undefined,
    })
      .then(r => r.text())
      .then(text => sendResponse({ ok: true, text }))
      .catch(err => sendResponse({ ok: false, error: err.message }));
    return true; // keep channel open for async response
  }

  if (request.action === 'keepalive') {
    sendResponse({ alive: true });
    return true;
  }
});

// ─── Keep service worker alive ────────────────────────────────────────────────
// Chrome kills service workers after ~30s. This alarm fires every 25s to
// prevent that during an active pyslick ask session.
chrome.alarms.create('keepalive', { periodInMinutes: 0.4 }); // every ~25s
chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === 'keepalive') {
    // Just waking up is enough — no-op
  }
});
