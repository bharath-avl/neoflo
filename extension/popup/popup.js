document.addEventListener('DOMContentLoaded', () => {
  const toggleBtn = document.getElementById('toggleBtn');
  const statusIndicator = document.getElementById('statusIndicator');
  const statusText = document.getElementById('statusText');
  const eventCountEl = document.getElementById('eventCount');
  const optionsLink = document.getElementById('optionsLink');

  // Load current state
  chrome.storage.local.get(['isMonitoring', 'sessionEventCount'], (result) => {
    const isMonitoring = result.isMonitoring || false;
    const count = result.sessionEventCount || 0;
    
    updateUI(isMonitoring, count);
  });

  // Listen for changes from background script
  chrome.storage.onChanged.addListener((changes, namespace) => {
    if (namespace === 'local') {
      if (changes.isMonitoring) {
        updateUI(changes.isMonitoring.newValue, parseInt(eventCountEl.textContent, 10));
      }
      if (changes.sessionEventCount) {
        eventCountEl.textContent = changes.sessionEventCount.newValue;
      }
    }
  });

  toggleBtn.addEventListener('click', () => {
    chrome.storage.local.get(['isMonitoring'], (result) => {
      const newState = !result.isMonitoring;
      
      // Update state in storage, background script will react to this
      chrome.storage.local.set({ isMonitoring: newState });
    });
  });

  optionsLink.addEventListener('click', (e) => {
    e.preventDefault();
    if (chrome.runtime.openOptionsPage) {
      chrome.runtime.openOptionsPage();
    } else {
      window.open(chrome.runtime.getURL('options/options.html'));
    }
  });

  function updateUI(isMonitoring, eventCount) {
    if (isMonitoring) {
      statusIndicator.classList.add('active');
      statusText.textContent = 'Monitoring Active';
      toggleBtn.textContent = 'Stop Monitoring';
      toggleBtn.classList.remove('primary');
      toggleBtn.classList.add('danger');
    } else {
      statusIndicator.classList.remove('active');
      statusText.textContent = 'Monitoring Paused';
      toggleBtn.textContent = 'Start Monitoring';
      toggleBtn.classList.remove('danger');
      toggleBtn.classList.add('primary');
    }
    eventCountEl.textContent = eventCount || 0;
  }
});
