#!/usr/bin/env bash
#
# Развёртывание «ПромВизора» на чистом Ubuntu-сервере БЕЗ Docker.
# Docker-вариант проще: docker compose up -d --build, и всё.
#
#   sudo bash deploy/install.sh
#
# Что делает скрипт:
#   1. Ставит системные пакеты (ffmpeg, nginx, python3-venv).
#   2. Создаёт пользователя promvizor без прав root.
#   3. Копирует проект в /opt/promvizor.
#   4. Создаёт venv и ставит зависимости.
#   5. Ставит и включает автозапуск systemd.
#
# Чего скрипт НЕ делает:
#   * не трогает nginx и не выпускает сертификат — сертификат
#     выпускается только для домена, который вы уже купили и
#     направили на этот IP. Выпуск сертификата на IP-адрес
#     Let's Encrypt не делает.
#   * не запускает сервисы: сначала заполните .env, потом
#     systemctl start. Запуск с пустым .env поднимет сервис,
#     который сразу начнёт писать в пустую базу.

set -euo pipefail

APP_USER="promvizor"
APP_DIR="/opt/promvizor"
DATA_DIR="${APP_DIR}/data"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Каталог проекта: ${SRC_DIR}"
echo "==> Куда ставим:     ${APP_DIR}"

# --------------------------------------------------------------------------- #
# 1. Системные пакеты
# --------------------------------------------------------------------------- #
echo
echo "==> Системные пакеты"

# ffmpeg обязателен: без него OpenCV не откроет RTSP (CAP_FFMPEG).
# Без него камера молча отдаёт пустой поток — самая неприятная
# ошибка при развёртывании, потому что в логах нет ничего.
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    ffmpeg nginx ca-certificates curl rsync

if ! command -v git >/dev/null 2>&1; then
    echo "    (git не найден — скопируем файлы как есть, без истории)"
fi

# --------------------------------------------------------------------------- #
# 2. Пользователь
# --------------------------------------------------------------------------- #
echo
echo "==> Пользователь ${APP_USER}"

if id -u "${APP_USER}" >/dev/null 2>&1; then
    echo "    уже существует"
else
    useradd --system --create-home --home-dir "/home/${APP_USER}" --shell /usr/sbin/nologin "${APP_USER}"
    echo "    создан"
fi

# --------------------------------------------------------------------------- #
# 3. Файлы проекта
# --------------------------------------------------------------------------- #
echo
echo "==> Копирование проекта"

mkdir -p "${APP_DIR}"

# rsync, а не cp -r: иначе в /opt попадут .venv с чужими DLL,
# локальная база promvizor.db и — главное — .env с токеном
# Telegram с чужой машины.
if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
        --exclude '.venv' --exclude 'venv' \
        --exclude '.env' \
        --exclude '__pycache__' --exclude '*.pyc' \
        --exclude '*.db' --exclude '*.db-wal' --exclude '*.db-shm' \
        --exclude 'camera_mode.json' --exclude 'telegram_cursor.json' \
        --exclude '*.pt' --exclude 'runs' \
        --exclude '.git' \
        "${SRC_DIR}/" "${APP_DIR}/"
else
    echo "    rsync недоступен, копирую вручную"
    tar -C "${SRC_DIR}" \
        --exclude='.venv' --exclude='.env' --exclude='__pycache__' \
        --exclude='*.db' --exclude='.git' \
        -cf - . | tar -C "${APP_DIR}" -xf -
fi

mkdir -p "${DATA_DIR}"

# data/demo -> ../demo. Нужно потому, что сервис работает из data
# (там база и camera_mode.json, а весь /opt после ProtectSystem=strict
# доступен только на чтение), а демо-ролики ищутся по относительному
# пути demo/videos от рабочего каталога. Без ссылки режим demo в
# systemd не нашёл бы роликов и показал бы «нет сигнала».
ln -sfn "${APP_DIR}/demo" "${DATA_DIR}/demo"

# Каталог для ultralytics (YOLO_CONFIG_DIR ниже). Создаётся заранее:
# под ProtectHome=true /home/promvizor недоступен, и если каталога нет,
# первая запись упала бы с ошибкой.
mkdir -p "${DATA_DIR}/.config/ultralytics"

chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
chmod 750 "${DATA_DIR}"

# --------------------------------------------------------------------------- #
# 4. .env
# --------------------------------------------------------------------------- #
echo
echo "==> Конфигурация"

if [ -f "${APP_DIR}/.env" ]; then
    echo "    ${APP_DIR}/.env уже есть — оставляю как есть"
else
    cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"

    # В шаблоне API_HOST=127.0.0.1: извне сервис не виден вовсе.
    # Для сервера нужно слушать все интерфейсы.
    sed -i 's/^API_HOST=.*/API_HOST=0.0.0.0/' "${APP_DIR}/.env"
    # База — в каталог данных, а не рядом с кодом: с ProtectSystem=strict
    # весь /opt/promvizor доступен только на чтение.
    sed -i 's|^DATABASE_PATH=.*|DATABASE_PATH='"${DATA_DIR}"'/promvizor.db|' "${APP_DIR}/.env"

    echo "    создан из .env.example"
fi

# Каталог ultralytics — дописывается и к только что созданному .env,
# и к существующему: сервис работает с ProtectHome=true, поэтому
# /home/promvizor недоступен и без этого пути первая же попытка
# скачать веса модели упала бы с ошибкой записи — включение детекции
# выглядело бы как «YOLO сломан». Путь зависит от APP_DIR, поэтому он
# пишется явно, а не раскомментированием строки в шаблоне.
if ! grep -q '^YOLO_CONFIG_DIR=' "${APP_DIR}/.env"; then
    echo "YOLO_CONFIG_DIR=${DATA_DIR}/.config/ultralytics" >> "${APP_DIR}/.env"
    echo "    добавлен YOLO_CONFIG_DIR=${DATA_DIR}/.config/ultralytics"
fi

chown "${APP_USER}:${APP_USER}" "${APP_DIR}/.env"
chmod 640 "${APP_DIR}/.env"

echo "    ПРОВЕРЬТЕ ${APP_DIR}/.env перед запуском:"
echo "      API_HOST=0.0.0.0          (уже проставлено)"
echo "      DATABASE_PATH=...         (уже проставлено)"
echo "      YOLO_CONFIG_DIR=...       (уже проставлено)"
echo "      RTSP_URL=...              адрес вашей камеры"
echo "      COST_PER_MINUTE=750       ваша стоимость минуты"
echo "      TELEGRAM_BOT_TOKEN=...    от @BotFather"

# --------------------------------------------------------------------------- #
# 5. Зависимости
# --------------------------------------------------------------------------- #
echo
echo "==> Зависимости (это займёт несколько минут)"

sudo -u "${APP_USER}" python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/pip" install --upgrade pip --quiet

# ultralytics тянет torch (~554 МБ). Без него детекция человека
# не работает, но видео, API и Dashboard — работают. Переменная
# WITH_YOLO=1 ставит его; по умолчанию ставим лёгкий набор, чтобы
# первый запуск на сервере с 1 ГБ RAM вообще состоялся.
if [ "${WITH_YOLO:-0}" = "1" ]; then
    "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt" --quiet
    echo "    установлено с YOLO"
else
    "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements-lite.txt" --quiet
    echo "    установлено без YOLO (переустановить с YOLO: WITH_YOLO=1)"
fi

# Бот — отдельный процесс, но пакеты живут в том же venv: это
# один проект, и две виртуальные среды разъедутся по версиям.
"${APP_DIR}/.venv/bin/pip" install "python-telegram-bot>=21.0" --quiet

chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}/.venv"

# --------------------------------------------------------------------------- #
# 6. systemd
# --------------------------------------------------------------------------- #
echo
echo "==> Автозапуск"

if ! command -v systemctl >/dev/null 2>&1; then
    echo "    systemd не найден — пропускаю"
    exit 0
fi

cp "${SRC_DIR}/deploy/systemd/promvizor-api.service" /etc/systemd/system/
cp "${SRC_DIR}/deploy/systemd/promvizor-telegram.service" /etc/systemd/system/

systemctl daemon-reload
systemctl enable promvizor-api.service promvizor-telegram.service

echo "    сервисы включены в автозапуск"

# --------------------------------------------------------------------------- #
# Готово
# --------------------------------------------------------------------------- #
cat <<EOF

Готово. Дальше:

  1) Проверьте конфигурацию:
       nano ${APP_DIR}/.env

  2) Запустите backend:
       systemctl start promvizor-api
       systemctl status promvizor-api
       journalctl -u promvizor-api -f

  3) Проверьте, что он отвечает:
       curl http://127.0.0.1:8000/api/health

  4) Наполните базу демо-данными (необязательно):
       curl -X POST "http://127.0.0.1:8000/api/demo/seed?days_back=7"

  5) Telegram-бот (после заполнения TELEGRAM_BOT_TOKEN):
       systemctl start promvizor-telegram

  6) Настройте nginx и сертификат:
       sudo cp ${APP_DIR}/deploy/nginx/promvizor.conf \\
         /etc/nginx/sites-available/promvizor
       sudo ln -s /etc/nginx/sites-available/promvizor \\
         /etc/nginx/sites-enabled/
       # в promvizor.conf замените promvizor.example.ru на свой домен
       sudo certbot --nginx -d ВАШ_ДОМЕН
       sudo nginx -t && sudo systemctl reload nginx

  До этого порта 8000 наружу не выставлен: слышен только на 127.0.0.1.

EOF