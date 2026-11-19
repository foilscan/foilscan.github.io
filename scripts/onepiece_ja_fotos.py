"""Foto japonesa de cada versión de las cartas de One Piece de TCGplayer (que solo tiene las inglesas).

La web oficial japonesa tiene varias fotos por código («OP01-024.png», «_p1», «_p2»…) y TCGplayer varias versiones
(normal, «Alternate Art», «Manga», «SP», con sello…), sin decir cuál es cuál. La app elegía la primera foto alternativa
para todas las versiones alternativas: la manga salía con la foto de la alternativa. Aquí se compara el dibujo de cada
versión inglesa con cada foto japonesa del mismo código (la parte de arriba de la carta, casi todo dibujo: el texto
cambia de idioma) y se queda la más parecida. Si ninguna se parece (una versión que no existe en japonés), null: la
app enseña la inglesa.

Solo se compara lo nuevo y lo que aún no tenía foto japonesa: lo ya emparejado se queda en el mismo archivo.
Genera data/onepiece-ja-fotos.json: {"actualizado": "...", "fotos": {"productId": "_p1" | "" | null}}.
Uso: python3 scripts/onepiece_ja_fotos.py data/onepiece-ja.json data/onepiece-ja-fotos.json [carpeta con las fotos oficiales]
"""
import concurrent.futures as cf
import datetime
import io
import json
import sys
import time
import urllib.error
import urllib.request

from PIL import Image

TCGCSV = "https://tcgcsv.com/tcgplayer/68"
OFFICIAL = "https://www.onepiece-cardgame.com/images/cardlist/card/"
HEADERS = {"User-Agent": "FoilScan card index (+https://foilscan.github.io)"}
# Más que esto, el dibujo no es el mismo (medido con cartas de muchas versiones: el mismo dibujo da casi siempre menos de
# 0,25, y las SP, con otro brillo, hasta 0,4; uno distinto, casi siempre más de 0,5).
LIMIT = 0.45


def fetch(url, tries=3):
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            return urllib.request.urlopen(request, timeout=60).read()
        except urllib.error.HTTPError as error:
            if error.code in (403, 404):
                return None
            time.sleep(2 + attempt * 2)
        except Exception:
            time.sleep(2 + attempt * 2)
    return None


def signature(data):
    """La parte de arriba de la carta (casi todo dibujo), reducida a 24 × 18 y normalizada por canal."""
    if not data:
        return None
    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None
    width, height = image.size
    if width < 100 or height < 100:
        return None
    crop = image.crop((int(width * 0.06), int(height * 0.07), int(width * 0.94), int(height * 0.55)))
    pixels = list(crop.resize((24, 18), Image.LANCZOS).getdata())
    channels = []
    for index in range(3):
        values = [pixel[index] for pixel in pixels]
        mean = sum(values) / len(values)
        spread = (sum((value - mean) ** 2 for value in values) / len(values)) ** 0.5 or 1
        channels.append([(value - mean) / spread for value in values])
    return [value for channel in channels for value in channel]


def distance(first, second):
    """1 − correlación (0: igual; 1: nada que ver)."""
    return sum((a - b) ** 2 for a, b in zip(first, second)) / len(first) / 2


def number(product):
    value = next((e["value"] for e in product.get("extendedData", []) if e["name"] == "Number"), None)
    return value.split(" ")[0].strip() if value else None


def official_image(code, suffix, folder):
    """La foto oficial, del disco si ya está descargada (la primera vez se hace con todas)."""
    if folder:
        try:
            return open(f"{folder}/{code}{suffix}.png", "rb").read()
        except OSError:
            pass
    return fetch(f"{OFFICIAL}{code}{suffix}.png")


def main(japanese_path, output, folder=None):
    japanese = json.load(open(japanese_path))["cartas"]
    try:
        previous = json.load(open(output)).get("fotos", {})
    except (OSError, ValueError):
        previous = {}
    groups = json.loads(fetch(f"{TCGCSV}/groups") or b"{}").get("results", [])
    if len(groups) < 50:
        sys.exit("No se han podido descargar los grupos de TCGplayer")
    by_code = {}
    for group in groups:
        data = json.loads(fetch(f"{TCGCSV}/{group['groupId']}/products") or b"{}")
        for product in data.get("results", []):
            code = number(product)
            if code in japanese and product.get("imageUrl") and (product.get("imageCount") or 0) > 0:
                by_code.setdefault(code, []).append(product)
        time.sleep(0.05)
    # Solo los códigos con más de una foto japonesa o más de una versión inglesa: con una de cada no hay duda. Las que no
    # tenían foto japonesa se vuelven a mirar (la web japonesa añade fotos de las versiones nuevas más tarde).
    pending = {code: [p for p in products if previous.get(str(p["productId"])) is None]
               for code, products in by_code.items() if len(japanese[code]) > 1 or len(products) > 1}
    pending = {code: products for code, products in pending.items() if products}
    print(len(pending), "códigos por emparejar,", sum(len(p) for p in pending.values()), "versiones", flush=True)

    def match(code):
        variants = japanese[code]
        official = {suffix: signature(official_image(code, suffix, folder)) for suffix in variants}
        official = {suffix: value for suffix, value in official.items() if value}
        found = {}
        for product in pending[code]:
            url = product["imageUrl"].replace("_200w.jpg", "_400w.jpg")
            mine = signature(fetch(url)) or signature(fetch(product["imageUrl"]))
            if not mine or not official:
                continue
            best = min(official, key=lambda suffix: distance(mine, official[suffix]))
            # Las reimpresiones («_r1») llevan el mismo dibujo que la normal: a casi igual distancia, la normal.
            if best.startswith("_r") and "" in official and distance(mine, official[""]) - distance(mine, official[best]) < 0.1:
                best = ""
            found[str(product["productId"])] = best if distance(mine, official[best]) <= LIMIT else None
        return found

    result = dict(previous)
    with cf.ThreadPoolExecutor(4) as pool:
        for done, found in enumerate(pool.map(match, sorted(pending)), 1):
            result.update(found)
            if done % 100 == 0:
                print(done, "códigos", flush=True)
    with open(output, "w") as file:
        json.dump({"actualizado": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                   "fotos": dict(sorted(result.items(), key=lambda item: int(item[0])))}, file, separators=(",", ":"))
    print(len(result), "versiones emparejadas;", sum(1 for value in result.values() if value is None), "sin foto japonesa")


if __name__ == "__main__":
    main(*sys.argv[1:4])
