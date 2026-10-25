#!/usr/bin/env bash
# Integrate the existing user service with the BumbleRig gateway.
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "$EUID" -ne 0 ]]; then
    if [[ -n "${WSL_DISTRO_NAME:-}" ]]; then
        exec /mnt/c/Windows/System32/wsl.exe --distribution "$WSL_DISTRO_NAME" --user root \
            --exec /bin/bash "$project_dir/scripts/deploy-lan.sh"
    fi
    exec sudo /bin/bash "$project_dir/scripts/deploy-lan.sh"
fi

app_user="$(stat -c %U "$project_dir")"
[[ "$app_user" != root ]] || { echo 'Il checkout deve appartenere a un utente senza privilegi.' >&2; exit 1; }
app_home="$(getent passwd "$app_user" | cut -d: -f6)"
app_uid="$(id -u "$app_user")"
user_systemctl() {
    runuser -u "$app_user" -- env XDG_RUNTIME_DIR="/run/user/$app_uid" systemctl --user "$@"
}
[[ -f /etc/bumblerig/apps.json && -f /etc/bumblerig/pki/root-ca.crt ]] || {
    echo 'Installa prima il gateway BumbleRig LAN.' >&2; exit 1;
}
user_systemctl is-active --quiet launchpad.service || {
    echo 'Installa e avvia prima il servizio utente launchpad.service.' >&2; exit 1;
}
stamp="$(date -u +%Y%m%dT%H%M%SZ)-$$"
backup="/var/backups/bumblerig/launchpad-$stamp"
snippet=/etc/nginx/bumblerig-apps/launchpad.conf
dropin_dir="$app_home/.config/systemd/user/launchpad.service.d"
dropin="$dropin_dir/bumblerig.conf"
install -d -m 700 "$backup"
cp -a /etc/bumblerig/apps.json "$backup/apps.json"
[[ ! -f "$snippet" ]] || cp -a "$snippet" "$backup/nginx.conf"
[[ ! -f "$dropin" ]] || cp -a "$dropin" "$backup/systemd.conf"
rollback() {
    status=$?
    trap - ERR
    echo "Deploy fallito: ripristino configurazione. Backup: $backup" >&2
    if [[ -f "$backup/nginx.conf" ]]; then cp -a "$backup/nginx.conf" "$snippet"; else rm -f "$snippet"; fi
    if [[ -f "$backup/systemd.conf" ]]; then cp -a "$backup/systemd.conf" "$dropin"; else rm -f "$dropin"; fi
    cp -a "$backup/apps.json" /etc/bumblerig/apps.json
    user_systemctl daemon-reload
    user_systemctl restart launchpad.service || true
    nginx -t && systemctl reload nginx || true
    exit "$status"
}
trap rollback ERR
install -d -m 755 -o "$app_user" -g "$(id -gn "$app_user")" "$dropin_dir"
install -m 644 -o "$app_user" -g "$(id -gn "$app_user")" "$project_dir/systemd/bumblerig.conf" "$dropin"
install -m 644 "$project_dir/deploy/launchpad.conf" "$snippet"
user_systemctl daemon-reload
user_systemctl restart launchpad.service
curl --silent --show-error --fail --retry 8 --retry-connrefused --retry-delay 1 --max-time 5 \
    http://127.0.0.1:7777/api/health >/dev/null
nginx -t
systemctl reload nginx
for path in /launchpad/ /launchpad/app.js /launchpad/style.css /launchpad/favicon.svg /launchpad/api/health /launchpad/api/tmux; do
    echo "Collaudo HTTPS: $path"
    curl --silent --show-error --fail --retry 4 --retry-all-errors --retry-delay 1 --max-time 5 \
        --cacert /etc/bumblerig/pki/root-ca.crt \
        --connect-to bumblerig.home.arpa:443:127.0.0.1:8443 "https://bumblerig.home.arpa$path" >/dev/null
done
python3 - /etc/bumblerig/apps.json <<'PY'
import json, os, sys, tempfile
path = sys.argv[1]
with open(path) as source:
    apps = json.load(source)
app = {'id': 'launchpad', 'name': 'Launchpad', 'category': 'La regia dei tuoi servizi',
       'description': 'Servizi, porte e container a colpo d’occhio. Controlla le sessioni tmux e avvia i tuoi progetti.',
       'tags': ['Servizi', 'Docker', 'Sessioni tmux'], 'href': '/launchpad/', 'enabled': True}
existing = next((i for i, value in enumerate(apps) if value['id'] == app['id']), None)
if existing is None:
    apps.append(app)
else:
    apps[existing] = {**apps[existing], **app}
fd, temporary = tempfile.mkstemp(dir=os.path.dirname(path), prefix='.apps-')
with os.fdopen(fd, 'w') as output:
    json.dump(apps, output, ensure_ascii=False, indent=2)
    output.write('\n')
os.chmod(temporary, 0o644)
os.replace(temporary, path)
PY
trap - ERR
echo 'Launchpad disponibile: https://bumblerig.home.arpa/launchpad/'
echo "Backup configurazione precedente: $backup"
