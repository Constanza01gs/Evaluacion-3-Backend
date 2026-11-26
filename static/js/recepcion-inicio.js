(() => {
  const toggle = document.querySelector('.menu-toggle'), menu = document.querySelector('#menu-recepcion');
  if (toggle && menu) {
    toggle.addEventListener('click', () => { const open = menu.classList.toggle('open'); toggle.setAttribute('aria-expanded', String(open)); toggle.setAttribute('aria-label', open ? 'Cerrar menú' : 'Abrir menú'); });
    document.addEventListener('keydown', event => { if (event.key === 'Escape' && menu.classList.contains('open')) { menu.classList.remove('open'); toggle.setAttribute('aria-expanded', 'false'); toggle.setAttribute('aria-label', 'Abrir menú'); toggle.focus(); } });
    document.addEventListener('click', event => { if (menu.classList.contains('open') && !menu.contains(event.target) && !toggle.contains(event.target)) { menu.classList.remove('open'); toggle.setAttribute('aria-expanded', 'false'); toggle.setAttribute('aria-label', 'Abrir menú'); } });
  }
  const form = document.querySelector('#form-simulador-qr'), input = document.querySelector('#qr-identificador-input');
  if (form && input) {
    form.noValidate = true;
    const clear = () => { input.removeAttribute('aria-invalid'); input.removeAttribute('aria-describedby'); document.querySelector('#acceso-error')?.remove(); };
    input.addEventListener('input', () => { if (input.value.trim()) clear(); });
    form.addEventListener('submit', event => {
      clear();
      if (!input.value.trim()) {
        event.preventDefault(); input.setAttribute('aria-invalid', 'true'); input.setAttribute('aria-describedby', 'acceso-error');
        const error = document.createElement('p'); error.id = 'acceso-error'; error.className = 'access-error'; error.setAttribute('role', 'alert'); error.textContent = 'Ingresa el código de acceso del socio.'; input.insertAdjacentElement('afterend', error); input.focus();
      }
    });
  }
  const table = document.querySelector('#tabla-ultimos-accesos-body');
  if (table) new MutationObserver(() => { if (table.querySelector('tr:not(.empty-access-row)')) table.querySelector('.empty-access-row')?.remove(); }).observe(table, {childList: true});
})();
