#!/usr/bin/env bash
# Run on the Linux server that will forward traffic. Inputs are read from /dev/tty.
set -Eeuo pipefail
# bash may be reading this script from curl or a heredoc. Docker must not
# consume the remaining installer commands as its interactive stdin.
docker() { command docker "$@" </dev/null; }
apt-get() { command apt-get "$@" </dev/null; }
umask 077
export -n ADMIN_PASSWORD
if [[ -n ${GOKASKAD_PASSWORD_FILE:-} ]]; then
    ADMIN_PASSWORD=$(cat "$GOKASKAD_PASSWORD_FILE")
fi
[[ $EUID -eq 0 ]] || { echo 'Запустите установщик через sudo bash.' >&2; exit 1; }
[[ $(uname -s) == Linux ]] || { echo 'Нужен Linux с Docker и iptables.' >&2; exit 1; }
type -P apt-get >/dev/null || { echo 'Автоустановка поддерживает Debian и Ubuntu.' >&2; exit 1; }

configure=false
resume=false
case ${1:-} in
    '') ;;
    --configure-domain) configure=true ;;
    --resume-domain) configure=true; resume=true ;;
    *) echo 'Usage: install.sh [--configure-domain|--resume-domain]' >&2; exit 2 ;;
esac
root=/opt/go-kaskad
[[ ! -L $root ]] || { echo 'Каталог установки не должен быть симлинком.' >&2; exit 1; }
install -d -m 700 "$root" "$root/config" "$root/config/tls" "$root/releases"
exec 9>"$root/install.lock"
flock -n 9 || { echo 'Установка/удаление уже выполняется.' >&2; exit 1; }
if ! $configure && [[ -e $root/current ]]; then
    echo 'Установка уже существует. Обновление — см. README; подключение домена — install.sh --configure-domain.' >&2
    exit 1
fi
if $configure; then
    [[ -f $root/current/.env ]] || { echo 'Сначала установите Gokaskad.' >&2; exit 1; }
    if ! $resume; then
        grep -q RESERVED_TCP_PORTS "$root/current/compose.yaml" || {
            echo 'Сначала обновите Gokaskad по инструкции README.' >&2; exit 1;
        }
    fi
fi
[[ ! -L /etc/sysctl.d/90-gokaskad.conf ]] || { echo 'Не заменяю симлинк /etc/sysctl.d/90-gokaskad.conf.' >&2; exit 1; }
state="$root/install-state"
install -d -m 700 "$state"
if [[ ! -f $state/packages.before ]]; then
    dpkg-query -W -f='${binary:Package}\t${db:Status-Status}\n' | awk '$2=="installed" {print $1}' | sort -u >"$state/packages.before"
    cat /proc/sys/net/ipv4/ip_forward >"$state/ip-forward.before"
    if [[ -f /etc/sysctl.d/90-gokaskad.conf ]]; then
        cp -p /etc/sysctl.d/90-gokaskad.conf "$state/sysctl.previous"
        touch "$state/sysctl.existed"
    fi
fi

work=$(mktemp -d)
cleanup() {
    result=$?
    set +e
    # Also record introduced dependencies if installation fails or is resumed.
    dpkg-query -W -f='${binary:Package}\t${db:Status-Status}\n' | awk '$2=="installed" {print $1}' | sort -u >"$state/packages.after"
    comm -13 "$work/packages.run.before" "$state/packages.after" >"$work/new-packages"
    touch "$state/new-packages"
    cat "$work/new-packages" "$state/new-packages" | sort -u >"$work/all-new-packages"
    cp "$work/all-new-packages" "$state/new-packages"
    rm -rf "$work"
    exit "$result"
}
trap cleanup EXIT
dpkg-query -W -f='${binary:Package}\t${db:Status-Status}\n' | awk '$2=="installed" {print $1}' | sort -u >"$work/packages.run.before"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl openssl iproute2 iptables apache2-utils util-linux python3
if ! type -P docker >/dev/null; then
    apt-get install -y --no-install-recommends docker.io
    systemctl enable --now docker
fi
if ! docker compose version >/dev/null 2>&1; then
    if apt-cache show docker-compose-v2 >/dev/null 2>&1; then
        apt-get install -y docker-compose-v2
    else
        apt-get install -y docker-compose
    fi
fi
docker compose version >/dev/null || { echo 'Нужен Docker Compose v2.' >&2; exit 1; }
docker info >/dev/null
mkdir "$work/source"
if [[ -n ${GOKASKAD_SOURCE_DIR:-} ]]; then
    cp -a "$GOKASKAD_SOURCE_DIR/." "$work/source/"
else
    curl --fail --show-error --silent --location --proto '=https' --tlsv1.2 \
        https://codeload.github.com/naum-mm/go-kaskad/tar.gz/refs/heads/main -o "$work/source.tar.gz" </dev/null
    tar -xzf "$work/source.tar.gz" --strip-components=1 -C "$work/source"
fi
[[ -f $work/source/compose.yaml && -f $work/source/web/worker/firewall.py ]] || {
    echo 'В main отсутствует веб-версия. Сначала опубликуйте изменения в GitHub.' >&2; exit 1;
}
source_helper="$work/source/tools/proxy_setup.py"
[[ -f $source_helper ]] || { echo 'В main отсутствует установщик домена.' >&2; exit 1; }
install -m 600 "$source_helper" "$root/config/proxy_setup.py"
helper="$root/config/proxy_setup.py"
mode=${INSTALL_MODE:-auto}
case $mode in auto|standalone|proxy) ;; *) echo 'INSTALL_MODE: auto, standalone или proxy.' >&2; exit 1 ;; esac
if $resume; then
    [[ -f $root/config/proxy.json ]] || { echo 'Нет незавершённой настройки домена.' >&2; exit 1; }
    python3 "$helper" resume
    exit
fi
if [[ $mode == auto && ${MANAGEMENT_BIND:-} == 127.0.0.1 && -n ${MANAGEMENT_PORT:-} ]] && ! $configure; then
    # Preserve the explicit manual reverse-proxy installation command.
    mode=standalone
fi
if [[ $mode != standalone ]]; then
    detected=$(python3 "$helper" discover --plan "$work/proxy-plan.json")
    if [[ $detected != standalone ]]; then
        mode=proxy
    elif [[ $mode == proxy ]] || $configure; then
        echo 'Не найден существующий nginx на TCP 80/443.' >&2; exit 1
    else
        mode=standalone
    fi
fi
if $configure && [[ $mode != proxy ]]; then
    echo '--configure-domain требует существующий nginx на TCP 80/443.' >&2; exit 1
fi
if [[ $mode == proxy ]]; then
    echo 'Обнаружен существующий nginx. Панель будет доступна по домену через HTTPS 443.'
    if [[ -z ${PANEL_DOMAIN:-} ]]; then
        read -r -p 'Домен панели (DNS уже должен указывать на этот сервер): ' PANEL_DOMAIN </dev/tty
    fi
    PANEL_DOMAIN=$(python3 "$helper" domain --domain "$PANEL_DOMAIN")
    MANAGEMENT_BIND=127.0.0.1
    RESERVED_TCP_PORTS="80,443${RESERVED_TCP_PORTS:+,$RESERVED_TCP_PORTS}"
    if $configure; then
        MANAGEMENT_PORT=$(awk -F= '$1=="MANAGEMENT_PORT" {print $2}' "$root/current/.env")
    elif [[ -z ${MANAGEMENT_PORT:-} ]]; then
        for candidate in $(seq 10443 10542); do
            if [[ -z $(ss -H -ltn "sport = :$candidate") ]]; then
                MANAGEMENT_PORT=$candidate
                break
            fi
        done
        [[ -n ${MANAGEMENT_PORT:-} ]] || { echo 'Нет свободного локального порта панели.' >&2; exit 1; }
    fi
    if ! command -v certbot >/dev/null; then
        if [[ -x /snap/bin/certbot ]]; then
            export PATH="$PATH:/snap/bin"
        else
            apt-get install -y --no-install-recommends certbot
        fi
    fi
fi
restore=false
if [[ -f $root/saved/env && -f $root/config/admin.htpasswd ]]; then restore=true; fi
if ! $configure && ! $restore; then
    if [[ -z ${ADMIN_PASSWORD:-} ]]; then
        read -r -s -p 'Пароль администратора (не менее 12 символов): ' ADMIN_PASSWORD </dev/tty
        echo >/dev/tty
    fi
    [[ ${#ADMIN_PASSWORD} -ge 12 && $ADMIN_PASSWORD != *$'\n'* && $ADMIN_PASSWORD != *$'\r'* ]] || {
        echo 'Пароль должен содержать не менее 12 символов без переводов строк.' >&2; exit 1;
    }
    (( $(printf '%s' "$ADMIN_PASSWORD" | wc -c) <= 72 )) || {
        echo 'Пароль bcrypt должен занимать не более 72 байт.' >&2; exit 1;
    }
    export -n ADMIN_PASSWORD
fi
if [[ -z ${MANAGEMENT_PORT:-} ]]; then
    read -r -p 'HTTPS-порт панели [8443]: ' MANAGEMENT_PORT </dev/tty
    MANAGEMENT_PORT=${MANAGEMENT_PORT:-8443}
fi
[[ $MANAGEMENT_PORT =~ ^[0-9]{1,5}$ ]] || { echo 'Некорректный порт.' >&2; exit 1; }
MANAGEMENT_PORT=$((10#$MANAGEMENT_PORT))
(( MANAGEMENT_PORT >= 1 && MANAGEMENT_PORT <= 65535 )) || { echo 'Порт должен быть от 1 до 65535.' >&2; exit 1; }

# Optional settings for installation behind an existing reverse proxy.
MANAGEMENT_BIND=${MANAGEMENT_BIND:-}
[[ -z $MANAGEMENT_BIND || $MANAGEMENT_BIND == 127.0.0.1 ]] || {
    echo 'MANAGEMENT_BIND: допустимо пустое значение или 127.0.0.1.' >&2; exit 1;
}
RESERVED_TCP_PORTS=${RESERVED_TCP_PORTS:-}
if [[ -n $RESERVED_TCP_PORTS ]]; then
    [[ $RESERVED_TCP_PORTS =~ ^[0-9]{1,5}(,[0-9]{1,5})*$ ]] || {
        echo 'RESERVED_TCP_PORTS: ожидаются порты через запятую.' >&2; exit 1;
    }
    IFS=, read -ra reserved_ports <<<"$RESERVED_TCP_PORTS"
    for port in "${reserved_ports[@]}"; do
        (( 10#$port >= 1 && 10#$port <= 65535 )) || {
            echo 'Зарезервированный порт должен быть от 1 до 65535.' >&2; exit 1;
        }
    done
fi

if $configure; then
    cd "$root/current"
    docker compose exec -T worker bash /app/gokaskad.sh --machine list >"$work/rules.json"
    python3 - "$work/rules.json" <<'CHECK'
import json, sys
rules = json.load(open(sys.argv[1]))['rules']
if any(r['protocol'] == 'tcp' and r['input_port'] in (80, 443) and r['enabled'] for r in rules):
    sys.exit('Сначала отключите правила перенаправления TCP 80/443 в панели.')
CHECK
    cp -p .env "$root/config/env.before-domain"
    python3 - <<'ENV'
from pathlib import Path
p = Path('.env')
lines = p.read_text().splitlines()
values = dict(line.split('=', 1) for line in lines if '=' in line and not line.startswith('#'))
reserved = sorted((set(values.get('RESERVED_TCP_PORTS', '').split(',')) | {'80', '443'}) - {''})
lines = [line for line in lines if not line.startswith(('MANAGEMENT_BIND=', 'RESERVED_TCP_PORTS='))]
p.write_text('\n'.join(lines) + '\nMANAGEMENT_BIND=127.0.0.1\nRESERVED_TCP_PORTS=' + ','.join(reserved) + '\n')
ENV
    docker compose up -d --wait --wait-timeout 180
    python3 "$helper" setup --plan "$work/proxy-plan.json" --domain "$PANEL_DOMAIN" --port "$MANAGEMENT_PORT"
    exit
fi
if [[ -z $(ss -H -ltn "sport = :$MANAGEMENT_PORT") ]]; then :; else
    echo "Порт $MANAGEMENT_PORT уже занят." >&2; exit 1
fi
if [[ ! -f $state/images.before ]]; then
    docker image ls -aq --no-trunc | sort -u >"$state/images.before"
fi
release=$(mktemp -d "$root/releases/install.XXXXXX")
cp -a "$work/source/." "$release/"
if ! $restore; then
    printf '%s\n' "$ADMIN_PASSWORD" | htpasswd -niBC 10 admin >"$root/config/admin.htpasswd"
    printf 'Authorization: Basic %s\n' "$(printf 'admin:%s' "$ADMIN_PASSWORD" | openssl base64 -A)" >"$work/auth.header"
    unset ADMIN_PASSWORD
    chmod 644 "$root/config/admin.htpasswd"
fi
# Include discovered IPv4 addresses so the generated certificate can be trusted explicitly.
san='DNS:localhost,IP:127.0.0.1'
while read -r address; do san+=",IP:$address"; done < <(ip -o -4 addr show scope global | awk '{split($4,a,"/"); print a[1]}')
if ! $restore || [[ ! -f $root/config/tls/server.crt ]]; then
    openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 365 \
        -keyout "$root/config/tls/server.key" -out "$root/config/tls/server.crt" \
        -subj '/CN=Gokaskad' -addext "subjectAltName=$san" >/dev/null 2>&1
fi
backend=legacy
if iptables --version | grep -q nf_tables; then backend=nft; fi
printf 'MANAGEMENT_PORT=%s\nCONFIG_DIR=%s/config\nIPTABLES_BACKEND=%s\nMANAGEMENT_BIND=%s\nRESERVED_TCP_PORTS=%s\n' "$MANAGEMENT_PORT" "$root" "$backend" "$MANAGEMENT_BIND" "$RESERVED_TCP_PORTS" >"$release/.env"
printf 'net.ipv4.ip_forward=1\n' > /etc/sysctl.d/90-gokaskad.conf
sysctl -w net.ipv4.ip_forward=1
cd "$release"
docker compose config --quiet
docker compose up -d --build --wait --wait-timeout 180
docker image ls -aq --no-trunc | sort -u >"$state/images.after"
comm -13 "$state/images.before" "$state/images.after" >"$state/new-images"
# A restored certificate may belong to the old domain; lifecycle verifies the new endpoint.
if ! $restore; then
# Verify the authenticated HTTPS path, including nginx -> API -> worker -> SQLite.
[[ $(curl --silent --retry 10 --retry-connrefused --retry-delay 1 --output /dev/null --write-out '%{http_code}' --cacert "$root/config/tls/server.crt" \
    "https://localhost:$MANAGEMENT_PORT/api/rules") == 401 ]] || {
    echo 'Проверка защиты паролем не прошла. Проверьте docker compose logs.' >&2; exit 1;
}
fi
if $restore; then
    docker compose exec -T api python3 -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8080/api/health").read().decode())' | grep -q '"ready": true'
else
    curl --fail --silent --show-error --cacert "$root/config/tls/server.crt" \
        --header "@$work/auth.header" "https://localhost:$MANAGEMENT_PORT/api/health" | grep -q '"ready": true'
fi
ln -s "$release" "$root/current"
if [[ ${GOKASKAD_STAGING:-} == 1 ]]; then
    echo 'Контейнеры подготовлены. Настраиваю выбранный режим.'
    exit
fi
if [[ $mode == proxy ]]; then
    if ! python3 "$helper" setup --plan "$work/proxy-plan.json" --domain "$PANEL_DOMAIN" --port "$MANAGEMENT_PORT" --auth-file "$work/auth.header"; then
        echo 'Панель установлена локально. Для продолжения выполните установщик с --resume-domain; пароль и БД сохранены.' >&2
        exit 1
    fi
    exit
fi
rm -f "$work/auth.header"
echo
if [[ $MANAGEMENT_BIND == 127.0.0.1 ]]; then
    echo "Готово. Панель доступна только локально: https://127.0.0.1:$MANAGEMENT_PORT, пользователь: admin."
    echo 'Подключите существующий reverse proxy для доступа через домен.'
else
    echo "Готово. Откройте https://<IP-сервера>:$MANAGEMENT_PORT, пользователь: admin."
fi
echo "Самоподписанный сертификат: $root/config/tls/server.crt"
openssl x509 -in "$root/config/tls/server.crt" -noout -fingerprint -sha256
echo "Сверьте отпечаток и добавьте сертификат в доверенные. Срок действия — 365 дней."
echo "Логи: cd $root/current && docker compose logs -f"
