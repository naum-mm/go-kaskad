# Веб-панель: подробная инструкция

[Краткое описание и установка](../README.md)


Панель управляет TCP/UDP-перенаправлениями: создание, редактирование, включение,
выключение и удаление правил. Наружу опубликован только HTTPS-порт nginx.
Логин фиксирован: `admin`. Установщик спрашивает **только пароль и порт панели**
(по умолчанию 8443). Пароль — не менее 12 символов и не более 72 байт; хеш — bcrypt.

Установка на Debian/Ubuntu выполняется одной командой:

```bash
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/naum-mm/go-kaskad/main/install.sh | sudo bash
```

Запускайте на Linux-сервере, который будет пересылать трафик, с root-доступом,
Docker Engine и iptables/nftables. Установщик устанавливает отсутствующий Docker
и Compose из подписанных репозиториев дистрибутива. Rootless Docker и Docker Desktop
для управления firewall Linux-хоста не поддерживаются. Контейнер worker требует
`CAP_NET_ADMIN`; привилегированный режим и Docker socket ему не нужны.

Откройте `https://IP-сервера:ПОРТ`. Установщик генерирует самоподписанный сертификат
на 365 дней и выводит SHA-256-отпечаток. Сверьте отпечаток, затем добавьте сертификат
`/opt/go-kaskad/config/tls/server.crt` в доверенные. При доступе через публичный IP
за NAT или своё DNS-имя замените сертификат на подходящий для этого адреса.
TLS-файлы можно заменить без изменения пароля/порта; затем перезапустите nginx.
Установщик не меняет UFW/security groups: при необходимости разрешите выбранный
порт панели и порты правил в firewall провайдера.

### Устройство

```text
Браузер → nginx :выбранный_порт (HTTPS + Basic Auth)
               ├── GUI (статические HTML/CSS/JS)
               └── API (внутренняя Docker-сеть)
                     └── Unix socket → worker → gokaskad.sh --machine → iptables
                                          └── SQLite (Docker volume)
```

Четыре контейнера: `nginx`, `gui`, `api`, `worker`. У API нет опубликованных портов;
сеть `control` изолирована от внешней сети. Worker использует сеть хоста, чтобы
менять его NAT, но **не слушает TCP/UDP** — только Unix-сокет. SQLite не является
сетевым сервисом и хранится в `gokaskad_worker-data`; доступа из API к файлу БД нет.
Пароль хранится только в виде хеша nginx, не в `.env` и не в БД. Basic Auth закрывает
как GUI, так и `/api/`; изменяющие запросы дополнительно требуют заголовок и проверку
Origin. Браузер запоминает Basic Auth до закрытия сессии браузера.

Правила панели используют собственные цепочки `GK_DNAT`, `GK_SNAT`, `GK_FORWARD`
и зарезервированную conntrack-маску `0x4b530000/0xffff0000`. Другие цепочки не
очищаются. Панель показывает только свои правила; существующие правила старого
интерактивного CLI автоматически не импортируются. Не используйте старый
`--flush` одновременно с панелью: он очищает firewall целиком. Назначение — unicast
IPv4, не loopback; выбранный порт панели нельзя использовать как входящий порт
правила. Совпадения активных правил по протоколу и входящему порту запрещены.
Изменения влияют на новые соединения; существующие conntrack-сессии могут
продолжаться до завершения/таймаута.

Worker последовательно применяет изменения, затем сохраняет их в SQLite. При
ошибке выполняется восстановление предыдущих правил, запрос возвращает ошибку.
При старте worker восстанавливает правила из БД. Включение IP forwarding сохраняется
в `/etc/sysctl.d/90-gokaskad.conf`; BBR и глобальные firewall-политики установщик
не меняет. После перезапуска Docker при изменении его цепочек перезапустите worker,
чтобы проверить/восстановить переходы в цепочки Gokaskad.

### Управление и данные

```bash
cd /opt/go-kaskad/current
sudo docker compose ps
sudo docker compose logs -f --tail=100
sudo docker compose restart worker
```

Настройки nginx и сертификат: `/opt/go-kaskad/config`. Порт, путь к настройкам и
автоматически выбранный backend iptables (nft/legacy):
`/opt/go-kaskad/current/.env`. База: `rules.sqlite3` в volume `gokaskad_worker-data`.
Для резервной копии остановите worker, скопируйте volume и каталог config, затем
запустите worker. `docker compose down` сохраняет volume и уже применённые NAT-правила;
для удаления перенаправлений сначала удалите правила через панель. `down -v`
удаляет БД без возможности восстановления — не используйте для обычного обновления.

Установщик не перезаписывает существующую успешную установку. Для обновления
разместите новый checkout/архив в отдельном каталоге `/opt/go-kaskad/releases`,
скопируйте `.env` из `current`, выполните там `docker compose up -d --build --wait`
и после проверки переключите симлинк `current`. Compose project name и volumes
остаются прежними; заранее сделайте резервную копию. Сертификаты обновляются отдельно.

### Полное обновление

Для блока «Системный firewall» нужно обновить **GUI, API и worker**: замены только
HTML/CSS недостаточно. Существующий пароль, порт, сертификат и volume БД используются
повторно. Полное обновление из `main` на сервере:

```bash
sudo bash <<'UPDATE'
set -euo pipefail
root=/opt/go-kaskad
test -f "$root/current/.env"
release=$(mktemp -d "$root/releases/update.XXXXXX")
curl --proto '=https' --tlsv1.2 -fsSL https://codeload.github.com/naum-mm/go-kaskad/tar.gz/refs/heads/main |
  tar -xz --strip-components=1 -C "$release"
cp -p "$root/current/.env" "$release/.env"
cd "$release"
docker compose config --quiet
docker compose up -d --build --wait --wait-timeout 180
docker compose exec -T nginx nginx -t
ln -sfn "$release" "$root/current"
UPDATE
```

Системный firewall показывается отдельно и только для чтения: iptables-nft,
iptables-legacy, IPv4/IPv6 и отдельные цепочки nftables, включая fail2ban. Наборы
адресов nftables раскрываются в таблице; при ошибке чтения видна отметка неполного
обзора. Это снимок правил ядра, а не статус процесса fail2ban или список его настроек.

### JSON CLI для worker

Интерактивный CLI продолжает работать. Дополнительный машинный режим используется
в контейнере worker (Python 3, `STATE_DIR`, `MANAGEMENT_PORT`):

```bash
docker compose exec -T worker bash /app/gokaskad.sh --machine list
printf '%s\n' '{"id":"b29bc2cf-47c6-43bc-8e49-5d560378fe66","name":"web","protocol":"tcp","input_port":8080,"target_ip":"192.0.2.10","target_port":80,"enabled":true}' |
  docker compose exec -T worker bash /app/gokaskad.sh --machine put
```

Ответы — JSON; код 0 означает успех, 2 — неверный запрос, 1 — ошибку firewall/БД.
`delete` принимает `{"id":"UUID"}`, `reconcile` восстанавливает правила из БД.
`firewall` возвращает системные цепочки и правила, ничего не изменяя; API использует
для него только `GET /api/firewall`.

### Разработка и проверки

```bash
python3 -m unittest discover -s tests -v
bash -n gokaskad.sh install.sh
node --check web/gui/app.js
```

Для Docker-проверок нужен Linux Docker Engine. Реальный NAT проверяется в отдельном
сетевом namespace worker через override `network_mode: bridge`, чтобы не менять
firewall машины разработки. Production-конфигурация использует `network_mode: host`.

Интеграционный сценарий: подготовьте тестовые `admin.htpasswd` и TLS-файлы в
`CONFIG_DIR`, задайте `MANAGEMENT_PORT=18443`, затем:

```bash
docker compose -p gokaskad-test -f compose.yaml -f tests/compose.smoke.yaml up -d --build --wait
SMOKE_PASSWORD='<тестовый пароль>' SMOKE_CA="$CONFIG_DIR/tls/server.crt" python3 tests/docker_smoke.py
# Опционально, при установленном Playwright:
SMOKE_PASSWORD='<тестовый пароль>' node tests/browser.cjs
docker compose -p gokaskad-test -f compose.yaml -f tests/compose.smoke.yaml down -v
```

`tests/docker_smoke.py` отказывается работать с worker в сети хоста. Проверяются
настоящие TCP/UDP-запросы, HTTPS/аутентификация, CSRF, восстановление после рестарта,
отключение правил и отсутствие опубликованных портов внутренних сервисов.
GitHub Actions выполняет этот сценарий автоматически; browser-тест дополнительно
проверяет создание, редактирование, отключение, удаление правил и мобильную вёрстку.
