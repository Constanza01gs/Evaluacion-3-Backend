(() => {
  const form = document.querySelector('.access-card form');
  if (!form) return;
  const fields = [...form.querySelectorAll('input, select, textarea')].filter(field => field.type !== 'hidden');
  form.noValidate = true;
  function clear(field) {
    field.removeAttribute('aria-invalid');
    const id = field.id + '-client-error';
    document.getElementById(id)?.remove();
    const descriptions = (field.getAttribute('aria-describedby') || '').split(/\s+/).filter(value => value && value !== id);
    if (descriptions.length) field.setAttribute('aria-describedby', descriptions.join(' ')); else field.removeAttribute('aria-describedby');
  }
  function validate(field) {
    clear(field);
    if (field.checkValidity()) return false;
    const error = document.createElement('p');
    error.id = field.id + '-client-error'; error.className = 'field-error'; error.setAttribute('role', 'alert');
    error.textContent = field.validity.valueMissing ? 'Completa este campo.' : field.validity.typeMismatch && field.type === 'email' ? 'Ingresa un correo electrónico válido.' : field.validationMessage;
    field.closest('.field').append(error);
    field.setAttribute('aria-invalid', 'true');
    field.setAttribute('aria-describedby', [field.getAttribute('aria-describedby'), error.id].filter(Boolean).join(' '));
    return true;
  }
  function handleEdit(field) {
    // El aviso del navegador se retira al comenzar a corregir; al enviar se valida de nuevo.
    const clientError = document.getElementById(field.id + '-client-error');
    if (clientError && field.value.trim()) clear(field);
  }
  fields.forEach(field => {
    field.addEventListener('input', () => handleEdit(field));
    field.addEventListener('change', () => handleEdit(field));
  });
  form.addEventListener('submit', event => { let first = null; fields.forEach(field => { if (validate(field) && !first) first = field; }); if (first) { event.preventDefault(); first.focus(); } });
})();
