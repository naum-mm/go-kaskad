#!/usr/bin/env bash
# Remove Gokaskad resources only. Optional package removal requires an install manifest.
set -Eeuo pipefail
# bash may be reading this script from curl or a heredoc. Docker must not
# consume the remaining installer commands as its interactive stdin.
docker() { command docker "$@" </dev/null; }
[[ $EUID -eq 0 ]] || { echo 'Запустите через sudo bash. / Run with sudo bash.' >&2; exit 1; }
purge=false
case ${1:-} in
    '') ;;
    --remove-packages) purge=true ;;
    *) echo 'Usage: sudo bash uninstall.sh [--remove-packages]' >&2; exit 2 ;;
esac
root=/opt/go-kaskad
[[ -d $root && ! -L $root ]] || { echo 'Установка /opt/go-kaskad не найдена.' >&2; exit 1; }
exec 9>"$root/install.lock"
flock -n 9 || { echo 'Установка/удаление уже выполняется.' >&2; exit 1; }
release=$(readlink -f "$root/current")
[[ $release == "$root/releases/"* && -f $release/compose.yaml && -f $release/.env ]] || {
    echo 'Не найден current/compose.yaml; файлы не удалены.' >&2; exit 1;
}
state="$root/install-state"
if $purge && [[ ! -f $state/new-packages ]]; then
    echo 'У этой старой установки нет списка установленных пакетов. Автоматическое удаление пакетов невозможно.' >&2
    echo 'Запустите без --remove-packages для удаления приложения; общие системные пакеты будут сохранены.' >&2
    exit 1
fi
cd "$release"
# Detach our domain before stopping the panel. Existing sites, hooks and networks stay.
if [[ -f $root/config/proxy.json ]]; then
    python3 "$root/config/proxy_setup.py" remove
fi
# Stop all writers before detaching rules or deleting the database.
docker compose stop
backend=$(awk -F= '$1=="IPTABLES_BACKEND" {print $2}' .env)
case $backend in
    nft|legacy) firewall="iptables-$backend" ;;
    '') firewall=iptables ;;
    *) echo 'Неизвестный backend iptables; удаление остановлено.' >&2; exit 1 ;;
esac
command -v "$firewall" >/dev/null
for table in nat filter; do
    "$firewall" -w 5 -t "$table" -S >/dev/null
done
for spec in 'nat PREROUTING GK_DNAT' 'nat POSTROUTING GK_SNAT' 'filter FORWARD GK_FORWARD'; do
    read -r table parent chain <<<"$spec"
    while "$firewall" -w 5 -t "$table" -C "$parent" -j "$chain" 2>/dev/null; do
        "$firewall" -w 5 -t "$table" -D "$parent" -j "$chain"
    done
    if "$firewall" -w 5 -t "$table" -S "$chain" >/dev/null 2>&1; then
        "$firewall" -w 5 -t "$table" -F "$chain"
        "$firewall" -w 5 -t "$table" -X "$chain"
    fi
done
docker compose down --volumes --rmi local --remove-orphans
remaining_containers=$(docker ps -aq)
# Remove only images absent before installation, and never force shared images out.
if [[ -f $state/new-images ]]; then
    while read -r image; do
        [[ $image =~ ^sha256:[0-9a-f]{64}$ ]] || continue
        docker image rm "$image" >/dev/null 2>&1 || true
    done <"$state/new-images"
fi
if [[ -f $state/sysctl.existed ]]; then
    cp -p "$state/sysctl.previous" /etc/sysctl.d/90-gokaskad.conf
else
    rm -f /etc/sysctl.d/90-gokaskad.conf
fi

# Preserve the manifest if package cleanup is blocked or interrupted.
if $purge; then
    # System packages and Docker may now be used by other applications.
    if [[ -n $remaining_containers || -n $(docker volume ls -q) || -n $(docker image ls -aq) ||
          -n $(docker network ls --filter type=custom -q) ]]; then
        echo 'Остались другие ресурсы Docker. Пакеты и install-state сохранены.' >&2
        echo 'Удалите общие пакеты вручную после проверки; не удаляйте /var/lib/docker вслепую.' >&2
        exit 1
    fi
    if compgen -G '/etc/letsencrypt/renewal/*.conf' >/dev/null; then
        echo 'Остались другие сертификаты Let’s Encrypt. Пакеты и install-state сохранены для их продления.' >&2
        exit 1
    fi
    mapfile -t packages <"$state/new-packages"
    if ((${#packages[@]})); then
        for package in "${packages[@]}"; do
            [[ $package =~ ^[a-z0-9][a-z0-9+.-]*(:[a-z0-9]+)?$ ]] || exit 1
        done
        # Refuse to remove anything that was not recorded as newly installed.
        plan=$(apt-get -s purge "${packages[@]}")
        native_arch=$(dpkg --print-architecture)
        while read -r package; do
            if ! grep -Fxq "$package" "$state/new-packages" &&
               ! { [[ $package != *:* ]] && grep -Fxq "$package:$native_arch" "$state/new-packages"; }; then
                echo "Пакет $package используется вне проекта. Удаление пакетов остановлено." >&2; exit 1;
            fi
        done < <(awk '$1=="Remv" || $1=="Purg" {print $2}' <<<"$plan")
        DEBIAN_FRONTEND=noninteractive apt-get purge -y "${packages[@]}"
    fi
fi
if [[ -f $state/ip-forward.before ]]; then
    # Restore the runtime setting only when no other Docker containers need it.
    if [[ -z $remaining_containers ]]; then
        previous=$(cat "$state/ip-forward.before")
        [[ $previous == 0 || $previous == 1 ]] && sysctl -w "net.ipv4.ip_forward=$previous"
    else
        echo 'IP forwarding сохранён для других контейнеров Docker.'
    fi
else
    echo 'Старый установщик не сохранял прежнее значение IP forwarding; оно оставлено без изменений.'
fi
cd /
rm -rf -- "$root"
echo 'Gokaskad удалён: контейнеры, volume с БД, сети, образы приложения, правила и файлы.'
if ! $purge; then
    echo 'Общие системные пакеты и Docker сохранены. Режим --remove-packages выбирается вместо обычного удаления.'
fi
