const $ = (id) => document.getElementById(id);
let rules = [];
let busy = false;
const form = $('rule-form');

async function request(path = '/api/rules', method = 'GET', body) {
  const response = await fetch(path, {
    method, headers: {'Content-Type': 'application/json', 'X-Gokaskad': '1'},
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: 'no-store',
  });
  let data;
  try { data = await response.json(); } catch { throw new Error('Сервер недоступен. Проверьте подключение и попробуйте ещё раз.'); }
  if (!response.ok) throw new Error(data.error || `Ошибка сервера (${response.status})`);
  return data;
}

function notice(text, error = false) {
  $('message').textContent = text;
  $('message').className = error ? 'error' : 'success';
  $('message').hidden = !text;
}

function render(data) {
  rules = data.rules;
  $('total').textContent = rules.length;
  $('active').textContent = rules.filter(r => r.enabled).length;
  $('protocols').textContent = [...new Set(rules.map(r => r.protocol.toUpperCase()))].join(' / ') || '—';
  $('count').textContent = rules.length;
  $('connection').textContent = '● Worker подключён';
  $('connection').className = 'badge online';
  $('empty').hidden = rules.length > 0;
  $('rules').replaceChildren();
  for (const rule of rules) {
    const tr = document.createElement('tr');
    const cell = (text, cls = '') => { const td = document.createElement('td'); td.textContent = text; td.className = cls; tr.append(td); return td; };
    cell(rule.name || 'Без названия', 'rule-name');
    cell(rule.protocol.toUpperCase(), 'mono');
    cell(`:${rule.input_port}`, 'mono');
    cell(`${rule.target_ip}:${rule.target_port}`, 'mono destination');
    const state = cell('');
    const toggle = document.createElement('button');
    toggle.className = `status ${rule.enabled ? 'enabled' : ''}`;
    toggle.textContent = rule.enabled ? '● Активно' : '○ Выключено';
    toggle.setAttribute('aria-label', `${rule.enabled ? 'Выключить' : 'Включить'} ${rule.name || rule.input_port}`);
    toggle.onclick = () => mutate(() => request(`/api/rules/${rule.id}`, 'PUT', {...rule, enabled: !rule.enabled}));
    state.append(toggle);
    const actions = cell(''); actions.className = 'actions';
    for (const [text, label, handler] of [
      ['Изменить', 'Изменить правило', () => edit(rule)],
      ['×', 'Удалить правило', () => { if (confirm(`Удалить правило «${rule.name || rule.input_port}»?`)) mutate(() => request(`/api/rules/${rule.id}`, 'DELETE')); }],
    ]) {
      const button = document.createElement('button'); button.className = 'secondary small';
      button.textContent = text; button.setAttribute('aria-label', label); button.onclick = handler; actions.append(button);
    }
    $('rules').append(tr);
  }
}

async function refresh() {
  if (busy) return;
  busy = true; $('refresh').disabled = true;
  try { render(await request()); notice(''); }
  catch (error) { notice(error.message, true); $('connection').textContent = '● Нет связи с worker'; $('connection').className = 'badge offline'; }
  finally { busy = false; $('refresh').disabled = false; }
}

async function mutate(operation) {
  if (busy) return;
  busy = true;
  document.querySelectorAll('button').forEach(b => b.disabled = true);
  try { render(await operation()); notice('Изменения применены.'); return true; }
  catch (error) { notice(error.message, true); $('form-error').textContent = error.message; return false; }
  finally { busy = false; document.querySelectorAll('button').forEach(b => b.disabled = false); }
}

function edit(rule) {
  if (busy) return;
  form.reset(); $('form-error').textContent = '';
  const values = rule || {id: crypto.randomUUID(), name: '', protocol: 'tcp', input_port: '', target_ip: '', target_port: '', enabled: true};
  for (const [key, value] of Object.entries(values)) {
    const input = form.elements.namedItem(key);
    if (input.type === 'checkbox') input.checked = value; else input.value = value;
  }
  $('editor-title').textContent = rule ? 'Изменить правило' : 'Новое правило';
  $('editor').showModal();
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(form));
  data.input_port = Number(data.input_port); data.target_port = Number(data.target_port);
  data.enabled = form.elements.namedItem('enabled').checked;
  if (await mutate(() => request(`/api/rules/${data.id}`, 'PUT', data))) $('editor').close();
});
$('new-rule').onclick = $('empty-new').onclick = () => edit();
$('close-editor').onclick = $('cancel-editor').onclick = () => $('editor').close();
$('refresh').onclick = refresh;
refresh();
