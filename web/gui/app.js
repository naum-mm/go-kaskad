const $ = (id) => document.getElementById(id);
let rules = [];
let busy = false;
let firewallData = null;
let firewallBusy = false;
let firewallPending = false;
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
  finally { busy = false; $('refresh').disabled = false; $('firewall-refresh').disabled = firewallBusy; }
}

const actionNames = {ACCEPT: 'Разрешить', DROP: 'Заблокировать', REJECT: 'Отклонить', RETURN: 'Вернуться',
  DNAT: 'Перенаправить', SNAT: 'Изменить источник', MASQUERADE: 'Маскарадинг',
  CONNMARK: 'Метка соединения', MARK: 'Метка пакета', LOG: 'Записать в журнал', QUEUE: 'В очередь', NFQUEUE: 'В очередь'};

function renderFirewall() {
  if (!firewallData) return;
  const query = $('firewall-search').value.trim().toLowerCase();
  const fail2banOnly = $('firewall-fail2ban').checked;
  const chains = firewallData.chains.filter(chain => (!fail2banOnly || chain.fail2ban) &&
    (!query || JSON.stringify(chain).toLowerCase().includes(query)));
  const chainNames = new Set(firewallData.chains.map(c => JSON.stringify([c.backend, c.family, c.table, c.name])));
  $('firewall-rules').replaceChildren();
  for (const chain of chains) {
    const header = document.createElement('tr'); header.className = 'firewall-chain';
    const heading = document.createElement('td'); heading.colSpan = 7;
    const name = document.createElement('strong'); name.textContent = chain.name; heading.append(name);
    const meta = document.createElement('span');
    meta.textContent = ` ${chain.family} · ${chain.table} · ${chain.backend}`;
    heading.append(meta);
    if (chain.fail2ban) {
      const tag = document.createElement('span'); tag.className = 'firewall-tag'; tag.textContent = 'fail2ban'; heading.append(tag);
    }
    if (chain.policy) {
      const policy = document.createElement('span'); policy.className = 'firewall-policy';
      policy.textContent = `Политика: ${actionNames[chain.policy] || chain.policy}`; heading.append(policy);
    }
    if (chain.hook) { const hook = document.createElement('span'); hook.textContent = ` · ${chain.hook}`; heading.append(hook); }
    header.append(heading); $('firewall-rules').append(header);
    if (!chain.rules.length) {
      const row = document.createElement('tr'); const empty = document.createElement('td'); empty.colSpan = 7;
      empty.className = 'firewall-no-rules'; empty.textContent = 'В этой цепочке нет отдельных правил'; row.append(empty); $('firewall-rules').append(row);
    }
    chain.rules.forEach((rule, index) => {
      const row = document.createElement('tr');
      const cell = (text, cls = '') => { const td = document.createElement('td'); td.textContent = text; td.className = cls; row.append(td); return td; };
      cell(index + 1);
      cell(rule.protocol === 'Любой' ? rule.protocol : rule.protocol.toUpperCase());
      for (const field of ['source', 'destination']) {
        const td = cell(rule[field], 'firewall-address');
        if (rule[field + '_members']?.length) {
          const details = document.createElement('details'); const summary = document.createElement('summary');
          summary.textContent = `Адреса в наборе: ${rule[field + '_members'].length}`;
          const members = document.createElement('div'); members.className = 'firewall-members';
          for (const address of rule[field + '_members']) { const item = document.createElement('span'); item.textContent = address; members.append(item); }
          details.append(summary, members); td.append(details);
        }
      }
      cell(`${rule.source_port} → ${rule.destination_port}`, 'firewall-ports');
      const jump = chainNames.has(JSON.stringify([chain.backend, chain.family, chain.table, rule.action]));
      cell(actionNames[rule.action] || (jump ? `Переход: ${rule.action}` : rule.action));
      const conditions = cell('');
      if (rule.conditions.length) {
        const list = document.createElement('ul');
        for (const condition of rule.conditions) { const item = document.createElement('li'); item.textContent = condition; list.append(item); }
        conditions.append(list);
      } else { conditions.textContent = '—'; }
      $('firewall-rules').append(row);
    });
  }
  const time = new Date(firewallData.updated_at).toLocaleTimeString('ru-RU');
  $('firewall-state').textContent = !firewallData.sources.length ? 'Не удалось прочитать системный firewall.' :
    !chains.length ? (firewallData.chains.length ? 'По вашему фильтру цепочки не найдены.' : 'Системных цепочек не найдено.') : `Обновлено ${time}`;
  $('firewall-warnings').hidden = !firewallData.warnings.length;
  $('firewall-warnings').textContent = firewallData.warnings.length ? 'Обзор неполный: ' + firewallData.warnings.join('; ') + '.' : '';
}

async function refreshFirewall() {
  if (firewallBusy) { firewallPending = true; return; }
  firewallBusy = true; $('firewall-refresh').disabled = true;
  $('firewall-state').textContent = firewallData ? 'Обновление… Предыдущий снимок показан ниже.' : 'Загрузка правил…';
  try { firewallData = await request('/api/firewall'); renderFirewall(); }
  catch (error) { $('firewall-state').textContent = (firewallData ? 'Не удалось обновить. Ниже — предыдущий снимок. ' : 'Правила firewall недоступны. ') + error.message; }
  finally {
    firewallBusy = false; $('firewall-refresh').disabled = busy;
    if (firewallPending) { firewallPending = false; refreshFirewall(); }
  }
}

async function mutate(operation) {
  if (busy) return;
  busy = true;
  document.querySelectorAll('button').forEach(b => b.disabled = true);
  try { render(await operation()); notice('Изменения применены.'); refreshFirewall(); return true; }
  catch (error) { notice(error.message, true); $('form-error').textContent = error.message; return false; }
  finally { busy = false; document.querySelectorAll('button').forEach(b => b.disabled = false); $('firewall-refresh').disabled = firewallBusy; }
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
$('firewall-refresh').onclick = refreshFirewall;
$('firewall-search').oninput = $('firewall-fail2ban').onchange = renderFirewall;
refresh();
refreshFirewall();
