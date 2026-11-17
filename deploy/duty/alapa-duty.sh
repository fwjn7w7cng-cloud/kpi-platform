#!/bin/bash
# Дежурный (Адам 17.11: «создавать приложения прямо из пульта»). Пульт (core/pult.py, «＋ Новое приложение») создаёт папку
# clients/<код>/ и кладёт записку /srv/alapa/data/_duty/<код>.new. Дежурный (запускается сам, когда появилась записка):
#   проверяет код, порт и адрес → права alapa → служба kpi-web@<код> → адрес в Caddy (/etc/caddy/clients/<код>.caddy)
#   → fail2ban следит и за этой службой → записка становится <код>.ok (или <код>.bad с причиной — пульт её покажет).
# Установка (один раз, Альфред): см. deploy/duty/README.txt.
set -u
ROOT=/srv/alapa
DIR=$ROOT/data/_duty
CADDY=/etc/caddy/clients
JAIL=/etc/fail2ban/jail.d/alapa-web.local
shopt -s nullglob
for f in "$DIR"/*.new; do
  code=$(basename "$f" .new)
  fail() { echo "$1" > "$DIR/$code.bad"; rm -f "$f"; logger -t alapa-duty "$code: $1"; }
  [[ "$code" =~ ^[a-z0-9_]{2,40}$ ]] || { fail "плохой код клиента"; continue; }
  envf=$ROOT/clients/$code/.env
  [[ -f "$envf" && -f "$ROOT/clients/$code/config.yaml" ]] || { fail "нет папки клиента"; continue; }
  port=$(grep -E '^WEB_PORT=' "$envf" | head -1 | cut -d= -f2 | tr -d '[:space:]"')
  url=$(grep -E '^WEB_URL=' "$envf" | head -1 | cut -d= -f2 | tr -d '[:space:]"')
  host=${url#https://}
  [[ "$port" =~ ^[0-9]+$ ]] && (( port >= 8102 && port <= 8999 )) || { fail "порт «$port» вне 8102–8999"; continue; }
  [[ "$host" =~ ^[a-z0-9-]+\.alapa\.pro$ ]] || { fail "адрес «$host» не *.alapa.pro"; continue; }
  if grep -lE "^WEB_PORT=$port\s*$" "$ROOT"/clients/*/.env 2>/dev/null | grep -v "/clients/$code/.env" | grep -q .; then
    fail "порт $port уже у другого клиента"; continue
  fi
  chown -R alapa:alapa "$ROOT/clients/$code"
  systemctl enable --now "kpi-web@$code" || { fail "служба kpi-web@$code не запустилась"; continue; }
  mkdir -p "$CADDY"
  cat > "$CADDY/$code.caddy" <<EOF
$host {
	import secure
	request_body {
		max_size 24MB
	}
	reverse_proxy 127.0.0.1:$port
}
EOF
  if ! caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
    rm -f "$CADDY/$code.caddy"; fail "Caddy не принял адрес $host"; continue
  fi
  systemctl reload caddy
  units=$(systemctl list-units --all --plain --no-legend 'kpi-web@*.service' | awk '{print $1}')
  jm=$(for u in $units; do printf '_SYSTEMD_UNIT=%s + ' "$u"; done)"_SYSTEMD_UNIT=quizbot.service"
  [[ -f "$JAIL" ]] && sed -i "s|^journalmatch = .*|journalmatch = $jm|" "$JAIL" && systemctl reload fail2ban
  sleep 3
  http=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/" || true)
  mv "$f" "$DIR/$code.ok"
  echo "$(date '+%F %T') запущено: $host → 127.0.0.1:$port, ответ $http" >> "$DIR/$code.ok"
  logger -t alapa-duty "$code: запущено, $host → $port, ответ $http"
done
chown -R alapa:alapa "$DIR" 2>/dev/null || true
