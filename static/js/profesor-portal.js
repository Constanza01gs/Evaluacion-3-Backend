(() => {
  const toggle=document.querySelector('.menu-toggle'),menu=document.querySelector('#menu-profesor');if(!toggle||!menu)return;
  const close=()=>{menu.classList.remove('open');toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-label','Abrir menú');};
  toggle.addEventListener('click',()=>{const open=menu.classList.toggle('open');toggle.setAttribute('aria-expanded',String(open));toggle.setAttribute('aria-label',open?'Cerrar menú':'Abrir menú');});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&menu.classList.contains('open')){close();toggle.focus();}});
  document.addEventListener('click',event=>{if(menu.classList.contains('open')&&!menu.contains(event.target)&&!toggle.contains(event.target))close();});
})();
