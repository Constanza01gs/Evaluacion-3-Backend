(() => {
  const menu = document.querySelector('.notification-menu'); if (!menu) return;
  const list = menu.querySelector('.notification-preview'), dot = menu.querySelector('.notification-dot');
  async function refresh() {
    try {
      const response = await fetch(menu.dataset.url, { credentials: 'same-origin', headers: { 'Accept': 'application/json' }, cache: 'no-store' });
      if (!response.ok) throw new Error();
      const data = await response.json(); dot.hidden = !data.sin_leer; if (data.sin_leer) dot.setAttribute('aria-label', 'Tienes notificaciones sin leer'); else dot.removeAttribute('aria-label');
      list.replaceChildren();
      if (!data.avisos.length) { const p = document.createElement('p'); p.textContent = 'No tienes notificaciones.'; list.append(p); return; }
      data.avisos.forEach(aviso => { const row = document.createElement('article'), message = document.createElement('p'), time = document.createElement('time'); message.textContent = aviso.mensaje; time.textContent = aviso.fecha; if (aviso.url && aviso.url.startsWith('/') && !aviso.url.startsWith('//')) { const link = document.createElement('a'); link.href = aviso.url; link.append(message, time); row.append(link); } else { row.append(message, time); } list.append(row); });
    } catch (_) { list.replaceChildren(); const p = document.createElement('p'); p.textContent = 'No se pudieron cargar los avisos. Puedes verlos en la página de notificaciones.'; list.append(p); }
  }
  menu.addEventListener('toggle', () => { if (menu.open) refresh(); });
  document.addEventListener('click', event => { if (menu.open && !menu.contains(event.target)) menu.open = false; });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && menu.open) { menu.open = false; menu.querySelector('summary').focus(); } });
  refresh(); setInterval(refresh, 30000);
})();
