#!/usr/bin/env bash
# Обновить Алапу на сервере (Адам 20.11: «у всех клиентов один код»): код из main → все приложения перезапускаются → проверка.
# Запуск (root): bash /srv/alapa/deploy/update.sh
# Что делает: git из main, права alapa, перезапуск kpi-web@demo и каждого клиента из clients/<код>/ (у кого есть .env
# с WEB_PORT и включена служба), визитка в /var/www/kpi/site, проверка: каждый порт отвечает. Итог — последней строкой.
set -u
# git ниже может переписать этот файл, пока bash его читает, — работаем с копией
if [ -z "${ALAPA_UPD:-}" ]; then t=$(mktemp); cp "$0" "$t"; ALAPA_UPD=1 exec bash "$t" "$@"; fi
cd /srv/alapa || exit 1
git fetch -q origin && git reset -q --hard origin/main || { echo "ИТОГ: ❌ git не обновился"; exit 1; }
chown -R alapa:alapa /srv/alapa
cp -r web/site/. /var/www/kpi/site/
echo "Код: $(git log --oneline -1)"
bad=0
check() {  # $1 — код, $2 — порт
  for i in 1 2 3 4 5 6 7 8 9 10; do
    c=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$2/" || true)
    [ "$c" = "200" ] && { echo "✅ $1 (порт $2) — отвечает"; return 0; }
    sleep 1
  done
  echo "❌ $1 (порт $2) — не отвечает: $(systemctl is-active kpi-web@$1)"; bad=1
}
systemctl restart kpi-web@demo && check demo 8101
for d in clients/*/; do
  code=$(basename "$d")
  case "$code" in _*|demo|__pycache__) continue;; esac
  [ -f "$d/config.yaml" ] && [ -f "$d/.env" ] || continue
  port=$(grep -E '^WEB_PORT=' "$d/.env" | head -1 | cut -d= -f2 | tr -d '[:space:]')
  [ -n "$port" ] || { echo "⚠️ $code — нет WEB_PORT, пропускаю"; continue; }
  if ! systemctl is-enabled -q "kpi-web@$code" 2>/dev/null; then echo "⚠️ $code — служба не включена, пропускаю"; continue; fi
  systemctl restart "kpi-web@$code" && check "$code" "$port"
done
[ $bad = 0 ] && echo "ИТОГ: ✅ все приложения обновлены и отвечают" || echo "ИТОГ: ❌ есть неответившие — смотри строки выше"
exit $bad
