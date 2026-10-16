"""Precios de Cardmarket (euros) de cada carta Pokémon de TCGdex, para ver las expansiones en español en la app.

Cardmarket publica cada día su guía de precios (un precio por carta para todos los idiomas). TCGdex dice qué
producto de Cardmarket es cada carta (el mismo id de carta sirve para todos sus idiomas: «me01-186»).

Genera data/precios-cardmarket.json: {"actualizado": "...", "eur": {"me01-186": 19.33, ...}}.
Lo que ya se sabe de TCGdex (carta → producto de Cardmarket) se guarda en data/cardmarket-ids.json para no volver
a pedirlo cada día: solo se piden las cartas nuevas.
Uso: python3 scripts/precios.py data/precios-cardmarket.json
"""
import concurrent.futures as cf
import datetime
import json
import os
import sys
import time
import urllib.request

TCGDEX = "https://api.tcgdex.net/v2"
GUIDE = "https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_6.json"


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

    guide = {item["idProduct"]: item for item in (get(GUIDE) or {}).get("priceGuides", [])}
    prices = {}
    for card_id, product in known.items():
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
