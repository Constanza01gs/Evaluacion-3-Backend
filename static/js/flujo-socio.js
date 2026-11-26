(() => {
  const form = document.querySelector('#purchase-form');
  function showError(field) {
    const old = document.getElementById(field.id + '-client-error'); if (old) old.remove();
    field.removeAttribute('aria-invalid');
    if (field.checkValidity()) return false;
    const msg = document.createElement('p'); msg.className = 'field-error'; msg.id = field.id + '-client-error'; msg.setAttribute('role', 'alert');
    msg.textContent = field.validity.valueMissing ? 'Completa este campo.' : field.validity.typeMismatch ? 'Revisa el formato de este campo.' : field.validationMessage;
    (field.closest('.flow-field') || field.closest('.plan-grid') || field.parentElement).append(msg);
    field.setAttribute('aria-invalid', 'true'); field.setAttribute('aria-describedby', msg.id); return true;
  }
  document.querySelectorAll('.security-form input[type=password]').forEach(field => {
    const wrap = document.createElement('div'); wrap.className = 'password-control'; field.parentNode.insertBefore(wrap, field); wrap.append(field);
    const button = document.createElement('button'); button.type = 'button'; button.className = 'password-toggle';
    const nombre = field.name === 'respuesta' ? 'respuesta de seguridad' : 'contraseña';
    button.setAttribute('aria-label', `Mostrar ${nombre}`); button.setAttribute('aria-pressed', 'false');
    button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/><path class="eye-slash" d="M4 4l16 16"/></svg>';
    button.addEventListener('click', () => { const visible = field.type === 'password'; field.type = visible ? 'text' : 'password'; button.setAttribute('aria-pressed', String(visible)); button.setAttribute('aria-label', `${visible ? 'Ocultar' : 'Mostrar'} ${nombre}`); }); wrap.append(button);
  });
  document.querySelectorAll('.validated-form').forEach(f => {
    const fields = [...f.querySelectorAll('input:not([type=hidden]), select, textarea')];
    fields.forEach(field => field.addEventListener('input', () => { if (field.value.trim()) { field.removeAttribute('aria-invalid'); document.getElementById(field.id + '-client-error')?.remove(); field.closest('.flow-field')?.querySelectorAll('.field-error').forEach(error => error.remove()); } }));
    f.addEventListener('submit', e => { let first = null; fields.forEach(field => { if (showError(field) && !first) first = field; }); if (first) { e.preventDefault(); first.focus(); } });
  });
  if (form) {
    const one = document.querySelector('#purchase-plan'), two = document.querySelector('#purchase-payment');
    const step1 = document.querySelector('#steps-one'), step2 = document.querySelector('#steps-two');
    const next = document.querySelector('#purchase-next'), back = document.querySelector('#purchase-back'), submit = document.querySelector('#purchase-submit');
    const change = document.querySelector('#change-plan'), options = document.querySelector('#plan-options');
    if (change) { if (!form.querySelector('.field-error')) options.hidden = true; change.addEventListener('click', () => { options.hidden = !options.hidden; change.setAttribute('aria-expanded', String(!options.hidden)); }); }
    const setStep = number => { one.hidden = number !== 1; step1.hidden = number !== 1; next.hidden = number !== 1; two.hidden = number !== 2; step2.hidden = number !== 2; back.hidden = number !== 2; submit.hidden = number !== 2; };
    setStep(1);
    next.addEventListener('click', () => {
      const chosen = form.querySelector('input[name=plan]:checked');
      if (!chosen) { options.hidden = false; form.querySelector('input[name=plan]')?.focus(); const first = form.querySelector('input[name=plan]'); if (first) { first.id = 'plan-choice'; showError(first); } return; }
      const date = form.querySelector('[name=fecha_inicio]'); if (showError(date)) { date.focus(); return; }
      setStep(2);
    });
    back.addEventListener('click', () => setStep(1));
    form.addEventListener('submit', e => { const rut = form.querySelector('[name=rut]'), method = form.querySelector('[name=metodo_pago]'); if (showError(rut)) { e.preventDefault(); setStep(2); rut.focus(); } else if (showError(method)) { e.preventDefault(); setStep(2); method.focus(); } });
    if (document.querySelector('#purchase-payment .field-error')) setStep(2);
    form.querySelectorAll('input,select').forEach(field => field.addEventListener('input', () => { if (field.value.trim()) { field.removeAttribute('aria-invalid'); document.getElementById(field.id + '-client-error')?.remove(); field.closest('.flow-field')?.querySelectorAll('.field-error').forEach(error => error.remove()); } }));
  }
  const confirm = document.querySelector('#confirm-purchase');
  if (confirm) confirm.addEventListener('submit', () => { confirm.querySelector('button[type=submit]').disabled = true; });
  const ret = document.querySelector('#purchase-return'); if (ret) ret.addEventListener('click', e => { if (document.referrer && new URL(document.referrer).pathname === ret.pathname) { e.preventDefault(); history.back(); } });
  const receipt = document.querySelector('#receipt-content'), loading = document.querySelector('#receipt-loading');
  if (receipt && loading) {
    const key = 'fitlife-receipt-' + receipt.dataset.receipt;
    let played = false; try { played = sessionStorage.getItem(key) === '1'; } catch (_) {}
    if (!played) { receipt.hidden = true; loading.hidden = false; requestAnimationFrame(() => requestAnimationFrame(() => loading.querySelector('.loading-bar span').classList.add('running'))); setTimeout(() => { loading.hidden = true; receipt.hidden = false; try { sessionStorage.setItem(key, '1'); } catch (_) {} }, 5000); }
  }
})();
