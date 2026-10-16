"""Fotos de reserva para las cartas que salen sin imagen en el catálogo de TCGplayer (tcgcsv.com).

Para cada carta sin foto busca la de la misma carta en otro sitio:
  1. Pokémon (inglés y japonés): TCGdex, por expansión y número.
  2. Mismo código de carta en otra expansión («OP01-016», «BT1-001», «LOB-EN001»…).
  3. Mismo nombre en otra expansión, solo en juegos donde una carta tiene siempre la misma ilustración.

Genera data/imagenes-cartas.json: {"actualizado": "...", "imagenes": {"<productId>": "<url>"}}.
Uso: python3 scripts/imagenes.py data/imagenes-cartas.json
"""
import concurrent.futures as cf
import datetime
import json
import re
import sys
import time
import urllib.request

TCGCSV = "https://tcgcsv.com/tcgplayer"
TCGDEX = "https://api.tcgdex.net/v2"
# Categorías de TCGplayer (las de la app).
CATEGORIES = [3, 85, 68, 1, 2, 80, 63, 86, 81, 89, 79, 62, 20]
# Juegos cuyo «Number» es un código único de la carta (no solo su número dentro de la expansión).
CODE_GAMES = {68, 63, 80, 86, 81, 89, 62, 20, 2}
# Juegos donde la misma carta (mismo nombre) tiene la misma ilustración en todas sus ediciones.
NAME_GAMES = {2, 20, 89, 62, 79}
# Promos de Pokémon: grupo de TCGplayer → expansión de TCGdex.
POKEMON_PROMOS = {24451: "mep", 22872: "svp", 2545: "swshp", 1861: "smp", 1451: "xyp", 1407: "bwp",
                  1453: "hgssp", 1421: "dpp", 1423: "np", 1418: "basep"}


def get(url, tries=3):
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "FoilScan image index"})
            return json.load(urllib.request.urlopen(request, timeout=60))
        except Exception:
            time.sleep(1 + attempt)
    return None


def number_key(number):
    """«070/128» → «70»; «SM210» → «SM210»."""
    first = number.split("/")[0].strip().upper()
    return str(int(first)) if first.isdigit() else first


def clean_name(name):
    """Quita la rareza y el código del nombre de producto, pero no la ilustración alternativa."""
    name = re.sub(r"\s*\((?:[^)]*(?:rare|foil|holo|stamp|prerelease|promo|1st edition|limited|reverse|"
                  r"quarter century|platinum|gold|premium|duel terminal|parallel)[^)]*)\)", "", name, flags=re.I)
    name = re.sub(r"\s*-\s*[A-Z0-9]{2,6}-?[A-Z]{0,3}\d{2,4}[a-z]?$", "", name)
    return re.sub(r"\s+", " ", name).strip().lower()


def letters(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def load_category(category):
    groups = (get(f"{TCGCSV}/{category}/groups") or {}).get("results", [])

    def fetch(group):
        data = get(f"{TCGCSV}/{category}/{group['groupId']}/products") or {"results": []}
        rows = []
        for product in data["results"]:
            number = next((e["value"] for e in product.get("extendedData", []) if e["name"] == "Number"), None)
            if number:
                rows.append({"id": product["productId"], "group": group["groupId"], "name": product["name"],
                             "number": number, "images": product.get("imageCount") or 0,
                             "url": product.get("imageUrl")})
        return rows

    with cf.ThreadPoolExecutor(16) as pool:
        products = [row for rows in pool.map(fetch, groups) for row in rows]
    return {g["groupId"]: g["name"] for g in groups}, products


def tcgdex_images(language, set_ids):
    """Imágenes de TCGdex por expansión: {set_id: {número: url}}."""
    def fetch(set_id):
        data = get(f"{TCGDEX}/{language}/sets/{set_id}") or {}
        return set_id, {number_key(c["localId"]): f"{c['image']}/low.webp" for c in data.get("cards", []) if c.get("image")}

    with cf.ThreadPoolExecutor(8) as pool:
        return dict(pool.map(fetch, set_ids))


def pokemon_sets(category, groups):
    """Expansión de TCGdex de cada grupo de TCGplayer (por nombre en inglés, por código en japonés)."""
    language = "en" if category == 3 else "ja"
    sets = get(f"{TCGDEX}/{language}/sets") or []
    result = {}
    for group_id, name in groups.items():
        if category == 3:
            if group_id in POKEMON_PROMOS:
                result[group_id] = POKEMON_PROMOS[group_id]
                continue
            short = letters(name.split(":")[-1])
            match = next((s["id"] for s in sets if letters(s["name"]) == short), None)
        else:
            code = name.split(":")[0].strip().lower()
            match = next((s["id"] for s in sets if s["id"].lower() == code), None)
        if match:
            result[group_id] = match
    return language, result


def build():
    images = {}
    for category in CATEGORIES:
        groups, products = load_category(category)
        with_image = [p for p in products if p["images"] and p["url"]]
        missing = [p for p in products if not p["images"]]
        by_code, by_name = {}, {}
        for product in with_image:
            by_code.setdefault(product["number"].strip().upper(), product["url"])
            by_name.setdefault(clean_name(product["name"]), product["url"])
        pokemon = {}
        if category in (3, 85) and missing:
            language, mapping = pokemon_sets(category, groups)
            needed = {mapping[p["group"]] for p in missing if p["group"] in mapping}
            dex = tcgdex_images(language, needed)
            pokemon = {group: dex.get(set_id, {}) for group, set_id in mapping.items()}
        found = 0
        for product in missing:
            url = None
            if category in (3, 85):
                url = pokemon.get(product["group"], {}).get(number_key(product["number"]))
            code = product["number"].strip().upper()
            if url is None and category in CODE_GAMES and "/" not in code and re.search(r"[A-Z]", code):
                url = by_code.get(code)
            if url is None and category in NAME_GAMES:
                url = by_name.get(clean_name(product["name"]))
            if url:
                images[str(product["id"])] = url
                found += 1
        print(f"categoría {category}: {len(missing)} sin foto, {found} con foto de reserva", flush=True)
    return images


if __name__ == "__main__":
    images = build()
    # Si la lista no ha cambiado, no se reescribe (así no hay un commit nuevo cada día).
    try:
        with open(sys.argv[1]) as file:
            if json.load(file).get("imagenes") == images:
                print("sin cambios:", len(images), "fotos de reserva")
                sys.exit(0)
    except (OSError, ValueError):
        pass
    result = {"actualizado": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "imagenes": images}
    with open(sys.argv[1], "w") as file:
        json.dump(result, file, separators=(",", ":"), sort_keys=True)
    print(len(images), "fotos de reserva")
