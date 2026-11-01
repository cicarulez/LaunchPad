(() => {
  const storageKey = 'launchpad-theme';
  const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
  const validTheme = value => ['light', 'dark'].includes(value) ? value : 'system';
  let preference = 'system';
  try {
    preference = validTheme(localStorage.getItem(storageKey));
  } catch {
    // Il tema funziona anche quando il browser impedisce il salvataggio.
  }

  function applyTheme() {
    const theme = preference === 'system' ? (systemTheme.matches ? 'dark' : 'light') : preference;
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]').content = theme === 'dark' ? '#121d22' : '#f4f0e8';
    const select = document.querySelector('#theme');
    if (select) select.value = preference;
  }

  // Eseguito prima del CSS per evitare un lampo del tema chiaro all'apertura.
  applyTheme();
  systemTheme.addEventListener('change', () => {
    if (preference === 'system') applyTheme();
  });
  window.addEventListener('storage', event => {
    if (event.storageArea !== localStorage || (event.key !== storageKey && event.key !== null)) return;
    preference = validTheme(event.newValue);
    applyTheme();
  });
  document.addEventListener('DOMContentLoaded', () => {
    const select = document.querySelector('#theme');
    select.value = preference;
    select.addEventListener('change', () => {
      preference = validTheme(select.value);
      applyTheme();
      try {
        if (preference === 'system') localStorage.removeItem(storageKey);
        else localStorage.setItem(storageKey, preference);
      } catch {
        // Mantieni la scelta per la pagina corrente se lo storage non è disponibile.
      }
    });
  });
})();
