"""Готовим логотип PromVizor из присланной картинки.

Что делаем и зачем.

1. Отделяем монограмму от фона. В исходнике она нарисована зелёным
   по тёмно-зелёному фону: похожие цвета, и простой порог «зелёный
   пиксель — значит логотип» захватывал и фон тоже. Отличаем по
   СВЕТЛОТЕ: линии логотипа светлые, фон тёмный.
2. Текст «PromVizor» в логотип сайта не берём. Подпись и так стоит
   рядом отдельной строкой, а второй раз она дублировалась бы.
3. По краям оставляем поле: обрезка вплотную к линиям на тёмной
   теме даёт «обрезанный» вид.

Результат: web/assets/logo.png — монограмма с прозрачным фоном.
Она и на странице входа, и в дашборде: один файл, а не копии.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

SRC = Path(
    r"C:\Users\mirlo\Downloads\Telegram Desktop\image_2026-10-16_05-06-27.png"
)
DST_DIR = Path(r"F:\Projects\PromVizor\web\assets")
DST = DST_DIR / "logo.png"

#: Светлота фона исходной картинки. Найдена замером: тёмно-зелёный
#: фон держится около 0.25, а линии логотипа — от 0.55 вверх.
BG_VALUE_MAX = 0.45

#: Поле вокруг монограммы, доля от её размера. Без него линии
#: обрезаются по краям и на тёмном фоне это заметно.
PADDING_RATIO = 0.06


def main() -> int:
    if not SRC.is_file():
        print(f"нет исходника: {SRC}")
        return 1

    DST_DIR.mkdir(parents=True, exist_ok=True)
    src = Image.open(SRC).convert("RGB")
    width, height = src.size
    print(f"исходник: {width}x{height}")

    # Светлота = максимум каналов: у линий высокий, у фона низкий.
    r, g, b = src.split()
    pixels = list(zip(r.getdata(), g.getdata(), b.getdata()))

    values = [max(px) / 255.0 for px in pixels]
    light_count = sum(1 for v in values if v > BG_VALUE_MAX)
    print(
        f"светлых пикселей: {light_count} из {len(values)} "
        f"({light_count * 100 // len(values)}%)"
    )

    if light_count < 100:
        print("монограмма не найдена: почти весь кадр тёмный")
        return 1

    # Верхняя половина картинки содержит и монограмму, и подпись.
    # Подпись белым нам не нужна, но у неё светлота выше, поэтому
    # отрезаем её по геометрии: монограмма — верхние ~55 % кадра.
    cutoff = int(height * 0.55)
    alpha = Image.new("L", (width, height), 0)
    alpha_px = []
    for index, value in enumerate(values):
        y = index // width
        keep = y < cutoff and value > BG_VALUE_MAX
        alpha_px.append(255 if keep else 0)
    alpha.putdata(alpha_px)

    bbox = alpha.getbbox()
    if bbox is None:
        print("монограмма не найдена после отсечения по высоте")
        return 1
    print(f"монограмма в {bbox}")

    # Цвет берём с исходника, но усиливаем яркость: на тёмной теме
    # бледно-зелёная линия почти не читается.
    out = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    out_px = []
    for index, (ri, gi, bi) in enumerate(pixels):
        a = alpha_px[index]
        if a:
            # Оригинальный зелёный со сдвигом в яркость: 60 % от
            # исходного плюс 40 % чистого #52B788 из палитры.
            nr = min(255, int(ri * 0.6 + 0x52 * 0.4))
            ng = min(255, int(gi * 0.6 + 0xB7 * 0.4))
            nb = min(255, int(bi * 0.6 + 0x88 * 0.4))
            out_px.append((nr, ng, nb, 255))
        else:
            out_px.append((0, 0, 0, 0))
    out.putdata(out_px)
    out = out.crop(bbox)

    pad_x = int(out.size[0] * PADDING_RATIO)
    pad_y = int(out.size[1] * PADDING_RATIO)
    padded = Image.new(
        "RGBA",
        (out.size[0] + pad_x * 2, out.size[1] + pad_y * 2),
        (0, 0, 0, 0),
    )
    padded.paste(out, (pad_x, pad_y), out)
    out = padded
    print(f"готово: {out.size[0]}x{out.size[1]}")

    out.save(DST)
    print(f"сохранено: {DST} ({DST.stat().st_size // 1024} КБ)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())