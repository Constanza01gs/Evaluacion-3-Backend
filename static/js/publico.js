(() => {
  const links = [...document.querySelectorAll('.nav a[href^="#"]')];
  const targets = links.map(link => document.querySelector(link.getAttribute('href'))).filter(Boolean);
  function updateNavigation() {
    const offset = (document.querySelector('.header')?.offsetHeight || 0) + 35;
    let current = targets[0];
    for (const target of targets) if (target.getBoundingClientRect().top <= offset) current = target;
    if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 5) current = targets[targets.length - 1];
    for (const link of links) {
      const active = link.getAttribute('href') === '#' + current?.id;
      link.classList.toggle('is-active', active);
      if (active) link.setAttribute('aria-current', 'location'); else link.removeAttribute('aria-current');
    }
  }
  window.addEventListener('scroll', updateNavigation, {passive: true});
  window.addEventListener('resize', updateNavigation);
  updateNavigation();
})();
(() => {
  const toggle = document.querySelector('.classes-toggle');
  const section = document.querySelector('#actividades');
  if (!toggle || !section) return;
  toggle.addEventListener('click', () => {
    const opening = section.hidden;
    section.hidden = !opening;
    toggle.setAttribute('aria-expanded', String(opening));
    toggle.textContent = opening ? 'Ocultar clases y horarios' : 'Ver clases y horarios';
    if (opening) section.scrollIntoView({behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start'});
  });
})();
