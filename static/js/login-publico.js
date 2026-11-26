(() => {
  const button = document.querySelector('.show-password');
  const input = document.querySelector('#password');
  if (!button || !input) return;
  button.addEventListener('click', () => {
    const visible = input.type === 'password';
    input.type = visible ? 'text' : 'password';
    button.setAttribute('aria-pressed', String(visible));
    button.setAttribute('aria-label', visible ? 'Ocultar contraseña' : 'Mostrar contraseña');
  });
})();
(() => {
  const form = document.querySelector('.login-card form');
  if (!form) return;
  const fields = [...form.querySelectorAll('input[required]')];
  form.noValidate = true;
  function clearError(field) {
    field.removeAttribute('aria-invalid');
    const id = field.id + '-error';
    document.getElementById(id)?.remove();
    const describedBy = (field.getAttribute('aria-describedby') || '').split(/\s+/).filter(value => value && value !== id);
    if (describedBy.length) field.setAttribute('aria-describedby', describedBy.join(' '));
    else field.removeAttribute('aria-describedby');
  }
  function showError(field) {
    clearError(field);
    if (field.checkValidity()) return false;
    const error = document.createElement('p');
    error.id = field.id + '-error';
    error.className = 'field-error';
    error.setAttribute('role', 'alert');
    error.textContent = field.validity.valueMissing ? (field.id === 'username' ? 'Ingresa tu correo o usuario.' : 'Ingresa tu contraseña.') : field.validationMessage;
    field.closest('.field').append(error);
    field.setAttribute('aria-invalid', 'true');
    field.setAttribute('aria-describedby', [field.getAttribute('aria-describedby'), error.id].filter(Boolean).join(' '));
    return true;
  }
  fields.forEach(field => field.addEventListener('input', () => {
    if (field.getAttribute('aria-invalid') === 'true') {
      if (field.checkValidity()) clearError(field);
    }
  }));
  form.addEventListener('submit', event => {
    let firstInvalid = null;
    fields.forEach(field => { if (showError(field) && !firstInvalid) firstInvalid = field; });
    if (firstInvalid) { event.preventDefault(); firstInvalid.focus(); }
  });
})();
