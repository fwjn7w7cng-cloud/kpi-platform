#!/bin/bash
# Копия всего важного на сервере раз в сутки (Адам 01.11): /var/backups/alapa/<дата>.tar.gz, хранится 14 дней.
# Базы — через sqlite3 .backup (копия целая даже во время записи), остальное — файлы, которых нет в git: секреты,
# состояние торгового бота, настройки служб, Caddy, fail2ban, ssh, crontab. Папка и файлы — только root (700/600).
set -uo pipefail
D=/var/backups/alapa; T=$(date +%F); W=$(mktemp -d)
mkdir -p "$D" && chmod 700 "$D"
db() { [ -f "$1" ] && sqlite3 "$1" ".backup '$W/$2'" ; }
db /srv/teplocom/data/pay.db quizbot-pay.db      # Теплоком: с 01.11 бот в /srv/teplocom (/root/quizbot — ссылка туда)
db /root/fitness-bot/data/fitness.db fitness.db
while IFS= read -r f; do
  db "$f" "alapa-$(echo "${f#/srv/alapa/data/}" | tr / _)"
done < <(find /srv/alapa/data -name '*.db' -not -path '*/guests/*' 2>/dev/null)
tar czf "$W/files.tgz" --ignore-failed-read --warning=no-file-changed \
  /srv/teplocom/.env /srv/teplocom/google_key.json /srv/teplocom/web/logo.png \
  /root/chogbot/.env /root/chogbot/state.json /root/chogbot/*.jsonl /root/chogbot/notify_details.json \
  /root/fitness-bot/.env \
  /srv/alapa/clients/*/.env \
  /etc/caddy/Caddyfile /etc/systemd/system/*.service /etc/systemd/system/*.service.d /etc/systemd/system/*.timer \
  /etc/fail2ban/jail.d /etc/fail2ban/filter.d/alapa-web.conf /etc/ssh/sshd_config.d /root/.ssh/authorized_keys \
  /var/spool/cron/crontabs/root /etc/apt/apt.conf.d/52alapa-reboot /usr/local/sbin/alapa-backup.sh 2>/dev/null
tar czf "$D/$T.tar.gz" -C "$W" . && chmod 600 "$D/$T.tar.gz"
rm -rf "$W"
find "$D" -name '*.tar.gz' -mtime +14 -delete
echo "копия $D/$T.tar.gz: $(du -h "$D/$T.tar.gz" | cut -f1)"
