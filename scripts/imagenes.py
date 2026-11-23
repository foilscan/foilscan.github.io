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
  6. Pokémon japonés que no está en ninguna parte (las colecciones HeartGold y SoulSilver, los mazos de 2003 a 2010…):
     la misma carta en inglés (mismo nombre, PS y ataques), que lleva el mismo dibujo.

Genera data/imagenes-cartas.json: {"actualizado": "...", "imagenes": {"<productId>": "<url>"}, "vacias": [<groupId>],
"nitidas": {"<productId>": "<url>"}} («vacias»: expansiones sin ningún producto en el catálogo, que la app no enseña;
«nitidas»: cartas Pokémon en inglés con foto pequeña en TCGplayer, con la grande de TCGdex).
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


def colored_promos(missing, with_image, groups):
    """Yu-Gi-Oh!: promos de liga con color («Call of the Mummy (Red)», «(Blue)»…) y rarezas abreviadas («(UTR)»,
    «(SE)»): solo cambia el brillo, el dibujo es el de la carta normal (mismo nombre). Las fichas («Token: Ojama
    (Blue)») no: cada color es otra ficha."""
    tag = re.compile(r"\s*\((?:red|blue|green|purple|bronze|silver|gold|utr|se|ur|scr|sr|qcsr|pscr)\)", re.I)
    by_name = {}
    for product in with_image:
        if plain(product) and not tag.search(product["name"]):
            by_name.setdefault(clean_name(product["name"]), product["url"])
    found = {}
    for product in missing:
        if "token" in product["name"].lower() or not tag.search(product["name"]):
            continue
        url = by_name.get(clean_name(tag.sub("", product["name"])))
        if url:
            found[str(product["id"])] = url
    return found


def letters(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


# Abreviatura de cada grupo («M-P», «SV-P»): en las promos japonesas el nombre no lleva el código delante.
ABBREVIATIONS = {}
# Fecha de salida de cada grupo (para elegir, entre varias impresiones inglesas, la más cercana a la japonesa).
DATES = {}


def load_category(category):
    """Grupos, cartas (con número) y grupos vacíos (sin ningún producto) de una categoría."""
    groups = (get(f"{TCGCSV}/{category}/groups") or {}).get("results", [])
    ABBREVIATIONS.update({g["groupId"]: (g.get("abbreviation") or "").strip() for g in groups})
    DATES.update({g["groupId"]: (g.get("publishedOn") or "")[:10] for g in groups})
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
                             "url": product.get("imageUrl"), "card": card_signature(product) if category in (3, 85) else None})
        return rows

    with cf.ThreadPoolExecutor(16) as pool:
        products = [row for rows in pool.map(fetch, groups) for row in rows]
    return {g["groupId"]: g["name"] for g in groups}, products, empty


def card_signature(product):
    """Pokémon: nombre, PS y ataques (nombre y daño), como los escribe TCGplayer en inglés también en las japonesas.
    None si no tiene ataques (Entrenadores y Energías: cada impresión lleva otro dibujo)."""
    extended = {e["name"]: e["value"] for e in product.get("extendedData", [])}
    moves = []
    for key in sorted(k for k in extended if k.startswith("Attack")):
        text = re.sub(r"<[^>]*>.*$", "", extended[key])
        text = re.sub(r"\[[^\]]*\]", "", text).strip()
        # En las expansiones antiguas el texto del ataque va detrás del nombre: «Spider Web - The Defending Pokémon…»,
        # «Leech Life (30) Remove from…». Solo cuentan el nombre y el daño.
        text = re.split(r"\s+[-–]\s+", text)[0]
        found = re.match(r"([^(]+)(?:\(([^)]*)\))?", text)
        if found:
            damage = (found.group(2) or "").strip()
            moves.append([found.group(1).strip().lower(), damage if re.fullmatch(r"\d+[x×+\-]?", damage) else ""])
    if not moves:
        return None
    # «Delta Species» es otra carta (otro tipo y otro dibujo) con el mismo nombre.
    delta = "delta" in product["name"].lower() or "δ" in product["name"]
    return [base_name(product["name"]) + (" δ" if delta else ""), extended.get("HP", ""), sorted(moves)]


def english_twins(missing, english):
    """Pokémon japonés sin foto en ningún sitio: la de la misma carta en inglés (mismo nombre, PS y ataques). Si hay
    varias impresiones, la que salió más cerca de la japonesa (las reimpresiones mucho después pueden llevar otro dibujo)."""
    def same_art(product):
        """Solo etiquetas de acabado, sello o número de mazo («Mirror Holofoil», «League Promo», «#3», «Prime»): no las
        que distinguen dibujos («Ditto (Squirtle)», con otro dibujo para cada Pokémon que imita)."""
        return all(FINISH.search(tag) or re.fullmatch(r"#?\d+|prime|delta species|league promo", tag, re.I)
                   for tag in tags(product["name"]))

    by_card = {}
    for product in english:
        if product["card"] and product["images"] and product["url"] and same_art(product):
            by_card.setdefault(json.dumps(product["card"]), []).append(product)

    def days(group):
        try:
            return datetime.date.fromisoformat(DATES.get(group) or "")
        except ValueError:
            return None

    found = {}
    for product in missing:
        twins = by_card.get(json.dumps(product["card"])) if product["card"] else None
        if not twins:
            continue
        # Dos cartas distintas de una misma expansión con el mismo texto (los Unown, uno por letra): otro dibujo cada
        # una, no se sabe cuál es.
        numbers = {}
        for twin in twins:
            numbers.setdefault(twin["group"], set()).add(number_key(twin["number"]))
        if any(len(found_numbers) > 1 for found_numbers in numbers.values()):
            continue
        mine = days(product["group"])
        # La de fecha más cercana y, a igual fecha, la normal (sin acabado entre paréntesis).
        twins = sorted(twins, key=lambda twin: (abs((days(twin["group"]) - mine).days) if mine and days(twin["group"]) else 99999,
                                                bool(tags(twin["name"]))))
        found[str(product["id"])] = twins[0]["url"]
    return found


def release_day(group):
    try:
        return datetime.date.fromisoformat(DATES.get(group) or "")
    except ValueError:
        return None


def kit_twins(missing, english, groups):
    """Kits de entrenador (Trainer Kits): reimpresiones de cartas de su época con el mismo dibujo y la numeración del kit
    («Caterpie 1/30»). Los Pokémon, como las gemelas japonesas (mismo nombre, PS y ataques); las Energías y los
    Entrenadores, por el nombre en la expansión normal más cercana (como mucho dos años: después cambia el dibujo)."""
    kit = [p for p in missing if "trainer kit" in groups.get(p["group"], "").lower()]
    found = english_twins([p for p in kit if p["card"]], english)
    by_name = {}
    for product in english:
        if (product["images"] and product["url"] and not tags(product["name"]) and not product["card"]
                and "trainer kit" not in groups.get(product["group"], "").lower()):
            by_name.setdefault(base_name(product["name"]), []).append(product)
    for product in kit:
        mine = release_day(product["group"])
        if product["card"] or str(product["id"]) in found or not mine:
            continue
        options = [(abs((release_day(o["group"]) - mine).days), o) for o in by_name.get(base_name(product["name"]), [])
                   if release_day(o["group"])]
        options = sorted((o for o in options if o[0] <= 730), key=lambda o: o[0])
        if options:
            found[str(product["id"])] = options[0][1]["url"]
    return found


def tcgdex_images(language, set_ids):
    """Imágenes de TCGdex por expansión: {set_id: {número: url}}."""
    def fetch(set_id):
        data = get(f"{TCGDEX}/{language}/sets/{set_id}") or {}
        return set_id, {number_key(c["localId"]): f"{c['image']}/low.webp" for c in data.get("cards", []) if c.get("image")}

    with cf.ThreadPoolExecutor(8) as pool:
        return dict(pool.map(fetch, set_ids))


def set_letters(name):
    name = re.sub(r"\band\b|&", " ", name, flags=re.I)
    name = re.sub(r"\bpromos\b", "collection", name, flags=re.I)
    return letters(re.sub(r"\s+base set$", "", name.strip(), flags=re.I))


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
            # «SV: Surging Sparks» y «SM - Cosmic Eclipse» → «Surging Sparks», «Cosmic Eclipse». También «EX Holon
            # Phantoms» = «Holon Phantoms», «Diamond and Pearl» = «Diamond & Pearl», «Expedition» = «Expedition Base
            # Set» y «McDonald's Promos 2014» = «McDonald's Collection 2014».
            options = {set_letters(name.split(":")[-1]), set_letters(name.split(" - ")[-1]), set_letters(re.sub(r"^EX ", "", name))}
            match = next((s["id"] for s in sets if set_letters(s["name"]) in options), None)
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
    name = re.sub(r"\s+-\s+[A-Z]{0,4}\d{1,4}[a-z]?(?:/\d{1,4})?\s*$", "", name)
    return re.sub(r"\s+", " ", name).strip().lower()


def same_face(product, other):
    """Misma carta con otro acabado (o la misma con otro sello)."""
    extra = [t for t in tags(product["name"]) + tags(other["name"]) if t]
    return all(FINISH.search(t) and not ALTERNATE.search(t) for t in extra)


def finish_variants(missing, with_image):
    """Foto de la misma carta (mismo nombre y número, en la misma expansión o en otra) con otro acabado."""
    index = {}
    for product in with_image:
        index.setdefault((base_name(product["name"]), same_number(product["number"])), []).append(product)
    found = {}
    for product in missing:
        key = (base_name(product["name"]), same_number(product["number"]))
        # Primero la de la misma expansión.
        options = sorted(index.get(key, []), key=lambda o: o["group"] != product["group"])
        match = next((o for o in options if same_face(product, o)), None)
        if match:
            found[str(product["id"])] = match["url"]
    return found


def same_number(number):
    """Número para comparar impresiones: sin ceros delante («051/236» = «51/236») ni la letra de las reimpresiones de
    liga («135a/156» = «135/156», con el mismo dibujo y el sello de la liga)."""
    parts = [re.sub(r"^0+(?=\d)", "", part.strip()) for part in number.strip().upper().split("/")]
    parts[0] = re.sub(r"(?<=\d)A$", "", parts[0])
    return "/".join(parts)


def plain(product):
    """Sin etiquetas de ilustración alternativa: la foto oficial de la carta normal le sirve."""
    return not any(ALTERNATE.search(t) for t in tags(product["name"]))


def championship_originals(missing, with_image, groups):
    """Mazos de campeonato del mundo (World Championship Decks): reimpresiones con el mismo dibujo, número y total que
    la carta original («Mewtwo EX - 2012 (Igor Costa)», 54/99 → la Mewtwo EX 54/99 de Next Destinies). Ni TCGplayer ni
    TCGdex tienen sus fotos: la de la original."""
    def key(name, number):
        parts = number.split("/")
        if len(parts) != 2 or not parts[0].strip().isdigit() or not parts[1].strip().isdigit():
            return None
        base = re.sub(r"\s*\(.*?\)|\s*\[.*?\]|\s+-\s+.*$", "", name)
        return letters(base), int(parts[0]), int(parts[1])
    index = {}
    for product in with_image:
        if "championship deck" not in groups.get(product["group"], "").lower():
            index.setdefault(key(product["name"], product["number"]), product["url"])
    found = {}
    for product in missing:
        if "championship deck" in groups.get(product["group"], "").lower():
            url = index.get(key(product["name"], product["number"]))
            if url:
                found[str(product["id"])] = url
    return found


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


def official_parallels(missing, products, template, code_of):
    """Ilustración alternativa (Alternate Art) sin foto: si TCGplayer solo tiene una versión alternativa de ese código y
    la web oficial solo una paralela («BT27-084_P1»), son la misma. Con varias no se sabe cuál es cuál."""
    alternates = {}
    for product in products:
        code = code_of(product)
        if code and not plain(product):
            alternates.setdefault(code, []).append(product)
    codes = {code_of(p) for p in missing if not plain(p)} - {None}
    codes = [c for c in codes if len(alternates.get(c, [])) == 1]

    def check(code):
        urls = []
        for n in range(1, 4):
            url = template.format(code=f"{code}_P{n}")
            if not exists(url):
                break
            urls.append(url)
        return code, urls

    with cf.ThreadPoolExecutor(6) as pool:
        return {str(alternates[code][0]["id"]): urls[0] for code, urls in pool.map(check, codes) if len(urls) == 1}


def riftbound_gallery():
    """Cartas de la galería oficial de Riftbound (playriftbound.com): {código: url}, p. ej. «VEN-139/166», «UNL-T02»."""
    request = urllib.request.Request("https://playriftbound.com/en-us/card-gallery/", headers={"User-Agent": "Mozilla/5.0 (FoilScan image index)"})
    try:
        html = urllib.request.urlopen(request, timeout=60).read().decode("utf-8", "replace")
        data = json.loads(re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S).group(1))
    except Exception:
        return {}, {}
    cards = []

    def walk(node):
        if isinstance(node, dict):
            if "publicCode" in node and isinstance(node.get("cardImage"), dict):
                cards.append(node)
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(data)
    # La foto en JPEG de 500 px (la original es un PNG de más de 1 MB).
    by_code = {c["publicCode"]: c["cardImage"]["url"] + ("&" if "?" in c["cardImage"]["url"] else "?") + "w=500&fm=jpg&q=85"
               for c in cards if c["cardImage"].get("url")}
    names = {}
    for card in cards:
        if re.fullmatch(r"[A-Z]{3}-T\d+", card["publicCode"]):
            names.setdefault(card["name"].strip().lower(), set()).add(by_code[card["publicCode"]])
    tokens = {name: urls.pop() for name, urls in names.items() if len(urls) == 1}
    return by_code, tokens


def riftbound(missing):
    """Riftbound: foto de la galería oficial por expansión y número («139/166» = VEN-139/166, que TCGplayer da sin la
    expansión), también para las promos de metal y de torneo con el mismo número; las fichas dobles («Bird // Buff»),
    con la de su cara delantera."""
    by_code, tokens = riftbound_gallery()
    sets = {}
    for code in by_code:
        match = re.fullmatch(r"([A-Z]{3})-\d{3}[a-z*]?/(\d{3})", code)
        if match:
            sets.setdefault(int(match.group(2)), set()).add(match.group(1))
    found = {}
    for product in missing:
        number = product["number"].strip()
        if "//" in number or re.fullmatch(r"T\d+", number):
            url = tokens.get(product["name"].split("//")[0].strip().lower())
        else:
            match = re.fullmatch(r"0*(\d+)([a-z*]?)/0*(\d+)", number)
            options = sets.get(int(match.group(3)), set()) if match else set()
            url = by_code.get(f"{next(iter(options))}-{int(match.group(1)):03d}{match.group(2)}/{int(match.group(3)):03d}") if len(options) == 1 else None
        if url:
            found[str(product["id"])] = url
    return found


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
        # «M-P Promotional Cards» no lleva el código delante: el de la abreviatura.
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{1,7}", code):
            code = ABBREVIATIONS.get(product["group"], "")
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{1,7}", code):
            wanted.setdefault(code, []).append(product)
    found = {}
    # Las promos («M-P», «SV-P») siguen saliendo: se vuelven a pedir cada semana (las expansiones, una vez).
    checked = cache.setdefault("_fechas", {})
    today = time.strftime("%Y-%m-%d")
    for code, products in wanted.items():
        entry = cache.get(code)
        stale = code.endswith("-P") and checked.get(code, "") < time.strftime("%Y-%m-%d", time.gmtime(time.time() - 7 * 86400))
        if entry is None or stale:
            checked[code] = today
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


def jpeg_width(data):
    """Ancho de una foto JPEG por su cabecera (marcador SOF), o None."""
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            return int.from_bytes(data[i + 7:i + 9], "big")
        if marker == 0xFF or marker == 0x01 or 0xD0 <= marker <= 0xD8:
            i += 1 if marker == 0xFF else 2
            continue
        i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    return None


def tcgplayer_width(product_id):
    """Ancho de la foto grande de TCGplayer leyendo solo el principio del archivo (None si no se ha podido)."""
    url = f"https://tcgplayer-cdn.tcgplayer.com/product/{product_id}_in_1000x1000.jpg"
    for size in (16384, 131072):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "FoilScan image index", "Range": f"bytes=0-{size - 1}"})
            width = jpeg_width(urllib.request.urlopen(request, timeout=30).read())
        except Exception:
            return None
        if width:
            return width
    return None


# Por debajo de este ancho la foto de TCGplayer se ve borrosa (hay expansiones enteras a 200 px: Base Set, Expedition,
# EX Holon Phantoms, XY BREAKthrough…) y el escáner la compara peor con la foto de la cámara.
SMALL = 400


def pokemon_sharper(with_image, groups, widths_path):
    """Pokémon en inglés con foto pequeña en TCGplayer: la de TCGdex (misma expansión y número), que es grande.
    Los anchos medidos se guardan en `widths_path` para medir cada día solo las cartas nuevas."""
    try:
        with open(widths_path) as file:
            widths = json.load(file)
    except (OSError, ValueError):
        widths = {}
    pending = [p for p in with_image if str(p["id"]) not in widths]
    with cf.ThreadPoolExecutor(8) as pool:
        for product, width in zip(pending, pool.map(lambda p: tcgplayer_width(p["id"]), pending)):
            if width:
                widths[str(product["id"])] = width
    with open(widths_path, "w") as file:
        json.dump(widths, file, separators=(",", ":"), sort_keys=True)
    small = [p for p in with_image if widths.get(str(p["id"]), SMALL) < SMALL]
    if not small:
        return {}
    language, mapping = pokemon_sets(3, groups)
    dex = tcgdex_images(language, {mapping[p["group"]] for p in small if p["group"] in mapping})
    result = {}
    for product in small:
        url = dex.get(mapping.get(product["group"]), {}).get(number_key(product["number"]))
        if url:
            result[str(product["id"])] = url
    result |= reprint_originals([p for p in small if str(p["id"]) not in result and plain(p)])
    print(f"Pokémon: {len(small)} con foto pequeña, {len(result)} con la de TCGdex", flush=True)
    return result


def reprint_originals(products):
    """Reimpresiones de otra expansión (Deck Exclusives, League & Championship, Prize Pack…): llevan el número y el total
    de la original («Garchomp - 5/147 (Championship Promo)» → la Garchomp 5/147 de Supreme Victors). Por número, total
    y nombre en las expansiones de TCGdex con ese total."""
    wanted = {}
    for product in products:
        match = re.match(r"^0*(\d+)[a-z]?/0*(\d+)$", product["number"].strip())
        if match:
            wanted[str(product["id"])] = (letters(base_name(product["name"])), match.group(1), int(match.group(2)))
    if not wanted:
        return {}
    sets = get(f"{TCGDEX}/en/sets") or []
    by_total = {}
    for item in sets:
        by_total.setdefault((item.get("cardCount") or {}).get("official"), []).append(item["id"])
    needed = {set_id for _, _, total in wanted.values() for set_id in by_total.get(total, [])}

    def fetch(set_id):
        data = get(f"{TCGDEX}/en/sets/{set_id}") or {}
        return set_id, {(number_key(c["localId"]), letters(c["name"])): f"{c['image']}/low.webp"
                        for c in data.get("cards", []) if c.get("image")}

    with cf.ThreadPoolExecutor(8) as pool:
        cards = dict(pool.map(fetch, needed))
    found = {}
    for product_id, (name, number, total) in wanted.items():
        for set_id in by_total.get(total, []):
            url = cards.get(set_id, {}).get((number, name))
            if url:
                found[product_id] = url
                break
    return found


def build(cache_path):
    images, empty, sharper = {}, [], {}
    english = []
    for category in CATEGORIES:
        groups, products, empty_groups = load_category(category)
        empty += empty_groups
        with_image = [p for p in products if p["images"] and p["url"]]
        missing = [p for p in products if not p["images"]]
        by_code, by_name = {}, {}
        for product in with_image:
            # Por código, solo la foto de la carta normal (antes la primera que salía, y una «Alternate Art» o una «SP»
            # daba su dibujo a la normal).
            if plain(product):
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
            if url is None and category in CODE_GAMES and "/" not in code and re.search(r"[A-Z]", code) and plain(product):
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
        if category == 3:
            extra |= championship_originals(rest, with_image, groups)
            extra |= kit_twins([p for p in rest if str(p["id"]) not in extra], products, groups)
            sharper = pokemon_sharper(with_image, groups, os.path.join(os.path.dirname(cache_path), "anchos-tcgplayer.json"))
        if category == 3:
            english = products
        if category == 85:
            extra |= pokemon_jp_official(rest, groups, cache_path)
            extra |= english_twins([p for p in rest if str(p["id"]) not in extra], english)
        elif category == 63:
            digimon_code = lambda p: (re.match(r"[A-Z]{1,4}\d{0,2}-\d{2,3}", p["number"].strip()) or [None])[0]
            extra |= official_by_code(rest, "https://world.digimoncard.com/images/cardlist/card/{code}.png", digimon_code)
            extra |= official_parallels([p for p in rest if str(p["id"]) not in extra], products,
                                        "https://world.digimoncard.com/images/cardlist/card/{code}.png", digimon_code)
        elif category == 68:
            extra |= official_by_code(rest, "https://en.onepiece-cardgame.com/images/cardlist/card/{code}.png",
                                      lambda p: (re.match(r"[A-Z]{1,4}\d{0,2}-\d{2,3}", p["number"].strip()) or [None])[0])
        elif category == 1:
            extra |= scryfall(rest)
        elif category == 2:
            extra |= colored_promos(rest, with_image, groups)
        elif category == 89:
            extra |= riftbound(rest)
        elif category == 80:
            extra |= official_by_code(rest, "https://www.dbs-cardgame.com/fw/images/cards/card/en/{code}.webp",
                                      lambda p: (re.match(r"F[BSP]\d{2}-\d{2,3}", p["number"].strip()) or [None])[0])
        elif category == 81:
            extra |= official_by_code(rest, "https://www.unionarena-tcg.com/na/images/cardlist/card/{code}.png",
                                      lambda p: p["number"].strip().replace("/", "_") if re.fullmatch(r"[A-Z0-9]+/[A-Z]{2,4}-\d-\d{3}", p["number"].strip()) else None)
        elif category == 20:
            extra |= official_by_code(rest, "https://en.ws-tcg.com/wordpress/wp-content/images/cardimages/{code}.png",
                                      weiss_path, exact=True)
        images |= extra
        print(f"categoría {category}: {len(missing)} sin foto, {found + len(extra)} con foto de reserva, "
              f"{len(empty_groups)} expansiones vacías", flush=True)
    return images, sorted(empty), sharper


if __name__ == "__main__":
    images, empty, sharper = build(os.path.join(os.path.dirname(sys.argv[1]) or ".", "pokemon-jp.json"))
    # Si la lista no ha cambiado, no se reescribe (así no hay un commit nuevo cada día).
    try:
        with open(sys.argv[1]) as file:
            previous = json.load(file)
            if previous.get("imagenes") == images and previous.get("vacias") == empty and previous.get("nitidas") == sharper:
                print("sin cambios:", len(images), "fotos de reserva")
                sys.exit(0)
    except (OSError, ValueError):
        pass
    result = {"actualizado": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "imagenes": images,
              "vacias": empty, "nitidas": sharper}
    with open(sys.argv[1], "w") as file:
        json.dump(result, file, separators=(",", ":"), sort_keys=True)
    print(len(images), "fotos de reserva")
