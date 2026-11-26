(() => {
  const names = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo'];
  document.querySelectorAll('.fixed-days').forEach(source => {
    const original = [...source.querySelectorAll('.fixed-day')].map((row, index) => ({
      index, check: row.querySelector('input[type=checkbox]'),
      start: row.querySelector('input[name^="inicio_"]'), end: row.querySelector('input[name^="fin_"]')
    }));
    if (original.length !== 7 || original.some(day => !day.check || !day.start || !day.end)) return;
    const editor = document.createElement('div'); editor.className = 'fixed-editor';
    const list = document.createElement('div'); list.className = 'fixed-editor-list';
    const add = document.createElement('button'); add.type = 'button'; add.className = 'fixed-add-day'; add.textContent = '+ Agregar día';
    editor.append(list, add); source.after(editor); source.hidden = true;
    const rows = [];
    function sync() {
      original.forEach(d => { d.check.checked = false; d.start.value = ''; d.end.value = ''; });
      rows.forEach(row => { const d = original[Number(row.select.value)]; if (!d) return; d.check.checked = true; d.start.value = row.start.value; d.end.value = row.end.value; });
      const selected = rows.map(row => row.select.value);
      rows.forEach(row => [...row.select.options].forEach(option => { option.disabled = option.value !== row.select.value && selected.includes(option.value); }));
      add.disabled = rows.length >= names.length;
    }
    function addRow(day, initialStart = '', initialEnd = '') {
      const row = document.createElement('div'); row.className = 'fixed-editor-row';
      const dayLabel = document.createElement('label'); dayLabel.textContent = 'Día';
      const select = document.createElement('select'); select.setAttribute('aria-label', 'Día de la semana');
      names.forEach((name, i) => { const option = document.createElement('option'); option.value = String(i); option.textContent = name; select.append(option); }); select.value = String(day); dayLabel.append(select);
      const startLabel = document.createElement('label'); startLabel.textContent = 'Inicio'; const start = document.createElement('input'); start.type = 'time'; start.value = initialStart; start.required = true; startLabel.append(start);
      const endLabel = document.createElement('label'); endLabel.textContent = 'Término'; const end = document.createElement('input'); end.type = 'time'; end.value = initialEnd; end.required = true; endLabel.append(end);
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'fixed-remove-day'; remove.setAttribute('aria-label', 'Quitar día'); remove.title = 'Quitar día';
      remove.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13M10 11v6M14 11v6"/></svg>';
      row.append(dayLabel, startLabel, endLabel, remove); list.append(row);
      const data = {node: row, select, start, end}; rows.push(data);
      [select, start, end].forEach(el => el.addEventListener('input', sync)); select.addEventListener('change', sync);
      remove.addEventListener('click', () => { rows.splice(rows.indexOf(data), 1); row.remove(); sync(); add.focus(); });
    }
    const initial = original.filter(d => d.check.checked).map(d => ({day:d.index, start:d.start.value, end:d.end.value}));
    initial.forEach(d => addRow(d.day, d.start, d.end));
    if (!rows.length) addRow(0);
    add.addEventListener('click', () => { const used = rows.map(row => Number(row.select.value)); const next = names.findIndex((_, i) => !used.includes(i)); if (next >= 0) { addRow(next); sync(); rows[rows.length - 1].select.focus(); } });
    source.closest('form').addEventListener('submit', sync); sync();
  });
})();
