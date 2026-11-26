(() => {
  document.querySelectorAll('form[method="get"] input[type="search"][name="q"]').forEach(input => {
    const form=input.form; if(!form)return; let timer, controller, sequence=0;
    function replace(documento, selector){const current=document.querySelector(selector), next=documento.querySelector(selector);if(current&&next)current.replaceWith(next);}
    function updateResults(documento){
      if(input.id==='buscar-socio'){replace(documento,'.staff-table-wrap');replace(documento,'.members-count');}
      else if(input.id==='buscar'){replace(documento,'.staff-table-wrap');}
      else if(input.id==='activity-query'){replace(documento,'.activity-table-wrap');const old=document.querySelector('.activity-pagination'), fresh=documento.querySelector('.activity-pagination');if(old&&fresh)old.replaceWith(fresh);else if(old)old.remove();else if(fresh)document.querySelector('.activity-table-wrap')?.after(fresh);}
      else if(input.id==='socio-query'){const host=document.querySelector('#staff-search-panel'), next=documento.querySelector('#staff-search-panel .search-results');host?.querySelector('.search-results')?.remove();if(next)host?.append(next);}
      else if(input.id.startsWith('socio-search-')){const host=form.closest('.reserve-search'), remote=documento.getElementById(input.id)?.closest('.reserve-search'),next=remote?.querySelector('.class-search-results');host?.querySelector('.class-search-results')?.remove();if(next)host?.append(next);}
    }
    async function search(){const current=++sequence;controller?.abort();controller=new AbortController();const address=new URL(form.getAttribute('action')||location.pathname,location.origin);const data=new FormData(form);for(const [key,value] of data) if(typeof value==='string')address.searchParams.set(key,value);address.searchParams.delete('page');try{const response=await fetch(address,{credentials:'same-origin',signal:controller.signal,headers:{'X-Requested-With':'XMLHttpRequest'}});if(!response.ok)throw new Error();const doc=new DOMParser().parseFromString(await response.text(),'text/html');if(current===sequence)updateResults(doc);}catch(error){if(error.name!=='AbortError'){form.dataset.searchError='true';}}}
    input.addEventListener('input',()=>{clearTimeout(timer);controller?.abort();++sequence;timer=setTimeout(search,300);});
  });
})();