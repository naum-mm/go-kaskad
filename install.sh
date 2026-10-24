#!/usr/bin/env bash
# One entry point for installation, upgrades, mode changes and removal.
set -Eeuo pipefail
umask 077
export -n ADMIN_PASSWORD
[[ $EUID -eq 0 ]] || { echo 'Запустите через sudo bash.' >&2; exit 1; }
[[ $(uname -s) == Linux ]] || { echo 'Поддерживается Linux.' >&2; exit 1; }
type -P apt-get >/dev/null || { echo 'Поддерживаются Debian/Ubuntu.' >&2; exit 1; }
# The shell reads this file from curl. Never attach child processes to its stdin.
docker() { command docker "$@" </dev/null; }
root=/opt/go-kaskad
legacy=false
case ${1:-} in
    --configure-domain|--resume-domain) legacy=true ;;
    --update) GOKASKAD_ACTION=update ;;
    --reinstall) GOKASKAD_ACTION=reinstall ;;
    --change-mode) GOKASKAD_ACTION=change ;;
    --keep-config) GOKASKAD_ACTION=remove ;;
    --purge) GOKASKAD_ACTION=purge ;;
    --menu) unset GOKASKAD_ACTION ;;
    '') ;;
    *) echo 'Usage: install.sh [--menu|--update|--reinstall|--change-mode|--keep-config|--purge|--configure-domain|--resume-domain]' >&2; exit 2 ;;
esac
# Backward compatible unattended installation commands retain their behavior.
if [[ ${1:-} != --menu && -z ${GOKASKAD_ACTION:-} ]] && { [[ -n ${ADMIN_PASSWORD:-} ]] || [[ ${MANAGEMENT_BIND:-} == 127.0.0.1 && -n ${MANAGEMENT_PORT:-} ]]; }; then legacy=true; fi
if $legacy && [[ ${1:-} == --configure-domain ]]; then
    [[ -f $root/current/.env ]] || { echo 'Сначала установите Gokaskad.' >&2; exit 1; }
    grep -q RESERVED_TCP_PORTS "$root/current/compose.yaml" || {
        echo 'Сначала обновите Gokaskad: запустите меню и выберите пункт 3.' >&2; exit 1;
    }
fi
if ! $legacy && [[ -z ${GOKASKAD_ACTION:-} ]]; then
    echo 'Gokaskad — установка и управление / Installation & management'
    if [[ -f $root/current/.env ]]; then
        echo "Установка найдена: $(readlink -f "$root/current")"
        awk -F= '$1=="MANAGEMENT_PORT" {print "Порт панели: " $2} $1=="MANAGEMENT_BIND" {print "Привязка: " ($2==""?"внешняя":$2)}' "$root/current/.env"
    elif [[ -f $root/saved/env ]]; then
        echo 'Найдены сохранённые конфигурация и правила; они будут восстановлены при установке.'
    else
        echo 'Gokaskad не установлен.'
    fi
    metadata="$root/config/installation.json"
    [[ -f $metadata ]] || metadata="$root/config/proxy.json"
    if [[ -f $metadata ]] && type -P python3 >/dev/null; then
        python3 - "$metadata" <<'INFO'
import json,sys
try:
    data=json.load(open(sys.argv[1]))
    mode=data.get('mode',data.get('kind','—'))
    print('Режим:',{'proxy':'SNI через существующий nginx','docker':'SNI через nginx в Docker','host':'SNI через nginx хоста','direct':'отдельный порт','standalone':'отдельный порт'}.get(mode,mode))
    certificate=data.get('certificate',data.get('certificate_type','certbot'))
    print('Сертификат:',{'selfsigned':'самоподписанный','certbot':'Let’s Encrypt / Certbot'}.get(certificate,certificate))
    print('Домен:',data.get('domain') or '—')
except (OSError,ValueError): print('Метаданные требуют проверки; файлы не изменены.')
INFO
    fi
    echo
    echo 'Слушающие TCP/UDP-порты и процессы:'
    if type -P ss >/dev/null; then command ss -lntup </dev/null; else echo 'ss не установлен'; fi
    if type -P docker >/dev/null; then
        echo 'Контейнеры Docker:'
        docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}' || true
    fi
    if type -P systemctl >/dev/null; then
        echo 'Веб-сервисы на хосте:'
        command systemctl --no-pager --plain --type=service --state=running </dev/null | awk '/nginx|apache|caddy|haproxy|certbot/ {print}' || true
    fi
    echo
    echo '1 — Отдельная установка на выбранном порту'
    echo '2 — Домен на 443 через существующий nginx / SNI'
    echo '3 — Обновление, переустановка или смена режима'
    echo '4 — Удаление: сохранить конфигурацию или удалить полностью'
    echo '0 — Выход'
    read -r -p 'Выберите действие: ' choice </dev/tty
    case $choice in
        1) GOKASKAD_ACTION=install; INSTALL_MODE=standalone ;;
        2) GOKASKAD_ACTION=install; INSTALL_MODE=proxy ;;
        3) GOKASKAD_ACTION=manage ;;
        4) GOKASKAD_ACTION=delete-menu ;;
        0) exit ;;
        *) echo 'Неизвестный пункт.' >&2; exit 2 ;;
    esac
fi
if ! $legacy; then
    case ${GOKASKAD_ACTION:-} in
        install)
            [[ ! -e $root/current ]] || { echo 'Установка уже существует. Выберите пункт 3 для обновления или смены режима.' >&2; exit 1; } ;;
        manage|update|reinstall|change)
            [[ -f $root/current/.env ]] || { echo 'Работающая установка не найдена.' >&2; exit 1; } ;;
        delete-menu|remove|purge)
            [[ -d $root ]] || { echo 'Gokaskad не установлен.' >&2; exit 1; } ;;
        *) echo 'Неизвестное действие.' >&2; exit 2 ;;
    esac
fi
export GOKASKAD_ACTION=${GOKASKAD_ACTION:-} INSTALL_MODE=${INSTALL_MODE:-auto}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
curl --proto '=https' --tlsv1.2 -fsSL https://codeload.github.com/naum-mm/go-kaskad/tar.gz/refs/heads/main -o "$work/source.tar.gz" </dev/null
mkdir "$work/source"
tar -xzf "$work/source.tar.gz" --strip-components=1 -C "$work/source"
if [[ -n ${ADMIN_PASSWORD:-} ]]; then
    printf '%s' "$ADMIN_PASSWORD" >"$work/admin-password"
    export GOKASKAD_PASSWORD_FILE="$work/admin-password"
    unset ADMIN_PASSWORD
fi
if $legacy; then
    GOKASKAD_SOURCE_DIR="$work/source" bash "$work/source/tools/install_core.sh" "$@" </dev/null
else
    GOKASKAD_SOURCE_DIR="$work/source" bash "$work/source/tools/menu.sh" </dev/null
fi
