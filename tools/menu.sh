#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
export -n ADMIN_PASSWORD
root=/opt/go-kaskad
source_dir=${GOKASKAD_SOURCE_DIR:?}
action=${GOKASKAD_ACTION:?}
ask() { local text=$1; local fallback=${2:-}; read -r -p "$text" answer </dev/tty; answer=${answer:-$fallback}; }
if [[ $action == manage ]]; then
    echo '1 — Обновить код, сохранить режим и настройки'
    echo '2 — Переустановить контейнеры, сохранить пароль и правила'
    echo '3 — Обновить и изменить режим / порт / тип сертификата'
    echo '0 — Выход'
    ask 'Выберите действие: '
    case $answer in 1) action=update;; 2) action=reinstall;; 3) action=change;; 0) exit;; *) exit 2;; esac
fi
if [[ $action == delete-menu ]]; then
    echo '1 — Удалить приложение, сохранить конфигурацию, сертификат и правила'
    echo '2 — Удалить полностью, включая данные и добавленные неиспользуемые пакеты'
    echo '0 — Выход'
    ask 'Выберите вариант: '
    case $answer in 1) action=remove;; 2) action=purge;; 0) exit;; *) exit 2;; esac
fi
if [[ $action == remove || $action == purge ]]; then
    if [[ ${GOKASKAD_CONFIRM:-} != YES ]]; then
        if [[ $action == purge ]]; then
            ask 'Будут удалены правила, пароль и файлы Kaskad. Введите DELETE: '
            [[ $answer == DELETE ]] || exit 1
        else
            ask 'Остановить и удалить приложение, сохранив настройки и правила? [y/N]: '
            [[ $answer == y || $answer == Y ]] || exit 1
        fi
    fi
    option=--keep-config
    [[ $action != purge ]] || option=--remove-packages
    bash "$source_dir/uninstall.sh" "$option" </dev/null
    exit
fi
if [[ $action == update || $action == reinstall ]]; then
    python3 "$source_dir/tools/lifecycle.py" "$action" --source "$source_dir"
    exit
fi
mode=${INSTALL_MODE:-auto}
if [[ $mode == auto ]]; then
    echo '1 — Отдельный HTTPS-порт'
    echo '2 — Домен на 443 через существующий nginx / SNI'
    ask 'Режим: '
    case $answer in 1) mode=standalone;; 2) mode=proxy;; *) exit 2;; esac
fi
certificate=${CERT_TYPE:-}
if [[ -z $certificate ]]; then
    echo '1 — Самоподписанный сертификат'
    echo '2 — Let’s Encrypt через Certbot + домен'
    ask 'Сертификат [2]: ' 2
    case $answer in 1) certificate=selfsigned;; 2) certificate=certbot;; *) exit 2;; esac
fi
[[ $mode == standalone || $mode == proxy ]] || exit 2
[[ $certificate == selfsigned || $certificate == certbot ]] || exit 2
domain=${PANEL_DOMAIN:-}
if [[ $mode == proxy || $certificate == certbot ]]; then
    if [[ -z $domain ]]; then ask 'Домен панели (DNS должен указывать на VPS): '; domain=$answer; fi
fi
port=${MANAGEMENT_PORT:-}
if [[ $mode == proxy ]]; then
    if [[ -z $port && -f $root/current/.env ]]; then port=$(awk -F= '$1=="MANAGEMENT_PORT" {print $2}' "$root/current/.env"); fi
    if [[ -z $port ]]; then
        for candidate in $(seq 10443 10542); do
            if [[ -z $(ss -H -ltn "sport = :$candidate") ]]; then port=$candidate; break; fi
        done
    fi
    [[ -n $port ]] || { echo 'Нет свободного локального порта.' >&2; exit 1; }
else
    default_port=8443
    [[ $certificate != certbot ]] || default_port=443
    if [[ -z $port ]]; then ask "HTTPS-порт [$default_port]: " "$default_port"; port=$answer; fi
fi
[[ $port =~ ^[0-9]{1,5}$ ]] || { echo 'Некорректный порт.' >&2; exit 1; }
port=$((10#$port))
((port>=1 && port<=65535)) || exit 1
previous_port=''
if [[ -f $root/current/.env ]]; then previous_port=$(awk -F= '$1=="MANAGEMENT_PORT" {print $2}' "$root/current/.env"); fi
if [[ $port != "$previous_port" && -n $(ss -H -ltn "sport = :$port") ]]; then
    echo "TCP $port занят. Выберите другой порт или режим SNI (пункт 2)." >&2
    exit 1
fi
if [[ $mode == standalone && $certificate == certbot && -n $(ss -H -ltn 'sport = :80') ]]; then
    echo 'Для Certbot standalone нужен свободный TCP 80. Для существующего nginx выберите SNI.' >&2
    exit 1
fi
export MANAGEMENT_PORT=$port
if [[ $action == install && -f $root/saved/rules.sqlite3 ]] && type -P python3 >/dev/null; then
    python3 - "$root/saved/rules.sqlite3" "$mode" "$port" <<'RULES'
import json,sqlite3,sys
connection=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True)
for row in connection.execute('SELECT data FROM rules'):
    rule=json.loads(row[0])
    if rule['enabled'] and (rule['input_port']==int(sys.argv[3]) or (sys.argv[2]=='proxy' and rule['protocol']=='tcp' and rule['input_port'] in (80,443))):
        sys.exit('Сохранённые правила конфликтуют с портом панели / SNI. Восстановите прежний режим и отключите конфликтующее правило.')
RULES
fi
if [[ $mode == proxy ]] && type -P python3 >/dev/null; then
    discovered=$(python3 "$source_dir/tools/proxy_setup.py" discover)
    [[ $discovered != standalone ]] || { echo 'Для SNI нужен существующий nginx на 80/443.' >&2; exit 1; }
fi
if [[ $action == change ]]; then
    # A legacy installation is upgraded automatically before a mode change.
    python3 "$source_dir/tools/lifecycle.py" update --source "$source_dir"
else
    bind=''
    [[ $mode != proxy ]] || bind=127.0.0.1
    GOKASKAD_STAGING=1 INSTALL_MODE=standalone MANAGEMENT_BIND="$bind" bash "$source_dir/tools/install_core.sh" </dev/null
fi
python3 "$source_dir/tools/lifecycle.py" change --mode "$mode" --certificate "$certificate" --domain "$domain" --port "$port"
