# Gokaskad

[Русский](#русский) · [English](#english)

## Русский

**Gokaskad — простая веб-панель для перенаправления TCP/UDP-портов на Linux-сервере.**

Например: соединение с портом `8080` вашего сервера отправляется на
`192.0.2.10:80`. Правила можно создавать, изменять, включать, выключать и удалять
в браузере. Они сохраняются и восстанавливаются после перезапуска.

Под правилами перенаправления показан **системный firewall**: отдельная серая
таблица только для просмотра. Видны цепочки, политики, адреса, порты и действия,
включая fail2ban в iptables и nftables для IPv4/IPv6. Есть поиск и фильтр fail2ban.
Если часть правил недоступна, панель предупреждает о неполном обзоре.

### Установка одной командой

Нужен сервер с Debian/Ubuntu и root-доступом. Установщик установит Docker и
Docker Compose, если их ещё нет.

```bash
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/naum-mm/go-kaskad/main/install.sh | sudo bash
```

Установщик сам определяет окружение и задаёт **два вопроса**:

- **На свободном сервере:** пароль администратора и порт панели (по умолчанию `8443`).
- **Если HTTPS уже обслуживает nginx:** домен панели и пароль администратора.

Пароль — от 12 символов, до 72 байт. Логин: **`admin`**.
На свободном сервере откройте `https://IP-сервера:ПОРТ`; установщик выведет отпечаток
самоподписанного сертификата. Разрешите выбранный порт в firewall провайдера.

### Установка рядом с существующим сайтом

Используйте **ту же команду установки**. Сначала направьте DNS домена панели
на этот VPS. Порты 80/443 должны быть доступны для проверки Let’s Encrypt.

Установщик добавит домен в существующий nginx, выпустит сертификат через Certbot
на хосте и настроит продление. Nginx выберет сертификат по **SNI** и направит запрос
в блок нужного домена. Сайты продолжат работать на своих доменах.

Панель привязывается к loopback; для nginx в Docker автоматически подключается
общая сеть. API и база остаются внутренними. TCP-порты 80/443 зарезервированы:
создать перенаправление на них в панели нельзя. Настройки сохраняются при обновлении.

Поддерживаются nginx на хосте и Docker nginx с доступными через bind mount
конфигурацией, ACME webroot и `/etc/letsencrypt`. Для неизвестной конфигурации
установщик остановится с объяснением. Отдельный HAProxy для панели не требуется.

[Подробности, продолжение после ошибки и ручной режим](docs/web.ru.md#домен-в-существующем-nginx)

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

### Посмотреть макет без установки

![Макет панели Gokaskad](docs/panel-preview.png)

[Мобильный вид](docs/panel-mobile.png) · [Скачать автономный HTML-макет](docs/preview.html)

### Удаление с сервера

**Удаляются все правила панели, база данных, пароль и сертификат. Восстановление
возможно только из вашей резервной копии.**

```bash
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/naum-mm/go-kaskad/main/uninstall.sh | sudo bash
```

Скрипт остановит контейнеры и удалит их вместе с volume базы, сетями, образами
приложения, цепочками `GK_*`, настройкой Gokaskad в sysctl и каталогом
`/opt/go-kaskad`. При автоматическом подключении домена также удаляются свой блок
nginx и hook продления. Созданный установщиком сертификат удаляется, если его не
используют другие блоки nginx. Общие сети, таймер Certbot и чужие настройки сохраняются.
Общие пакеты системы и Docker останутся.

Для новой установки, которая сохранила список добавленных пакетов, вместо
обычного удаления можно выбрать полную очистку:

```bash
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/naum-mm/go-kaskad/main/uninstall.sh | sudo bash -s -- --remove-packages
```

Этот режим дополнительно удалит только пакеты, которых не было до установки.
Если Docker используется другими приложениями или удаление затрагивает чужие
пакеты, очистка пакетов остановится и сохранит список в `install-state`.
Общий кэш сборки Docker не очищается: он может использоваться другими проектами.
Для старых установок список пакетов и прежнее значение IP forwarding неизвестны;
скрипт не угадывает их. Приложение удаляется обычной командой, а общие пакеты можно
проверить по `/var/log/apt/history.log` и удалить отдельно, если они больше не нужны.

## English

**Gokaskad is a simple web panel for forwarding TCP/UDP ports on a Linux server.**

For example, a connection to port `8080` on your server is forwarded to
`192.0.2.10:80`. Create, edit, enable, disable, and delete rules in your browser.
Rules are saved and restored after a restart.

A separate **system firewall** table appears below forwarding rules. It uses
muted gray text and is read-only. View chains, policies, addresses, ports and
actions, including fail2ban in iptables and nftables for IPv4/IPv6. Search and a
fail2ban filter are included. A warning appears if some rules cannot be read.

### One-command installation

Use a Debian/Ubuntu server with root access. The installer installs Docker and
Docker Compose if they are missing.

```bash
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/naum-mm/go-kaskad/main/install.sh | sudo bash
```

The installer detects the environment and asks **two questions**:

- **A standalone server:** administrator password and panel port (default `8443`).
- **An existing nginx on HTTPS:** panel domain and administrator password.

Passwords must be at least 12 characters and at most 72 bytes. Username: **`admin`**.
For a standalone installation, open `https://SERVER-IP:PORT`, verify the printed
self-signed certificate fingerprint, and allow the panel port in your provider firewall.

### Install alongside an existing website

Use **the same installation command**. Point the panel domain's DNS to this VPS
first. Ports 80/443 must be reachable for Let’s Encrypt validation.

The installer adds the domain to your existing nginx, obtains a certificate using
Certbot on the host, and sets up renewal. Nginx uses **SNI** to select the certificate
and routes requests to the matching domain. Existing sites keep their domains.

The panel binds to loopback. For Docker nginx, a shared network is configured
automatically. The API and database stay internal. TCP ports 80/443 are reserved
and cannot be used by panel forwarding rules. Settings persist when updating.

Supported: host nginx, or Docker nginx with bind-mounted configuration, ACME
webroot and `/etc/letsencrypt`. Unrecognized configurations stop with an explanation.
A separate HAProxy is not needed for the panel.

[Domain setup, recovery and manual mode](docs/proxy.en.md)

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

### Preview without installing

[Desktop preview](docs/panel-preview.png) · [Mobile preview](docs/panel-mobile.png) · [Standalone HTML demo](docs/preview.html)

### Uninstall

**This deletes the panel's rules, database, password, and certificate. You need
your own backup to restore them.**

```bash
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/naum-mm/go-kaskad/main/uninstall.sh | sudo bash
```

Removes containers, the database volume, project networks and application images,
`GK_*` firewall chains, the project's sysctl setting, and `/opt/go-kaskad`.
Automatic domain integration also removes its nginx block and renewal hook.
An installer-created certificate is deleted if no other nginx block references it.
Shared networks, the Certbot timer, other sites and system packages remain.

For a new installation with a saved package manifest, choose this instead to
also remove packages introduced by the installer:

```bash
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/naum-mm/go-kaskad/main/uninstall.sh | sudo bash -s -- --remove-packages
```

Package removal stops if other Docker resources remain or unrelated packages
would be removed. Shared Docker build cache is preserved. Older installations
did not record their package baseline or original IP forwarding value: use the
regular uninstall command, then review `/var/log/apt/history.log` before removing
any shared packages manually.
