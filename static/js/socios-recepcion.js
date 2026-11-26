(() => {
  document.addEventListener('toggle', event => { const menu=event.target; if(menu.matches?.('.staff-actions') && menu.open) document.querySelectorAll('.staff-actions[open]').forEach(other=>{if(other!==menu)other.open=false;}); }, true);
  document.addEventListener('click', event=>{document.querySelectorAll('.staff-actions[open]').forEach(menu=>{if(!menu.contains(event.target))menu.open=false;});});
  document.addEventListener('keydown', event=>{if(event.key==='Escape')document.querySelectorAll('.staff-actions[open]').forEach(menu=>{menu.open=false;menu.querySelector('summary')?.focus();});});
  document.querySelectorAll('.staff-form').forEach(form => {
    const fields=[...form.querySelectorAll('input:not([type=hidden]),select,textarea')];
    fields.forEach(field => { const container=field.closest('.staff-field'); if (container?.querySelector('.staff-error')) field.setAttribute('aria-invalid','true'); field.addEventListener('input',()=>{if(field.type==='checkbox'?field.checked:field.value.trim()){field.removeAttribute('aria-invalid');container?.querySelectorAll('.staff-error').forEach(item=>item.remove());}}); });
    form.addEventListener('submit',event=>{let first=null;fields.forEach(field=>{const container=field.closest('.staff-field')||field.closest('.staff-check'); if (!field.checkValidity() && container) { container.querySelectorAll('.staff-error').forEach(item=>item.remove()); const error=document.createElement('p');error.className='staff-error';error.setAttribute('role','alert');error.textContent=field.validity.valueMissing?'Completa este campo.':field.validity.typeMismatch&&field.type==='email'?'Ingresa un correo electrónico válido.':field.validationMessage;container.append(error);field.setAttribute('aria-invalid','true');if(!first)first=field;}});if(first){event.preventDefault();first.focus();}});
  });
})();
