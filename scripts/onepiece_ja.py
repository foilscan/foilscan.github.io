"""Cartas de One Piece que existen en japonés, con las variantes de su foto oficial.

Recorre la lista de cartas de la web oficial japonesa (onepiece-cardgame.com/cardlist), expansión por expansión, y
anota cada imagen («OP01-001.png», «OP01-001_p1.png»…). La app la usa para el catálogo en japonés: solo muestra lo
que existe en japonés y elige la foto de cada variante sin tener que probar direcciones.

Genera data/onepiece-ja.json: {"actualizado": "...", "cartas": {"OP01-001": ["", "_p1"], ...}}.
Con un segundo archivo, también el nombre japonés, el tipo y la fuerza de cada código, para que el escáner reconozca por
el nombre las que no deja leer el código (las alternativas a toda carta): {"actualizado": "...", "nombres":
{"OP01-001": ["ロロノア・ゾロ", "LEADER", 5000, "超新星/麦わらの一味"], ...}} (fuerza null en eventos y escenarios;
lo último son los rasgos, que van impresos debajo del nombre y a veces son el nombre de otra carta: «白ひげ海賊団»).
Uso: python3 scripts/onepiece_ja.py data/onepiece-ja.json [data/onepiece-ja-nombres.json]
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


def card_names(text):
    """Nombre, tipo, fuerza y rasgos de cada código en una página de la lista oficial."""
    names = {}
    for block_id, body in re.findall(r'<dl class="modalCol" id="([^"]+)">(.*?)</dl>', text, re.S):
        info = re.search(r'class="infoCol">(.*?)</div>', body, re.S)
        name = re.search(r'class="cardName">([^<]*)<', body)
        if not info or not name:
            continue
        spans = [value.strip() for value in re.findall(r"<span>([^<]*)</span>", info.group(1))]
        code = spans[0] if spans else block_id.split("_")[0]
        power = re.search(r'class="power"><h3>[^<]*</h3>\s*([0-9]+)', body)
        feature = re.search(r'class="feature"><h3>[^<]*</h3>([^<]*)<', body)
        if code not in names and name.group(1).strip():
            names[code] = [name.group(1).strip(), spans[2] if len(spans) > 2 else "", int(power.group(1)) if power else None,
                           feature.group(1).strip() if feature else ""]
    return names


def main(output, names_output=None):
    first = page(BASE)
    series = list(dict.fromkeys(re.findall(r'<option value="(\d{6})"', first)))
    if not series:
        sys.exit("No se ha podido leer la lista de expansiones")
    cards = {}
    names = {}
    for number in series:
        text = html.unescape(page(f"{BASE}?series={number}"))
        found = re.findall(r"images/cardlist/card/([A-Z0-9]+-[A-Z0-9]+)(_[a-z]\d+)?\.png", text)
        for code, variant in found:
            cards.setdefault(code, set()).add(variant)
        for code, value in card_names(text).items():
            names.setdefault(code, value)
        print(f"{number}: {len(set(found))} imágenes", flush=True)
        time.sleep(1)
    result = {
        "actualizado": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "cartas": {code: sorted(variants) for code, variants in sorted(cards.items())},
    }
    with open(output, "w") as file:
        json.dump(result, file, separators=(",", ":"), ensure_ascii=False)
    print(len(cards), "cartas en japonés")
    # Solo si salen casi todos: una página cambiada no deja al escáner sin nombres.
    if names_output and len(names) >= len(cards) * 0.9:
        with open(names_output, "w") as file:
            json.dump({"actualizado": result["actualizado"], "nombres": dict(sorted(names.items()))}, file,
                      separators=(",", ":"), ensure_ascii=False)
        print(len(names), "nombres")


if __name__ == "__main__":
    main(*sys.argv[1:3])
