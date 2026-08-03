// Background service worker

let isMonitoring = false;
let sessionEventCount = 0;
let sessionId = null;
let captureInterval = 30; // default 30s
let backendUrl = 'http://localhost:8000';
let blocklist = [];
let captureTimer = null;

let eventQueue = [];
let batchTimer = null;

// Initialize state on load
chrome.storage.local.get(
  ['isMonitoring', 'sessionEventCount', 'sessionId', 'captureInterval', 'backendUrl', 'blocklist'],
  (result) => {
    isMonitoring = result.isMonitoring || false;
    sessionEventCount = result.sessionEventCount || 0;
    sessionId = result.sessionId || null;
    if (result.captureInterval) captureInterval = result.captureInterval;
    if (result.backendUrl) backendUrl = result.backendUrl;
    if (result.blocklist) blocklist = result.blocklist;
    
    updateAlarm();
    if (isMonitoring) {
        startBatchLoop();
    }
  }
);

// Listen for storage changes
chrome.storage.onChanged.addListener((changes, namespace) => {
  if (namespace === 'local') {
    if (changes.isMonitoring) {
      isMonitoring = changes.isMonitoring.newValue;
      if (isMonitoring) {
        startSession();
      } else {
        endSession();
      }
    }
    if (changes.captureInterval) {
      captureInterval = changes.captureInterval.newValue;
      updateAlarm();
    }
    if (changes.backendUrl) {
      backendUrl = changes.backendUrl.newValue;
    }
    if (changes.blocklist) {
      blocklist = changes.blocklist.newValue;
    }
  }
});

// Start a new session
function startSession() {
  sessionId = generateId();
  sessionEventCount = 0;
  eventQueue = [];
  chrome.storage.local.set({ sessionId, sessionEventCount });
  updateAlarm();
  startBatchLoop();
  console.log('Session started:', sessionId);
}

// End current session
function endSession() {
  console.log('Session ended:', sessionId);
  flushEventQueue(); // Try to send any remaining
  sessionId = null;
  chrome.storage.local.set({ sessionId: null });
  updateAlarm();
  stopBatchLoop();
}

chrome.runtime.onStartup.addListener(() => {
  chrome.storage.local.get(['isMonitoring'], (result) => {
    if (result.isMonitoring) {
      startSession();
    }
  });
});

// Update screenshot alarm based on state
function updateAlarm() {
  chrome.alarms.clear('captureScreenshot');
  if (captureTimer) {
    clearTimeout(captureTimer);
    captureTimer = null;
  }

  if (isMonitoring) {
    chrome.alarms.create('captureScreenshot', { periodInMinutes: 1 });
    scheduleNextCapture();
  }
}

function scheduleNextCapture() {
  if (captureTimer) {
    clearTimeout(captureTimer);
    captureTimer = null;
  }
  
  if (!isMonitoring) return;
  
  captureTimer = setTimeout(async () => {
    await captureScreenshot();
    scheduleNextCapture();
  }, captureInterval * 1000);
}

// Batching loop
function startBatchLoop() {
  stopBatchLoop();
  batchTimer = setInterval(() => {
    flushEventQueue();
  }, 10000); // 10 seconds
}

function stopBatchLoop() {
  if (batchTimer) {
    clearInterval(batchTimer);
    batchTimer = null;
  }
}

// Handle alarms
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'captureScreenshot' && isMonitoring) {
    scheduleNextCapture();
  }
});

// Helper to check blocklist
function isUrlBlocklisted(url) {
  if (!url) return true;
  try {
    const urlObj = new URL(url);
    const domain = urlObj.hostname.toLowerCase();
    return blocklist.some(blocked => domain === blocked || domain.endsWith('.' + blocked));
  } catch (e) {
    return true; // fail safe
  }
}

async function captureScreenshot() {
  if (!isMonitoring || !sessionId) return;

  try {
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    if (!tab || !tab.url) return;

    if (isUrlBlocklisted(tab.url)) return;

    chrome.tabs.captureVisibleTab(tab.windowId, { format: 'png' }, (dataUrl) => {
      if (chrome.runtime.lastError) return;
      if (dataUrl) {
        handleScreenshotCaptured(dataUrl, tab);
      }
    });
  } catch (err) {
    console.error('Failed to capture screenshot:', err);
  }
}

function handleScreenshotCaptured(dataUrl, tab) {
  incrementEventCount();
  
  const payload = {
    session_id: sessionId,
    tab_url: tab.url,
    image_base64: dataUrl,
    timestamp: new Date().toISOString()
  };

  fetchWithRetry(`${backendUrl}/screenshots`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
}

function queueEvent(event) {
  if (!isMonitoring || !sessionId) return;
  event.session_id = sessionId;
  eventQueue.push(event);
  incrementEventCount();
}

function flushEventQueue() {
  if (eventQueue.length === 0 || !sessionId) return;

  const eventsToSend = [...eventQueue];
  eventQueue = []; // clear queue

  fetchWithRetry(`${backendUrl}/events`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ events: eventsToSend })
  }).catch((err) => {
    // If it fails completely after retries, put them back
    console.error("Failed to send events, putting back in queue", err);
    eventQueue = [...eventsToSend, ...eventQueue];
  });
}

async function fetchWithRetry(url, options, maxRetries = 5) {
  let delay = 1000;
  for (let i = 0; i < maxRetries; i++) {
    try {
      const response = await fetch(url, options);
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      return await response.json();
    } catch (error) {
      if (i === maxRetries - 1) throw error;
      await new Promise(res => setTimeout(res, delay));
      delay *= 2; // exponential backoff
    }
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === 'CONTENT_EVENT') {
    if (!isMonitoring) return;
    
    const url = message.payload.url;
    if (isUrlBlocklisted(url)) return;

    queueEvent({
      type: message.payload.type,
      url: message.payload.url,
      metadata: message.payload.metadata,
      timestamp: message.payload.ts
    });
  }
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (isMonitoring && changeInfo.status === 'complete' && tab.url) {
    if (!isUrlBlocklisted(tab.url)) {
      queueEvent({
        type: 'navigation',
        url: tab.url,
        metadata: { title: tab.title },
        timestamp: new Date().toISOString()
      });
    }
  }
});

chrome.tabs.onActivated.addListener((activeInfo) => {
  if (isMonitoring) {
    chrome.tabs.get(activeInfo.tabId, (tab) => {
      if (tab && tab.url && !isUrlBlocklisted(tab.url)) {
        queueEvent({
          type: 'activation',
          url: tab.url,
          metadata: { title: tab.title },
          timestamp: new Date().toISOString()
        });
      }
    });
  }
});

function incrementEventCount() {
  sessionEventCount++;
  chrome.storage.local.set({ sessionEventCount });
}

function generateId() {
  return Math.random().toString(36).substring(2, 15) + Math.random().toString(36).substring(2, 15);
}
