#!/usr/bin/env bash
# Run on the Linux server that will forward traffic. Inputs are read from /dev/tty.
set -Eeuo pipefail
umask 077
[[ $EUID -eq 0 ]] || { echo 'Запустите установщик через sudo bash.' >&2; exit 1; }
[[ $(uname -s) == Linux ]] || { echo 'Нужен Linux с Docker и iptables.' >&2; exit 1; }
command -v apt-get >/dev/null || { echo 'Автоустановка поддерживает Debian и Ubuntu.' >&2; exit 1; }

root=/opt/go-kaskad
[[ ! -L $root ]] || { echo 'Каталог установки не должен быть симлинком.' >&2; exit 1; }
install -d -m 700 "$root" "$root/config" "$root/config/tls" "$root/releases"
exec 9>"$root/install.lock"
flock -n 9 || { echo 'Установка/удаление уже выполняется.' >&2; exit 1; }
[[ ! -e $root/current ]] || { echo "Установка уже существует: $root/current. Для обновления см. README." >&2; exit 1; }
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

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl openssl iproute2 iptables apache2-utils util-linux
if ! command -v docker >/dev/null; then
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
dpkg-query -W -f='${binary:Package}\t${db:Status-Status}\n' | awk '$2=="installed" {print $1}' | sort -u >"$state/packages.after"
comm -13 "$state/packages.before" "$state/packages.after" >"$state/new-packages"
if [[ ! -f $state/images.before ]]; then
    docker image ls -aq --no-trunc | sort -u >"$state/images.before"
fi
if ss -H -ltn "sport = :$MANAGEMENT_PORT" | grep -q .; then
    echo "Порт $MANAGEMENT_PORT уже занят." >&2; exit 1
fi
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
curl --fail --show-error --silent --location --proto '=https' --tlsv1.2 \
    https://codeload.github.com/naum-mm/go-kaskad/tar.gz/refs/heads/main -o "$work/source.tar.gz"
mkdir "$work/source"
tar -xzf "$work/source.tar.gz" --strip-components=1 -C "$work/source"
[[ -f $work/source/compose.yaml && -f $work/source/web/worker/firewall.py ]] || {
    echo 'В main отсутствует веб-версия. Сначала опубликуйте изменения в GitHub.' >&2; exit 1;
}
release="$root/releases/$(date -u +%Y%m%dT%H%M%SZ)"
mv "$work/source" "$release"
printf '%s\n' "$ADMIN_PASSWORD" | htpasswd -niBC 10 admin >"$root/config/admin.htpasswd"
printf 'Authorization: Basic %s\n' "$(printf 'admin:%s' "$ADMIN_PASSWORD" | openssl base64 -A)" >"$work/auth.header"
unset ADMIN_PASSWORD
chmod 644 "$root/config/admin.htpasswd"
# Include discovered IPv4 addresses so the generated certificate can be trusted explicitly.
san='DNS:localhost,IP:127.0.0.1'
while read -r address; do san+=",IP:$address"; done < <(ip -o -4 addr show scope global | awk '{split($4,a,"/"); print a[1]}')
openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 365 \
    -keyout "$root/config/tls/server.key" -out "$root/config/tls/server.crt" \
    -subj '/CN=Gokaskad' -addext "subjectAltName=$san" >/dev/null 2>&1
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
# Verify the authenticated HTTPS path, including nginx -> API -> worker -> SQLite.
[[ $(curl --silent --retry 10 --retry-connrefused --retry-delay 1 --output /dev/null --write-out '%{http_code}' --cacert "$root/config/tls/server.crt" \
    "https://localhost:$MANAGEMENT_PORT/api/rules") == 401 ]] || {
    echo 'Проверка защиты паролем не прошла. Проверьте docker compose logs.' >&2; exit 1;
}
curl --fail --silent --show-error --cacert "$root/config/tls/server.crt" \
    --header "@$work/auth.header" "https://localhost:$MANAGEMENT_PORT/api/health" | grep -q '"ready": true'
rm -f "$work/auth.header"
ln -s "$release" "$root/current"
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
