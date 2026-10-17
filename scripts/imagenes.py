"""Fotos de reserva para las cartas que salen sin imagen en el catálogo de TCGplayer (tcgcsv.com).

Para cada carta sin foto busca la de la misma carta en otro sitio:
  1. Pokémon (inglés y japonés): TCGdex, por expansión y número.
  2. Mismo código de carta en otra expansión («OP01-016», «BT1-001», «LOB-EN001»…).
  3. Mismo nombre en otra expansión, solo en juegos donde una carta tiene siempre la misma ilustración.
  4. La misma carta (mismo nombre y número) en otro acabado: «Master Ball Pattern», «Mirror Holofoil», «Staff»,
     «Serial Numbered»… Solo si lo único que cambia es el brillo o el sello, nunca una ilustración alternativa.
  5. Webs oficiales: pokemon-card.com (Pokémon japonés), world.digimoncard.com (Digimon),
     en.onepiece-cardgame.com (One Piece), en.ws-tcg.com (Weiss Schwarz) y Scryfall (Magic), por expansión y
     número o por código.

Genera data/imagenes-cartas.json: {"actualizado": "...", "imagenes": {"<productId>": "<url>"}, "vacias": [<groupId>]}
(«vacias»: expansiones sin ningún producto en el catálogo, que la app no enseña).
Uso: python3 scripts/imagenes.py data/imagenes-cartas.json
Lo leído de pokemon-card.com se guarda en data/pokemon-jp.json para no volver a pedirlo cada día.
"""
import concurrent.futures as cf
import datetime
import json
import os
import re
import sys
import time
import urllib.parse
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
                  1453: "hgssp", 1421: "dpp", 1423: "np", 1418: "basep", 23237: "sv03.5"}


def get(url, tries=3):
    for attempt in range(tries):
        try:
            # Scryfall pide «Accept» además de «User-Agent».
            request = urllib.request.Request(url, headers={"User-Agent": "FoilScan image index", "Accept": "application/json"})
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
    """Grupos, cartas (con número) y grupos vacíos (sin ningún producto) de una categoría."""
    groups = (get(f"{TCGCSV}/{category}/groups") or {}).get("results", [])
    empty = []

    def fetch(group):
        data = get(f"{TCGCSV}/{category}/{group['groupId']}/products")
        # Solo cuenta como vacía si se ha podido leer (un fallo de red no es una expansión vacía).
        if data is not None and not data.get("results"):
            empty.append(group["groupId"])
        data = data or {"results": []}
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
    return {g["groupId"]: g["name"] for g in groups}, products, empty


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
            # «SV: Surging Sparks» y «SM - Cosmic Eclipse» → «Surging Sparks», «Cosmic Eclipse».
            options = {letters(name.split(":")[-1]), letters(name.split(" - ")[-1])}
            match = next((s["id"] for s in sets if letters(s["name"]) in options), None)
        else:
            code = name.split(":")[0].strip().lower()
            match = next((s["id"] for s in sets if s["id"].lower() == code), None)
        if match:
            result[group_id] = match
    return language, result


# Etiquetas que solo cambian el brillo, el sello o la firma: la cara de la carta es la misma.
FINISH = re.compile(r"pattern|holo|foil|stamp|staff|signature|serial|prerelease|reverse|mirror|cracked ice|cosmos|"
                    r"league|championship|winner|non-holo|1st edition|unlimited|shadowless|display commander|"
                    r"^(red|blue|green|purple|bronze|silver|gold|black)$|^\d+$", re.I)
# Ilustración distinta: nunca se usa la foto de otra versión.
ALTERNATE = re.compile(r"alternate|alt art|manga|parallel|full art|special|sp$|showcase|borderless|extended|"
                       r"textless|anime|jumbo|oversize", re.I)


def tags(name):
    return [t.strip() for t in re.findall(r"[(\[]([^)\]]*)[)\]]", name)]


def base_name(name):
    name = re.sub(r"[(\[][^)\]]*[)\]]", "", name)
    name = re.sub(r"\s+-\s+[A-Z]{0,4}\d{1,4}(?:/\d{1,4})?\s*$", "", name)
    return re.sub(r"\s+", " ", name).strip().lower()


def same_face(product, other):
    """Misma carta con otro acabado (o la misma con otro sello)."""
    extra = [t for t in tags(product["name"]) + tags(other["name"]) if t]
    return all(FINISH.search(t) and not ALTERNATE.search(t) for t in extra)


def finish_variants(missing, with_image):
    """Foto de la misma carta (mismo nombre y número, en la misma expansión o en otra) con otro acabado."""
    index = {}
    for product in with_image:
        index.setdefault((base_name(product["name"]), product["number"].strip().upper()), []).append(product)
    found = {}
    for product in missing:
        key = (base_name(product["name"]), product["number"].strip().upper())
        # Primero la de la misma expansión.
        options = sorted(index.get(key, []), key=lambda o: o["group"] != product["group"])
        match = next((o for o in options if same_face(product, o)), None)
        if match:
            found[str(product["id"])] = match["url"]
    return found


def plain(product):
    """Sin etiquetas de ilustración alternativa: la foto oficial de la carta normal le sirve."""
    return not any(ALTERNATE.search(t) for t in tags(product["name"]))


def exists(url):
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0 (FoilScan image index)"})
    try:
        return urllib.request.urlopen(request, timeout=20).status == 200
    except Exception:
        return False


def official_by_code(missing, template, code_of, exact=False):
    """Foto oficial por código de carta (Digimon, One Piece, Weiss Schwarz), comprobando que existe.
    Con `exact`, el código ya identifica la versión (rareza incluida) y sirve también para las especiales."""
    candidates = [(p, code_of(p)) for p in missing if exact or plain(p)]
    candidates = [(p, c) for p, c in candidates if c]

    def check(item):
        product, code = item
        url = template.format(code=code)
        return str(product["id"]), url if exists(url) else None

    with cf.ThreadPoolExecutor(6) as pool:
        return {pid: url for pid, url in pool.map(check, candidates) if url}


def scryfall(missing):
    """Magic: misma carta (nombre y número de coleccionista) en Scryfall."""
    found = {}
    for product in missing:
        if not plain(product):
            continue
        number = product["number"].split("/")[0].strip().lstrip("0") or "0"
        query = urllib.parse.quote(f'!"{base_name(product["name"])}" cn:{number}')
        data = get(f"https://api.scryfall.com/cards/search?q={query}&unique=prints") or {}
        time.sleep(0.12)
        cards = data.get("data") or []
        if not cards:
            # TCGplayer y Scryfall numeran distinto algunas expansiones («Commander: Marvel Super Heroes»): por
            # nombre, y solo vale si todas sus ediciones tienen la misma ilustración (se comprueba más abajo).
            query = urllib.parse.quote(f'!"{base_name(product["name"])}"')
            data = get(f"https://api.scryfall.com/cards/search?q={query}&unique=prints") or {}
            time.sleep(0.12)
            cards = data.get("data") or []
        # La versión numerada («Serial Numbered») es la «559z»; si no, la del número exacto.
        serial = any("serial" in t.lower() for t in tags(product["name"]))
        exact = [c for c in cards if ("serialized" in (c.get("promo_types") or [])) == serial] or cards
        exact = [c for c in exact if c.get("collector_number", "").rstrip("z").lstrip("0") == number] or exact
        # Varias opciones solo valen si todas tienen la misma ilustración.
        if not exact or len({c.get("illustration_id") for c in exact}) != 1:
            continue
        card = exact[0]
        uris = card.get("image_uris") or (card.get("card_faces") or [{}])[0].get("image_uris") or {}
        if uris.get("normal"):
            found[str(product["id"])] = uris["normal"]
    return found


def weiss_path(product):
    """«BD/W47-E089S SR» → «b/bd_w47/BD_W47_E089S» (ruta de la foto en la web oficial en inglés)."""
    code = product["number"].strip().split(" ")[0]
    if not re.fullmatch(r"[A-Za-z0-9]+/[A-Za-z0-9]+-[A-Za-z0-9]+", code):
        return None
    folder = code.split("-")[0].replace("/", "_").lower()
    return f"{folder[0]}/{folder}/{code.replace('/', '_').replace('-', '_')}"


POKEMON_JP_SITE = "https://www.pokemon-card.com"


def pokemon_jp_official(missing, groups, cache_path):
    """Pokémon japonés: la web oficial (pokemon-card.com), por código de expansión y número."""
    try:
        with open(cache_path) as file:
            cache = json.load(file)
    except (OSError, ValueError):
        cache = {}
    wanted = {}
    for product in missing:
        if not plain(product):
            continue
        code = groups.get(product["group"], "").split(":")[0].strip()
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{1,7}", code):
            wanted.setdefault(code, []).append(product)
    found = {}
    for code, products in wanted.items():
        entry = cache.get(code)
        if entry is None:
            entry = {}
            first = get(f"{POKEMON_JP_SITE}/card-search/resultAPI.php?keyword=&regulation_sidebar_form=all&pg={code}&page=1")
            if first and first.get("hitCnt"):
                cards = list(first.get("cardList", []))
                for page in range(2, int(first.get("maxPage") or 1) + 1):
                    more = get(f"{POKEMON_JP_SITE}/card-search/resultAPI.php?keyword=&regulation_sidebar_form=all&pg={code}&page={page}")
                    cards += (more or {}).get("cardList", [])
                    time.sleep(0.3)
                def number_of(card):
                    try:
                        request = urllib.request.Request(f"{POKEMON_JP_SITE}/card-search/details.php/card/{card['cardID']}/regu/all",
                                                         headers={"User-Agent": "Mozilla/5.0 (FoilScan image index)"})
                        page_html = urllib.request.urlopen(request, timeout=30).read().decode("utf-8", "ignore")
                    except Exception:
                        return None
                    finally:
                        time.sleep(0.25)
                    # El número va justo debajo de la imagen: «<div class="subtext …"> … 001&nbsp;/&nbsp;086».
                    block = page_html.split('class="subtext', 1)[-1][:600].replace("&nbsp;", " ")
                    number = re.search(r">\s*(\d{1,3})\s*/\s*([A-Za-z0-9-]{2,6})\s*<", block)
                    if not number:
                        return None
                    return f"{int(number.group(1))}/{number.group(2).upper()}", POKEMON_JP_SITE + card["cardThumbFile"]

                # Cuatro a la vez como mucho, para no cargar la web oficial.
                with cf.ThreadPoolExecutor(4) as pool:
                    for result in pool.map(number_of, cards):
                        if result:
                            entry.setdefault(*result)
            cache[code] = entry
            print(f"  pokemon-card.com {code}: {len(entry)} cartas", flush=True)
        for product in products:
            parts = product["number"].split("/")
            if len(parts) == 2 and parts[0].strip().isdigit():
                url = entry.get(f"{int(parts[0])}/{parts[1].strip().upper()}")
                if url:
                    found[str(product["id"])] = url
    os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
    with open(cache_path, "w") as file:
        json.dump(cache, file, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return found


def build(cache_path):
    images, empty = {}, []
    for category in CATEGORIES:
        groups, products, empty_groups = load_category(category)
        empty += empty_groups
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
        # Lo que aún falta: la misma carta con otro acabado y, después, las webs oficiales.
        rest = [p for p in missing if str(p["id"]) not in images]
        extra = finish_variants(rest, with_image)
        rest = [p for p in rest if str(p["id"]) not in extra]
        if category == 85:
            extra |= pokemon_jp_official(rest, groups, cache_path)
        elif category == 63:
            extra |= official_by_code(rest, "https://world.digimoncard.com/images/cardlist/card/{code}.png",
                                      lambda p: (re.match(r"[A-Z]{1,4}\d{0,2}-\d{2,3}", p["number"].strip()) or [None])[0])
        elif category == 68:
            extra |= official_by_code(rest, "https://en.onepiece-cardgame.com/images/cardlist/card/{code}.png",
                                      lambda p: (re.match(r"[A-Z]{1,4}\d{0,2}-\d{2,3}", p["number"].strip()) or [None])[0])
        elif category == 1:
            extra |= scryfall(rest)
        elif category == 20:
            extra |= official_by_code(rest, "https://en.ws-tcg.com/wordpress/wp-content/images/cardimages/{code}.png",
                                      weiss_path, exact=True)
        images |= extra
        print(f"categoría {category}: {len(missing)} sin foto, {found + len(extra)} con foto de reserva, "
              f"{len(empty_groups)} expansiones vacías", flush=True)
    return images, sorted(empty)


if __name__ == "__main__":
    images, empty = build(os.path.join(os.path.dirname(sys.argv[1]) or ".", "pokemon-jp.json"))
    # Si la lista no ha cambiado, no se reescribe (así no hay un commit nuevo cada día).
    try:
        with open(sys.argv[1]) as file:
            previous = json.load(file)
            if previous.get("imagenes") == images and previous.get("vacias") == empty:
                print("sin cambios:", len(images), "fotos de reserva")
                sys.exit(0)
    except (OSError, ValueError):
        pass
    result = {"actualizado": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "imagenes": images,
              "vacias": empty}
    with open(sys.argv[1], "w") as file:
        json.dump(result, file, separators=(",", ":"), sort_keys=True)
    print(len(images), "fotos de reserva")
