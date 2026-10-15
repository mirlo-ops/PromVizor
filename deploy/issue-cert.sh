#!/usr/bin/env bash
# Выпуск сертификата и включение боевого конфига nginx.
#
# Зачем он отдельным скриптом: Let's Encrypt ограничивает 50 сертификатов
# на зарегистрированный домен в неделю, а бесплатные поддомены вроде
# *.fvds.ru выдаются многим людям с одного домена fvds.ru. Когда лимит
# выбран, certbot падает и говорит, до какого времени ждать. Скрипт
# запускается по cron и повторяет попытку сам.
set -uo pipefail

DOMAIN="${PV_DOMAIN:-akylovlev11.fvds.ru}"
CONF_SRC="/opt/promvizor/deploy/nginx/promvizor.conf"
CONF_DST="/etc/nginx/sites-available/promvizor"

# Кадры с камеры браузера не проходят через общий client_max_body_size,
# поэтому для них в конфиге отдельный location.
build_conf() {
    sed -e "s/promvizor\.example\.ru/${DOMAIN}/g" \
        -e '/^[[:space:]]*http2 on;/d' \
        -e 's/listen 443 ssl;/listen 443 ssl http2;/' \
        -e 's/listen \[::\]:443 ssl;/listen [::]:443 ssl http2;/' \
        "${CONF_SRC}" > "${CONF_DST}"
}

# Временный конфиг: только HTTP. Без него nginx не поднимется, если
# сертификата ещё нет, а в боевом конфиге на него стоит ссылка.
build_http_conf() {
    cat > "${CONF_DST}" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    location /.well-known/acme-challenge/ { root /var/www/html; }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
}

ln -sfn "${CONF_DST}" /etc/nginx/sites-enabled/promvizor

if [ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]; then
    echo "сертификат уже есть — применяем боевой конфиг"
    build_conf
    nginx -t && systemctl reload nginx
    exit 0
fi

echo "сертификата нет — пробуем выпустить"
build_http_conf
nginx -t && systemctl reload nginx

if certbot --nginx -d "${DOMAIN}" --redirect --agree-tos \
           --register-unsafely-without-email --non-interactive; then
    echo "сертификат выпущен — применяем боевой конфиг"
    build_conf
    nginx -t && systemctl reload nginx
    exit 0
fi

echo "certbot не смог (лимит или сеть) — nginx остался на HTTP, повторим позже"
exit 1