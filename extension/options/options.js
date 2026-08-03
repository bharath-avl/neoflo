document.addEventListener('DOMContentLoaded', () => {
  const backendUrlInput = document.getElementById('backendUrl');
  const captureIntervalInput = document.getElementById('captureInterval');
  const intervalValueDisplay = document.getElementById('intervalValue');
  const blocklistInput = document.getElementById('blocklist');
  const saveBtn = document.getElementById('saveBtn');
  const saveStatus = document.getElementById('saveStatus');

  // Load existing settings
  chrome.storage.local.get(
    {
      backendUrl: 'http://localhost:8000',
      captureInterval: 30,
      blocklist: []
    },
    (items) => {
      backendUrlInput.value = items.backendUrl;
      captureIntervalInput.value = items.captureInterval;
      intervalValueDisplay.textContent = items.captureInterval;
      blocklistInput.value = items.blocklist.join('\n');
    }
  );

  // Update interval display dynamically
  captureIntervalInput.addEventListener('input', (e) => {
    intervalValueDisplay.textContent = e.target.value;
  });

  // Save settings
  saveBtn.addEventListener('click', () => {
    const backendUrl = backendUrlInput.value.trim() || 'http://localhost:8000';
    const captureInterval = parseInt(captureIntervalInput.value, 10) || 30;
    
    // Parse blocklist
    const rawBlocklist = blocklistInput.value.split('\n');
    const blocklist = rawBlocklist
      .map(domain => domain.trim().toLowerCase())
      .filter(domain => domain.length > 0);

    chrome.storage.local.set(
      {
        backendUrl,
        captureInterval,
        blocklist
      },
      () => {
        // Show success message
        saveStatus.textContent = 'Preferences saved!';
        saveStatus.classList.add('show');
        
        setTimeout(() => {
          saveStatus.classList.remove('show');
        }, 3000);
      }
    );
  });
});
