# ПромВизор — образ backend'а (API + Dashboard + конвейер видео).
#
# Сборка:
#   docker build -t promvizor:0.1 .
#   docker build --build-arg INSTALL_YOLO=1 -t promvizor:0.1-yolo .
#
# INSTALL_YOLO=0 (по умолчанию) — без ultralytics: образ ~450 МБ,
# хватает для RTSP-конвейера без детекции человека, для /docs и тестов.
# INSTALL_YOLO=1 — с YOLO: образ ~2.5 ГБ, нужна детекция человека.
#
# Важно про число воркеров: конвейер видео живёт В ПРОЦЕССЕ (app/api/runtime.py),
# поэтому uvicorn обязан стартовать с одним воркером. При двух воркерах камера
# откроется дважды, второму потоку она не достанется, а в базу полезут дубли.

FROM python:3.11-slim-bookworm

# 1 = ставить ultralytics (тянет torch, ~554 МБ). Совпадает с ключом -Light
# у run.ps1: без него детектор человека просто не поднимется.
ARG INSTALL_YOLO=0

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg обязателен: OpenCV открывает RTSP через бэкенд FFMPEG
# (app/video/rtsp.py → cv2.CAP_FFMPEG). Без него камера отдаёт пустой поток
# без внятной ошибки. curl — для healthcheck и разовой диагностики.
# libgl1/libglib не нужны: стоит opencv-python-headless, он без GUI.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/promvizor

# requirements.txt — источник правды для разработки и для образов с YOLO.
# requirements-lite.txt повторяет его без ultralytics; править оба.
COPY requirements.txt requirements-lite.txt ./
RUN if [ "$INSTALL_YOLO" = "1" ]; then \
        pip install -r requirements.txt; \
    else \
        pip install -r requirements-lite.txt; \
    fi

COPY app ./app
COPY web ./web
COPY demo ./demo

# Данные — в отдельный каталог, который монтируется томом: туда
# попадают относительные пути promvizor.db и camera_mode.json, а с
# YOLO_CONFIG_DIR туда же кладётся рабочий каталог ultralytics —
# иначе веса модели перекачивались бы при каждом пересоздании
# контейнера (файл .pt весит десятки мегабайт).
#
# Ссылка demo нужна потому, что рабочим каталогом станет
# /var/lib/promvizor (там база), а ролики ищутся по относительному
# пути demo/videos. Docker переносит содержимое точки монтирования
# в пустой том при первом старте, ссылка при этом сохраняется.
# Если том уже существовал (например, создан более ранней версией
# Dockerfile), ссылка может отсутствовать — режим demo покажет «нет
# сигнала»; проверяется командой:
#   docker compose exec api ls -l /var/lib/promvizor/demo
RUN useradd --system --uid 10001 --create-home --home-dir /home/promvizor promvizor \
    && mkdir -p /var/lib/promvizor \
    && ln -s /opt/promvizor/demo /var/lib/promvizor/demo \
    && chown -R promvizor:promvizor /var/lib/promvizor /opt/promvizor

# PYTHONPATH обязателен: рабочий каталог ниже — /var/lib/promvizor (там
# база), а код лежит в /opt/promvizor. `python -m uvicorn app.api.main:app`
# ищет пакет app в текущем каталоге, его там нет, и контейнер падает с
# ModuleNotFoundError, перезапускаясь по кругу.
#
# 127.0.0.1 в контейнере — это сам контейнер. Слушать нужно 0.0.0.0,
# иначе снаружи не достучаться, хотя бы порт и был опубликован.
ENV DATABASE_PATH=/var/lib/promvizor/promvizor.db \
    YOLO_CONFIG_DIR=/var/lib/promvizor/.config/ultralytics \
    PYTHONPATH=/opt/promvizor \
    API_HOST=0.0.0.0 \
    API_PORT=8000

USER promvizor
WORKDIR /var/lib/promvizor

EXPOSE 8000

VOLUME ["/var/lib/promvizor"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${API_PORT}/api/health" || exit 1

# Один воркер — см. комментарий в шапке файла. --proxy-headers нужен,
# чтобы за nginx приложение видело реальный IP клиента и схему https.
CMD ["python", "-m", "uvicorn", "app.api.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]