(() => {
  const dateLabel = document.querySelector('#launch-date');
  const countdown = document.querySelector('.launch-countdown');
  let interval;
  async function loadLaunch() {
    try {
      const response = await fetch('/launch-status', { cache: 'no-store' });
      if (!response.ok) throw new Error('Launch details unavailable');
      const status = await response.json();
      if (!status.launchAt) return;
      const target = Date.parse(status.launchAt);
      if (!Number.isFinite(target)) throw new Error('Invalid launch date');
      // Use the server clock, with a monotonic elapsed timer, rather than the visitor's clock.
      const serverNow = Date.parse(status.serverNow);
      if (!Number.isFinite(serverNow)) throw new Error('Invalid server time');
      const startedAt = performance.now();
      dateLabel.textContent = 'Launching ' + new Intl.DateTimeFormat('en-GB', {
        dateStyle: 'full', timeStyle: 'short', timeZone: 'Europe/London'
      }).format(new Date(target)) + ' (UK time)';
      countdown.hidden = false;
      function tick() {
        const remaining = Math.max(0, Math.ceil((target - serverNow - (performance.now() - startedAt)) / 1000));
        const values = [Math.floor(remaining / 86400), Math.floor(remaining / 3600) % 24, Math.floor(remaining / 60) % 60, remaining % 60];
        ['days', 'hours', 'minutes', 'seconds'].forEach((id, index) => {
          document.getElementById(id).textContent = String(values[index]).padStart(2, '0');
        });
        if (remaining === 0) {
          clearInterval(interval);
          dateLabel.textContent = 'The reef is open! Loading the website…';
          setTimeout(() => window.location.replace('/'), 1200);
        }
      }
      tick();
      if (target > serverNow) interval = setInterval(tick, 1000);
    } catch {
      dateLabel.textContent = 'Join Discord for launch updates or try again shortly';
    }
  }
  loadLaunch();
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) { clearInterval(interval); loadLaunch(); }
  });
})();
