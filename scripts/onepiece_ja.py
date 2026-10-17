"""Cartas de One Piece que existen en japonés, con las variantes de su foto oficial.

Recorre la lista de cartas de la web oficial japonesa (onepiece-cardgame.com/cardlist), expansión por expansión, y
anota cada imagen («OP01-001.png», «OP01-001_p1.png»…). La app la usa para el catálogo en japonés: solo muestra lo
que existe en japonés y elige la foto de cada variante sin tener que probar direcciones.

Genera data/onepiece-ja.json: {"actualizado": "...", "cartas": {"OP01-001": ["", "_p1"], ...}}.
Uso: python3 scripts/onepiece_ja.py data/onepiece-ja.json
"""
import datetime
import html
import json
import re
import sys
import time
import urllib.request

BASE = "https://www.onepiece-cardgame.com/cardlist/"
HEADERS = {"User-Agent": "FoilScan card index (+https://foilscan.github.io)"}


def page(url, tries=3):
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            return urllib.request.urlopen(request, timeout=60).read().decode("utf-8", "replace")
        except Exception:
            time.sleep(2 + attempt * 2)
    return ""


def main(output):
    first = page(BASE)
    series = list(dict.fromkeys(re.findall(r'<option value="(\d{6})"', first)))
    if not series:
        sys.exit("No se ha podido leer la lista de expansiones")
    cards = {}
    for number in series:
        text = html.unescape(page(f"{BASE}?series={number}"))
        found = re.findall(r"images/cardlist/card/([A-Z0-9]+-[A-Z0-9]+)(_[a-z]\d+)?\.png", text)
        for code, variant in found:
            cards.setdefault(code, set()).add(variant)
        print(f"{number}: {len(set(found))} imágenes", flush=True)
        time.sleep(1)
    result = {
        "actualizado": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "cartas": {code: sorted(variants) for code, variants in sorted(cards.items())},
    }
    with open(output, "w") as file:
        json.dump(result, file, separators=(",", ":"), ensure_ascii=False)
    print(len(cards), "cartas en japonés")


if __name__ == "__main__":
    main(sys.argv[1])
