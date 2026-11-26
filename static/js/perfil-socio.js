(() => {
  const form = document.querySelector('.profile-form');
  if (!form) return;
  const fields = [...form.querySelectorAll('input:not([type="hidden"])')];
  const clear = field => {
    field.removeAttribute('aria-invalid');
    const id = field.id + '-client-error';
    document.getElementById(id)?.remove();
    const described = (field.getAttribute('aria-describedby') || '').split(/\s+/).filter(value => value && value !== id);
    if (described.length) field.setAttribute('aria-describedby', described.join(' ')); else field.removeAttribute('aria-describedby');
  };
  fields.forEach(field => {
    const server = document.getElementById(field.id + '-server-error');
    if (server) { field.setAttribute('aria-invalid', 'true'); field.setAttribute('aria-describedby', server.id); }
    field.addEventListener('input', () => { if (field.value.trim()) { clear(field); server?.remove(); } });
  });
  form.addEventListener('submit', event => {
    let first = null;
    fields.forEach(field => {
      clear(field);
      if (!field.checkValidity()) {
        const error = document.createElement('p');
        error.id = field.id + '-client-error'; error.className = 'field-error'; error.setAttribute('role', 'alert');
        error.textContent = field.validity.valueMissing ? 'Completa este campo.' : field.type === 'email' && field.validity.typeMismatch ? 'Ingresa un correo electrónico válido.' : field.validationMessage;
        field.closest('.profile-field').append(error); field.setAttribute('aria-invalid', 'true'); field.setAttribute('aria-describedby', error.id);
        if (!first) first = field;
      }
    });
    if (first) { event.preventDefault(); first.focus(); }
  });
})();
