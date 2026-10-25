# Каскад / Kaskad

[Русский](#русский) · [English](#english)

## Русский

**Релиз v1.0.0:** проект называется «Каскад», репозиторий — `naum-mm/kaskad`. [Обновление существующей установки](docs/web.ru.md#обновление-до-v100--каскад) · [История изменений](CHANGELOG.md).

**Каскад — простая веб-панель для перенаправления TCP/UDP-портов на Linux-сервере.**

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
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/naum-mm/kaskad/main/install.sh | sudo bash
```

Установщик покажет найденную установку, работающие сервисы, контейнеры и занятые
TCP/UDP-порты, затем предложит меню:

```text
1 — Отдельная установка на выбранном порту
2 — Домен на 443 через существующий nginx / SNI
3 — Обновление, переустановка или смена режима
4 — Удаление с сохранением настроек или полностью
0 — Выход
```

В пунктах 1 и 2 выбирается сертификат: **самоподписанный** или **Let’s Encrypt
через Certbot + домен**. Для SNI домен нужен в обоих случаях. Пароль спрашивается
только при первой установке; при обновлении и восстановлении он сохраняется.
Логин: **`admin`**. Пароль — от 12 символов, до 72 байт.

### Два режима работы

| Режим | Адрес панели | Что нужно |
|---|---|---|
| Отдельный порт | `https://IP:ПОРТ` или `https://домен:ПОРТ` | Свободный порт; для Certbot также домен и свободный TCP 80 |
| Существующий nginx / SNI | `https://домен` | nginx на 80/443, DNS домена на VPS |

Самоподписанный сертификат вызывает предупреждение браузера: сверьте отпечаток,
выведенный установщиком, и добавьте сертификат в доверенные. Let’s Encrypt требует
доступного снаружи TCP 80 и правильных DNS A/AAAA; выпуск и продление выполняются
Certbot на хосте. Дополнительные вопросы Certbot отключены.

В SNI-режиме существующий nginx выбирает сертификат домена и направляет запросы
к панели. Другие сайты сохраняются. Порт панели привязан к loopback, только её nginx
подключается к общей Docker-сети. API и база остаются внутренними; TCP 80/443
зарезервированы от правил форварда. Проверка TLS между nginx остаётся включённой.

Поддерживаются nginx на хосте и Docker nginx с bind mount конфигурации, каталога
сертификатов `/etc/letsencrypt` и ACME webroot. Неизвестные proxy автоматически
не переписываются; установщик объяснит причину остановки. HAProxy для панели не нужен.

### Обновление и смена режима

Пункт **3** позволяет обновить код, пересоздать контейнеры или изменить режим,
порт и сертификат — например, перейти с `8443` и self-signed на `443` через SNI
и Certbot. Старую установку перед сменой режима скрипт обновит сам.

Перед операцией сохраняются конфигурация, сертификат и согласованная копия БД
в `/root/gokaskad-backups`. Пароль и правила сохраняются. Если новый режим
настроить не удалось, скрипт пытается восстановить предыдущую конфигурацию.
При перезапуске контейнеров возможен краткий перерыв перенаправлений.

[Подробная инструкция](docs/web.ru.md) · [Режимы и сертификаты](docs/proxy.en.md)

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

![Макет панели Kaskad](docs/panel-preview.png)

[Мобильный вид](docs/panel-mobile.png) · [Скачать автономный HTML-макет](docs/preview.html)

### Удаление с сервера

Запустите ту же команду установки и выберите **пункт 4**:

- **Сохранить настройки:** удаляются контейнеры и правила firewall Kaskad;
  пароль, сертификат, конфигурация и volume БД остаются для восстановления.
  При следующей установке используется прежний пароль и база правил.
- **Удалить полностью:** удаляются контейнеры, volume БД, свои правила,
  файлы проекта и добавленные неиспользуемые пакеты. Требуется подтверждение `DELETE`.

Общий Docker, пакеты и сертификаты, необходимые другим проектам, сохраняются.
Чужие сайты, сети и firewall не очищаются. Резервные копии в
`/root/gokaskad-backups` сохраняются отдельно; удалите их вручную, если они больше не нужны.

## English

**Release v1.0.0:** renamed to Kaskad, with the repository at `naum-mm/kaskad`. [Changelog](CHANGELOG.md) · [Upgrade an existing installation](docs/proxy.en.md#upgrading-to-v100).

**Kaskad is a simple web panel for forwarding TCP/UDP ports on a Linux server.**

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
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/naum-mm/kaskad/main/install.sh | sudo bash
```

The installer displays the existing installation, running services, containers
and occupied TCP/UDP ports, then opens a menu:

```text
1 — Install on a separate port
2 — Domain on port 443 through existing nginx / SNI
3 — Update, reinstall containers, or change operating mode
4 — Remove while keeping settings, or remove fully
0 — Exit
```

Options 1 and 2 offer **self-signed** or **Let’s Encrypt / Certbot** certificates.
SNI requires a domain with either certificate type. The password is requested
only on first installation; updates and restoration preserve it. Username:
**`admin`**. Passwords must be at least 12 characters and at most 72 bytes.

### Two operating modes

| Mode | Panel URL | Requirements |
|---|---|---|
| Separate port | `https://IP:PORT` or `https://domain:PORT` | A free panel port; Certbot also needs a domain and free TCP 80 |
| Existing nginx / SNI | `https://domain` | Existing nginx on 80/443 and DNS pointing to the VPS |

Self-signed certificates trigger a browser warning: verify the printed fingerprint
and trust the certificate. Let’s Encrypt requires public TCP 80 and correct DNS
A/AAAA records. Certbot runs and renews on the host without extra interactive prompts.

In SNI mode, nginx selects the domain certificate and routes requests to the panel.
Other sites are preserved. The panel binds to loopback; only its nginx joins the
existing Docker network. The API/database stay internal and TCP ports 80/443 are
reserved from forwarding rules. Upstream TLS verification remains enabled.

Supported: host nginx, or Docker nginx with bind-mounted configuration,
`/etc/letsencrypt` and ACME webroot. Unrecognized proxy layouts stop with an
explanation instead of being overwritten. HAProxy is not required for the panel.

### Updates and mode changes

Menu option **3** updates code, recreates containers, or changes mode, port and
certificate type—for example, self-signed HTTPS on 8443 to SNI/Certbot on 443.
Older code is upgraded automatically before a mode change.

Configuration, certificates and a consistent database snapshot are backed up
under `/root/gokaskad-backups`. Password and rules are preserved. Failed mode
changes attempt to restore the previous configuration. Container restarts may
briefly interrupt forwarding.

[Modes, certificates, recovery and automation](docs/proxy.en.md)

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

Run the same installer command and choose **option 4**:

- **Keep settings:** removes containers and Kaskad firewall rules; retains the
  password, certificates, configuration and database volume for restoration.
- **Full removal:** deletes project containers, database volumes, firewall rules,
  project files and introduced unused packages. Requires typing `DELETE`.

Shared Docker, packages and certificates needed by other projects are kept.
Other sites, networks and firewall rules are preserved. Backups in
`/root/gokaskad-backups` remain separate; remove them manually when no longer needed.
