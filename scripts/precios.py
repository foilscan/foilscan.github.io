"""Precios de Cardmarket (euros) de cada carta Pokémon de TCGdex, para ver las expansiones en español en la app.

Cardmarket publica cada día su guía de precios (un precio por carta para todos los idiomas). TCGdex dice qué
producto de Cardmarket es cada carta (el mismo id de carta sirve para todos sus idiomas: «me01-186»).

Genera data/precios-cardmarket.json: {"actualizado": "...", "eur": {"me01-186": 19.33, ...}}.
Lo que ya se sabe de TCGdex (carta → producto de Cardmarket) se guarda en data/cardmarket-ids.json para no volver
a pedirlo cada día: solo se piden las cartas nuevas.

TCGdex no tiene el producto de algunas expansiones (Gym Heroes y Challenge, Shiny Vault de Hidden Fates, promos…) y
en otras da el de otra versión de la carta (las dos Patrat de Black & White con el mismo producto). Con el catálogo
público de Cardmarket (nombre y ataques de cada producto, «Patrat [Hyper Fang]») se empareja cada carta por nombre y
ataques dentro de su expansión de Cardmarket; si sale un solo producto, ese. El nombre y los ataques de cada carta de
TCGdex se guardan en data/tcgdex-firmas.json (solo se piden las nuevas).
Uso: python3 scripts/precios.py data/precios-cardmarket.json
"""
import concurrent.futures as cf
import datetime
import json
import os
import re
import sys
import time
import urllib.request
import zlib

TCGDEX = "https://api.tcgdex.net/v2"
GUIDE = "https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_6.json"
CATALOG = "https://downloads.s3.cardmarket.com/productCatalog/productList/products_singles_6.json"
# TCG Pocket (el juego para móvil): no tiene cartas en Cardmarket.
POCKET = re.compile(r"^(A\d|B\d|P-A|P-B)")


def get(url, tries=3):
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "FoilScan price index", "Accept": "application/json"})
            return json.load(urllib.request.urlopen(request, timeout=90))
        except Exception:
            time.sleep(1 + attempt)
    return None


def card_ids():
    """Ids de todas las cartas de TCGdex (en inglés y en español; son los mismos)."""
    ids = set()
    for language in ("en", "es"):
        for card in get(f"{TCGDEX}/{language}/cards") or []:
            ids.add(card["id"])
    return ids


def product_of(card_id):
    data = get(f"{TCGDEX}/en/cards/{card_id}") or {}
    market = (data.get("pricing") or {}).get("cardmarket") or {}
    return card_id, market.get("idProduct")


def signature(card_id):
    """Nombre y ataques (y habilidades) de una carta de TCGdex."""
    data = get(f"{TCGDEX}/en/cards/{card_id}")
    if not data:
        return card_id, None
    moves = [m.get("name") for m in (data.get("attacks") or []) + (data.get("abilities") or []) if m.get("name")]
    return card_id, [data.get("name"), moves]


def plain(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def by_catalog(known, folder):
    """Producto de Cardmarket de cada carta por nombre y ataques (ver arriba). Devuelve {carta: producto} solo para las
    que TCGdex no tiene o tiene con un producto de otros ataques."""
    path = os.path.join(folder, "tcgdex-firmas.json")
    try:
        with open(path) as file:
            signatures = json.load(file)
    except (OSError, ValueError):
        signatures = {}
    pending = sorted(i for i in known if i not in signatures and not POCKET.match(i))
    with cf.ThreadPoolExecutor(6) as pool:
        for card_id, value in pool.map(signature, pending):
            if value:
                signatures[card_id] = value
    with open(path, "w") as file:
        json.dump(signatures, file, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    catalog = [p for p in (get(CATALOG) or {}).get("products", []) if p.get("idCategory") == 51]
    if not catalog:
        return {}
    products = {p["idProduct"]: p for p in catalog}
    expansions = {}
    for product in catalog:
        expansions.setdefault(product["idExpansion"], []).append(product)

    def base(name):
        return plain(name.split(" [")[0].split(" (")[0])

    def moves(name):
        match = re.search(r"\[(.*)\]", name)
        return frozenset(plain(m) for m in match.group(1).split("|")) if match else frozenset()

    set_of = lambda card_id: card_id.rsplit("-", 1)[0]
    votes = {}
    for card_id, product in known.items():
        if product in products:
            votes.setdefault(set_of(card_id), {}).setdefault(products[product]["idExpansion"], 0)
            votes[set_of(card_id)][products[product]["idExpansion"]] += 1
    by_set = {}
    for card_id in signatures:
        by_set.setdefault(set_of(card_id), []).append(card_id)
    found = {}
    for set_id, ids in by_set.items():
        if votes.get(set_id):
            expansion = max(votes[set_id], key=votes[set_id].get)
        else:
            # Ninguna con producto: la expansión de Cardmarket con más nombres de esta expansión (la mitad, al menos).
            names = {base(signatures[i][0]) for i in ids}
            best = max(((len(names & {base(p["name"]) for p in ps}), e) for e, ps in expansions.items()), default=(0, None))
            expansion = best[1] if best[0] >= max(3, len(names) * 0.5) else None
        for card_id in ids:
            name, card_moves = signatures[card_id]
            card_moves = frozenset(plain(m) for m in card_moves)
            same = [p for p in expansions.get(expansion, []) if base(p["name"]) == base(name)]
            if len(same) > 1 and card_moves:
                same = [p for p in same if moves(p["name"]) == card_moves]
            if len(same) != 1:
                continue
            current = products.get(known.get(card_id) or 0)
            # Sin producto, o con uno de otros ataques (otra versión de la carta): el del catálogo.
            if current is None or (card_moves and moves(current["name"]) and moves(current["name"]) != card_moves):
                found[card_id] = same[0]["idProduct"]
    print(f"catálogo de Cardmarket: {len(found)} cartas con su producto por nombre y ataques", flush=True)
    return found


def main(output):
    folder = os.path.dirname(output) or "."
    cache_path = os.path.join(folder, "cardmarket-ids.json")
    try:
        with open(cache_path) as file:
            known = json.load(file)
    except (OSError, ValueError):
        known = {}
    ids = card_ids()
    missing = sorted(i for i in ids if i not in known)
    # Las apuntadas sin producto (0) se vuelven a pedir una vez por semana, un séptimo cada día: TCGdex va añadiendo
    # productos (antes se quedaban sin precio para siempre, como las 94 de Hidden Fates Shiny Vault).
    weekday = datetime.date.today().weekday()
    missing += sorted(i for i in ids if known.get(i) == 0 and zlib.crc32(i.encode()) % 7 == weekday)
    print(f"{len(ids)} cartas en TCGdex; {len(missing)} sin producto de Cardmarket conocido", flush=True)
    with cf.ThreadPoolExecutor(8) as pool:
        for count, (card_id, product) in enumerate(pool.map(product_of, missing), start=1):
            # Las que no tienen producto se apuntan como 0 para no volver a pedirlas cada día.
            known[card_id] = product or 0
            if count % 1000 == 0:
                print(f"  {count}/{len(missing)}", flush=True)
                with open(cache_path, "w") as file:
                    json.dump(known, file, separators=(",", ":"), sort_keys=True)
    with open(cache_path, "w") as file:
        json.dump(known, file, separators=(",", ":"), sort_keys=True)

    products = known | by_catalog(known, folder)
    guide = {item["idProduct"]: item for item in (get(GUIDE) or {}).get("priceGuides", [])}
    prices = {}
    for card_id, product in products.items():
        item = guide.get(product)
        if not item:
            continue
        value = item.get("trend") or item.get("avg30") or item.get("avg")
        if value and value > 0:
            prices[card_id] = round(value, 2)
    result = {"actualizado": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "eur": prices}
    with open(output, "w") as file:
        json.dump(result, file, separators=(",", ":"), sort_keys=True)
    print(len(prices), "precios")


if __name__ == "__main__":
    main(sys.argv[1])
