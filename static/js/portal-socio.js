(() => {
  const buttons = [...document.querySelectorAll('[data-view]')];
  buttons.forEach(button => button.addEventListener('click', () => {
    buttons.forEach(item => { const active = item === button; item.classList.toggle('selected', active); item.setAttribute('aria-pressed', String(active)); });
    document.getElementById('calendario').hidden = button.dataset.view !== 'calendario';
    document.getElementById('grafico').hidden = button.dataset.view !== 'grafico';
  }));
  const toggle = document.querySelector('.menu-toggle');
  const menu = document.querySelector('#menu-socio');
  if (toggle && menu) {
    toggle.addEventListener('click', () => { const open = menu.classList.toggle('open'); toggle.setAttribute('aria-expanded', String(open)); toggle.setAttribute('aria-label', open ? 'Cerrar menú' : 'Abrir menú'); });
    document.addEventListener('keydown', event => { if (event.key === 'Escape' && menu.classList.contains('open')) { menu.classList.remove('open'); toggle.setAttribute('aria-expanded', 'false'); toggle.setAttribute('aria-label', 'Abrir menú'); toggle.focus(); } });
    document.addEventListener('click', event => { if (menu.classList.contains('open') && !menu.contains(event.target) && !toggle.contains(event.target)) { menu.classList.remove('open'); toggle.setAttribute('aria-expanded', 'false'); toggle.setAttribute('aria-label', 'Abrir menú'); } });
  }
})();
