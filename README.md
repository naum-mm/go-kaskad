# Gokaskad

[Русский](#русский) · [English](#english)

## Русский

**Gokaskad — простая веб-панель для перенаправления TCP/UDP-портов на Linux-сервере.**

Например: соединение с портом `8080` вашего сервера отправляется на
`192.0.2.10:80`. Правила можно создавать, изменять, включать, выключать и удалять
в браузере. Они сохраняются и восстанавливаются после перезапуска.

### Установка одной командой

Нужен сервер с Debian/Ubuntu и root-доступом. Установщик установит Docker и
Docker Compose, если их ещё нет.

```bash
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/naum-mm/go-kaskad/main/install.sh | sudo bash
```

Установщик спросит только:

1. **Пароль администратора** — от 12 символов, до 72 байт.
2. **Порт панели** — по умолчанию `8443`.

Откройте `https://IP-сервера:ПОРТ`. Логин: **`admin`**.
HTTPS-сертификат создаётся автоматически. Он самоподписанный: сверьте выведенный
установщиком отпечаток и добавьте сертификат в доверенные.
Если у сервера есть firewall или security group, разрешите выбранный порт панели
и порты, которые хотите перенаправлять.

### Как устроено

- **nginx** — единственная внешняя точка входа: HTTPS и пароль.
- **GUI** — веб-панель управления правилами.
- **API** — работает во внутренней Docker-сети, без внешнего порта.
- **Worker** — вызывает JSON-режим CLI и применяет правила iptables.
- **SQLite** — хранит правила в Docker volume, не имеет сетевого порта.

Worker получает задания через Unix-сокет и не слушает TCP/UDP. Он управляет
firewall хоста с `CAP_NET_ADMIN`, без privileged-режима и Docker socket.
Панель управляет своими цепочками; старые правила CLI автоматически не импортируются.
OpenBSD-скрипт остаётся отдельным CLI, веб-панель предназначена для Linux.

[Подробная инструкция: запуск, данные, обновления и тесты](docs/web.ru.md)

## English

**Gokaskad is a simple web panel for forwarding TCP/UDP ports on a Linux server.**

For example, a connection to port `8080` on your server is forwarded to
`192.0.2.10:80`. Create, edit, enable, disable, and delete rules in your browser.
Rules are saved and restored after a restart.

### One-command installation

Use a Debian/Ubuntu server with root access. The installer installs Docker and
Docker Compose if they are missing.

```bash
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/naum-mm/go-kaskad/main/install.sh | sudo bash
```

The installer asks for just:

1. **Admin password** — at least 12 characters, up to 72 bytes.
2. **Panel port** — `8443` by default.

Open `https://SERVER-IP:PORT`. Username: **`admin`**.
An HTTPS certificate is generated automatically. It is self-signed: verify the
fingerprint printed by the installer and trust the certificate.
If your server has a firewall or security group, allow the selected panel port
and the ports you want to forward.

### How it works

- **nginx** — the only public entry point, with HTTPS and password protection.
- **GUI** — the rule management interface.
- **API** — runs on an internal Docker network, with no published port.
- **Worker** — calls the CLI's JSON interface to apply iptables rules.
- **SQLite** — stores rules in a Docker volume and has no network port.

The worker accepts jobs through a Unix socket and has no TCP/UDP listener. It
manages the host firewall with `CAP_NET_ADMIN`, without privileged mode or a
Docker socket. The panel manages its own chains; existing CLI rules are not
imported automatically. The OpenBSD script remains a separate CLI; the web panel
requires Linux.

[Detailed web guide (Russian)](docs/web.ru.md) · [Original CLI guide (English)](docs/cli.en.md)
