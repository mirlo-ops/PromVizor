"""Готовим фон для окна входа.

Идея. На присланном образце окно входа стоит на размытом цветном
фоне с бликами. Такой фон делает три вещи сразу: отделяет карточку от
страницы, задаёт настроение и не даёт экрану быть пустым тёмным
полем. Взять надо именно градиент, а не картинку целиком: картинка
в исходнике содержала бы чужой знак внутри.

Что делаем:
  * строим градиент по цветам из презентации (#081C15 → #0E221B →
    #265A43 → #52B788), это тот же зелёный, что и логотип;
  * добавляем мягкие световые пятна — на градиенте они читаются как
    блики на стекле;
  * сохраняем в web/assets/login-bg.svg.

Почему SVG, а не PNG. Градиент в SVG масштабируется без потери
качества и весит килобайты вместо мегабайтов. Растровый фон на
каждое открытие входа — это заметная задержка на демонстрации.
"""

from __future__ import annotations

from pathlib import Path

DST = Path(r"F:\Projects\PromVizor\web\assets\login-bg.svg")

#: Размер холста. Достаточно большой, чтобы покрыть и узкий ноутбук,
#: и широкий монитор одной картинкой: SVG тянется под любой экран.
W, H = 1600, 1000

#: Цвета из презентации, от тёмного к светлому.
C_DARK = "#081C15"
C_MID = "#0E221B"
C_GREEN = "#265A43"
C_BRIGHT = "#52B788"
C_LIGHT = "#D8F3DC"


SVG = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}"
     preserveAspectRatio="xMidYMid slice" role="img" aria-label="">
  <defs>
    <!-- Основной диагональный градиент: слева темно, справа зеленее.
         Такой разворот тона был на присланном образце. -->
    <linearGradient id="base" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%"   stop-color="{C_DARK}"/>
      <stop offset="38%"  stop-color="{C_MID}"/>
      <stop offset="72%"  stop-color="{C_GREEN}"/>
      <stop offset="100%" stop-color="{C_BRIGHT}"/>
    </linearGradient>

    <!-- Блик сверху слева: даёт объём, ффон перестаёт быть плоской
         заливкой. -->
    <radialGradient id="glow-a" cx="0.18" cy="0.12" r="0.75">
      <stop offset="0%"   stop-color="{C_BRIGHT}" stop-opacity=".55"/>
      <stop offset="60%"  stop-color="{C_GREEN}" stop-opacity=".18"/>
      <stop offset="100%" stop-color="{C_DARK}" stop-opacity="0"/>
    </radialGradient>

    <!-- Второй, более слабый блик внизу справа. -->
    <radialGradient id="glow-b" cx="0.88" cy="0.92" r="0.6">
      <stop offset="0%"   stop-color="{C_LIGHT}" stop-opacity=".38"/>
      <stop offset="70%"  stop-color="{C_BRIGHT}" stop-opacity=".12"/>
      <stop offset="100%" stop-color="{C_DARK}" stop-opacity="0"/>
    </radialGradient>

    <!-- Затемнение к центру: карточка входа должна читаться на любом
         фоне, а под светлым бликом её края сливались бы с подложкой. -->
    <radialGradient id="veil" cx="0.5" cy="0.5" r="0.72">
      <stop offset="0%"   stop-color="{C_DARK}" stop-opacity=".46"/>
      <stop offset="55%"  stop-color="{C_DARK}" stop-opacity=".12"/>
      <stop offset="100%" stop-color="{C_DARK}" stop-opacity="0"/>
    </radialGradient>
  </defs>

  <rect width="{W}" height="{H}" fill="url(#base)"/>
  <rect width="{W}" height="{H}" fill="url(#glow-a)"/>
  <rect width="{W}" height="{H}" fill="url(#glow-b)"/>
  <rect width="{W}" height="{H}" fill="url(#veil)"/>
</svg>
"""


def main() -> int:
    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(SVG, encoding="utf-8")
    print(f"сохранено: {DST} ({DST.stat().st_size} байт)")
    print(f"размер холста: {W}x{H}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())