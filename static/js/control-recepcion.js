(() => {
  const form = document.querySelector('#control-form'), code = document.querySelector('#control-code'), result = document.querySelector('#control-result');
  const token = form.querySelector('[name=csrfmiddlewaretoken]').value;
  const el = (tag, text, cls) => { const item = document.createElement(tag); if (text != null) item.textContent = text; if (cls) item.className = cls; return item; };
  async function capacity() {
    try { const response = await fetch('/api/aforo/', {credentials:'same-origin'}); if (!response.ok) throw new Error(); const d=await response.json();
      document.querySelector('#aforo-personas').textContent=d.personas_dentro; document.querySelector('#aforo-maximo').textContent=d.capacidad_maxima; document.querySelector('#aforo-porcentaje').textContent=d.porcentaje_ocupacion+'%'; document.querySelector('#aforo-disponibles').textContent=d.capacidad_disponible; document.querySelector('#aforo-hora-actualizacion').textContent=(d.fecha_hora_actual.split(' ')[1] || ''); document.querySelector('#aforo-circle-progress').style.strokeDashoffset=Math.max(0,408.4*(1-d.porcentaje_ocupacion/100));
    } catch (_) { document.querySelector('#aforo-hora-actualizacion').textContent='No disponible'; }
  }
  async function rooms() {
    const list=document.querySelector('#room-list');
    try { const response=await fetch('/api/salas/disponibilidad/', {credentials:'same-origin'}); if (!response.ok) throw new Error(); const data=await response.json(); list.replaceChildren();
      if (!data.clases.length) { list.append(el('p','No hay clases programadas para el resto del día.','empty-message')); return; }
      data.clases.forEach(c => { const row=el('article',null,'room-row'), heading=el('div',null,'room-heading'); heading.append(el('strong',c.sala+' · '+c.clase),el('span',c.inicio+' – '+c.fin)); const progress=el('progress'); progress.max=Math.max(c.capacidad,1); progress.value=Math.min(c.reservados,c.capacidad); progress.setAttribute('aria-label',`Reservas para ${c.clase} en ${c.sala}`); const note=el('small',`${c.reservados} de ${c.capacidad} cupos reservados · ${c.disponibles} disponibles`); row.append(heading,progress,note); list.append(row); });
    } catch (_) { list.replaceChildren(el('p','No se pudieron consultar los cupos. Inténtalo nuevamente.','empty-message')); }
  }
  code.addEventListener('input',()=>{ if(code.value.trim()){code.removeAttribute('aria-invalid');document.querySelector('#control-error')?.remove();} });
  form.addEventListener('submit',async event=>{
    event.preventDefault(); document.querySelector('#control-error')?.remove(); code.removeAttribute('aria-invalid');
    if(!code.value.trim()){const error=el('p','Ingresa el código de acceso del socio.','access-error');error.id='control-error';error.setAttribute('role','alert');code.insertAdjacentElement('afterend',error);code.setAttribute('aria-invalid','true');code.setAttribute('aria-describedby',error.id);code.focus();return;}
    const button=form.querySelector('button[type=submit]');button.disabled=true;result.replaceChildren(el('p','Validando acceso…'));
    try {const response=await fetch('/api/acceso/validar/',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':token},body:JSON.stringify({identificador:code.value.trim(),tipo:form.querySelector('[name=tipo_acceso]:checked').value,metodo:'SIMULACION_QR'})});if(!response.ok)throw new Error();const d=await response.json();
      const box=el('div',null,'result-box '+(d.autorizado?'allowed':'denied'));box.append(el('strong',d.autorizado?'Acceso permitido':'Acceso denegado'),el('p',d.motivo));if(d.socio)box.append(el('p',d.socio.nombre_completo+' · '+d.socio.membresia_plan));result.replaceChildren(box);
      (d.clases_proximas||[]).forEach(c=>{const section=el('div',null,'attendance-box'), question=el('p',`¿Vienes a ${c.nombre} a las ${c.hora}?`), confirm=el('button','Sí, confirmar asistencia','button primary'), skip=el('button','No por ahora','button outline'), status=el('p');confirm.type=skip.type='button';status.setAttribute('role','status');skip.addEventListener('click',()=>section.remove());confirm.addEventListener('click',async()=>{confirm.disabled=true;try{const r=await fetch(`/api/reservas/${encodeURIComponent(c.reserva_id)}/confirmar-asistencia/`,{method:'POST',credentials:'same-origin',headers:{'X-CSRFToken':token}});const info=await r.json();status.textContent=info.mensaje||'No fue posible confirmar la asistencia.';if(r.ok&&info.exito){confirm.remove();skip.remove();}else confirm.disabled=false;}catch(_){status.textContent='No fue posible conectar. Inténtalo nuevamente.';confirm.disabled=false;}});section.append(question,confirm,skip,status);result.append(section);});
      if(d.registro_id&&d.socio){const tbody=document.querySelector('#control-history');tbody.querySelector('.empty-access-row')?.remove();const row=el('tr');[new Date().toLocaleString('es-CL'),d.socio.nombre_completo,d.socio.rut,d.tipo==='ENTRADA'?'Entrada':'Salida',d.autorizado?'Permitido':'Denegado',d.motivo,String(d.tiempo_respuesta_ms)+' ms'].forEach(value=>row.append(el('td',value)));tbody.prepend(row);while(tbody.children.length>10)tbody.lastElementChild.remove();}capacity();rooms();
    }catch(_){result.replaceChildren(el('p','No fue posible conectar. Inténtalo nuevamente.','access-error'));}finally{button.disabled=false;}
  });
  capacity();rooms();setInterval(capacity,5000);setInterval(rooms,30000);
})();
