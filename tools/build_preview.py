"""Build a self-contained, clearly labelled demo from the actual GUI sources."""
from pathlib import Path

root = Path(__file__).resolve().parents[1]
gui = root / "web/gui"
script = (gui / "app.js").read_text()
start = script.index("async function request(")
end = script.index("\nfunction notice(", start)
adapter = '''
function demoUuid() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = Math.floor(Math.random() * 16);
    return (c === 'x' ? r : (r & 3) | 8).toString(16);
  });
}
let demoRules = [
  {id:'b29bc2cf-47c6-43bc-8e49-5d560378fe66',name:'Веб-сервер · пример',protocol:'tcp',input_port:8080,target_ip:'192.0.2.10',target_port:80,enabled:true},
  {id:'b29bc2cf-47c6-43bc-8e49-5d560378fe67',name:'VPN · пример',protocol:'udp',input_port:51820,target_ip:'192.0.2.20',target_port:51820,enabled:false}
];
async function request(path = '/api/rules', method = 'GET', body) {
  if (method === 'PUT') {
    if (demoRules.some(r => r.id !== body.id && r.enabled && body.enabled && r.protocol === body.protocol && r.input_port === body.input_port))
      throw new Error('Этот протокол и входящий порт уже используются.');
    demoRules = demoRules.filter(r => r.id !== body.id).concat(body);
  }
  if (method === 'DELETE') demoRules = demoRules.filter(r => r.id !== path.split('/').pop());
  return {rules: demoRules.map(r => ({...r}))};
}
'''
script = (script[:start] + adapter + script[end:]).replace('● Worker подключён', '● Демонстрация')
script = script.replace('crypto.randomUUID()', 'demoUuid()')
html = (gui / "index.html").read_text()
html = html.replace('<link rel="stylesheet" href="/style.css"><script src="/app.js" defer></script>',
                    '<style>' + (gui / "style.css").read_text() + '</style>')
html = html.replace('<main>', '<main><p class="demo-note">ДЕМО / DEMO · Примеры правил, без подключения к серверу. / Sample rules, no server connection.</p>')
html = html.replace('class="brand" href="/"', 'class="brand" href="#top"')
html = html.replace('</body>', '<script>' + script + '</script></body>')
html = html.replace('<title>Gokaskad · Управление трафиком</title>', '<title>Gokaskad · Демо / Demo</title>')
target = root / "docs/preview.html"
target.write_text(html)
print(target)
