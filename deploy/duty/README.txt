Дежурный Алапы (Адам 17.11): «＋ Новое приложение» в пульте → через минуту приложение клиента работает, без Альфреда.

Установка — один раз (Альфред, от root):
  install -m 0750 deploy/duty/alapa-duty.sh /usr/local/sbin/alapa-duty.sh
  cp deploy/duty/alapa-duty.path deploy/duty/alapa-duty.service /etc/systemd/system/
  mkdir -p /srv/alapa/data/_duty /etc/caddy/clients && chown alapa:alapa /srv/alapa/data/_duty
  в /etc/caddy/Caddyfile, ПОСЛЕ блока (secure), строка:   import /etc/caddy/clients/*.caddy
  caddy validate --config /etc/caddy/Caddyfile && systemctl reload caddy
  systemctl daemon-reload && systemctl enable --now alapa-duty.path

Обновление скрипта (после git): install -m 0750 deploy/duty/alapa-duty.sh /usr/local/sbin/alapa-duty.sh
Порты клиентов — 8301–8999 (18.11: 8102 был занят Botter). Дежурный проверяет, что порт свободен на сервере (ss), и что после
запуска его слушает именно служба клиента; иначе службу останавливает и пишет <код>.bad с причиной.

Проверка: записка data/_duty/<код>.new превращается в <код>.ok (или <код>.bad с причиной); журнал — journalctl -t alapa-duty.
Что дежурный НЕ делает: не трогает чужие службы и соседей, не удаляет ничего, не правит git (папку clients/<код>/ коммитит Альфред
при следующем обновлении; .env в git не попадает).
