// Content script to track interactions
// IMPORTANT: Never capture input values, keystroke content, or sensitive data.
// Only track that an interaction occurred and on what element type.

let eventQueue = [];

// Listen for clicks
document.addEventListener('click', (e) => {
  logEvent('click', e.target);
}, true);

// Listen for scrolls (debounced)
let scrollTimeout;
document.addEventListener('scroll', () => {
  if (scrollTimeout) {
    clearTimeout(scrollTimeout);
  }
  scrollTimeout = setTimeout(() => {
    logEvent('scroll', document.scrollingElement);
  }, 500);
}, true);

// Listen for keydowns (never log the key itself!)
document.addEventListener('keydown', (e) => {
  // We only care THAT they are typing, not WHAT they are typing.
  logEvent('keydown', e.target);
}, true);

function logEvent(eventType, element) {
  // Only send basic metadata to respect privacy
  const metadata = {
    tagName: element ? element.tagName.toLowerCase() : 'unknown',
    // Do NOT include value, textContent, or specific key pressed.
  };

  const eventPayload = {
    type: eventType,
    url: window.location.href,
    metadata: metadata,
    ts: new Date().toISOString()
  };

  // Send to background script which decides if it should be dropped (paused or blocklisted)
  chrome.runtime.sendMessage({
    type: 'CONTENT_EVENT',
    payload: eventPayload
  });
}
