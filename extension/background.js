// Background service worker

let isMonitoring = false;
let sessionEventCount = 0;
let sessionId = null;
let captureInterval = 30; // default 30s
let backendUrl = 'http://localhost:8000';
let blocklist = [];

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
  chrome.storage.local.set({ sessionId, sessionEventCount });
  updateAlarm();
  console.log('Session started:', sessionId);
}

// End current session
function endSession() {
  console.log('Session ended:', sessionId);
  sessionId = null;
  chrome.storage.local.set({ sessionId: null });
  updateAlarm();
}

// Ensure session ends on browser close (background script stops when browser closes usually,
// but MV3 service workers can wake up. We'll rely on the toggle for explicit sessions).
// Also if the service worker starts up and monitoring is on, it's a new session.
chrome.runtime.onStartup.addListener(() => {
  chrome.storage.local.get(['isMonitoring'], (result) => {
    if (result.isMonitoring) {
      startSession();
    }
  });
});

let captureTimer = null;

// Update screenshot alarm based on state
function updateAlarm() {
  chrome.alarms.clear('captureScreenshot');
  if (captureTimer) {
    clearTimeout(captureTimer);
    captureTimer = null;
  }

  if (isMonitoring) {
    // Keepalive alarm to wake up service worker if it goes to sleep
    chrome.alarms.create('captureScreenshot', {
      periodInMinutes: 1
    });
    
    scheduleNextCapture();
  }
}

function scheduleNextCapture() {
  if (captureTimer) {
    clearTimeout(captureTimer);
    captureTimer = null;
  }
  
  if (!isMonitoring) return;
  
  // Use setTimeout for accurate intervals (especially < 60s)
  captureTimer = setTimeout(async () => {
    await captureScreenshot();
    scheduleNextCapture();
  }, captureInterval * 1000);
}

// Handle alarms
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'captureScreenshot' && isMonitoring) {
    // Alarm acts as a keepalive. If the service worker was suspended,
    // the setTimeout would have paused. We wake up and restart the loop.
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
  if (!isMonitoring) return;

  try {
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    if (!tab || !tab.url) return;

    if (isUrlBlocklisted(tab.url)) {
      console.log('Skipping screenshot: URL is blocklisted', tab.url);
      return;
    }

    // Capture visible tab
    chrome.tabs.captureVisibleTab(tab.windowId, { format: 'png' }, (dataUrl) => {
      if (chrome.runtime.lastError) {
        console.error('Error capturing tab:', chrome.runtime.lastError);
        return;
      }
      if (dataUrl) {
        handleScreenshotCaptured(dataUrl, tab);
      }
    });
  } catch (err) {
    console.error('Failed to query tabs for screenshot:', err);
  }
}

function handleScreenshotCaptured(dataUrl, tab) {
  // In feat/backend-ingest this will POST to backend
  console.log('Captured screenshot for tab:', tab.url);
  // Log it as an event for now
  incrementEventCount();
}

// Listen for messages from content scripts
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === 'CONTENT_EVENT') {
    if (!isMonitoring) return;
    
    const url = message.payload.url;
    if (isUrlBlocklisted(url)) {
      return;
    }

    const event = {
      ...message.payload,
      tabId: sender.tab ? sender.tab.id : null
    };

    // In feat/backend-ingest this will batch and POST to backend
    console.log('Received event:', event);
    incrementEventCount();
  }
});

// Listen for tab navigation to log page changes
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (isMonitoring && changeInfo.status === 'complete' && tab.url) {
    if (!isUrlBlocklisted(tab.url)) {
      console.log('Tab updated:', tab.url);
      incrementEventCount();
      // We will record this as a navigation event in backend ingest
    }
  }
});

chrome.tabs.onActivated.addListener((activeInfo) => {
  if (isMonitoring) {
    chrome.tabs.get(activeInfo.tabId, (tab) => {
      if (tab && tab.url && !isUrlBlocklisted(tab.url)) {
        console.log('Tab activated:', tab.url);
        incrementEventCount();
        // We will record this as an activation event in backend ingest
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
