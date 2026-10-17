"""Fotos en español para las cartas Pokémon que TCGdex no tiene en español.

Las busca en el servidor de imágenes oficial de Pokémon (assets.pokemon.com, la base de datos de cartas de
pokemon.com/es), deduciendo el código de cada expansión a partir del de TCGdex («sm7.5» → «SM75», «swsh9tg» →
«SWSH9TG», «swsh12.5gg» → «SWSH12PT5GG») y comprobando que la imagen existe.

Genera data/imagenes-es.json: {"actualizado": "...", "imagenes": {"<id TCGdex>": "<url>"}}.
Uso: python3 scripts/imagenes_es.py data/imagenes-es.json
"""
import concurrent.futures as cf
import datetime
import json
import sys
import time
import urllib.request

TCGDEX = "https://api.tcgdex.net/v2"
CDN = "https://assets.pokemon.com/assets/cms2-es-es/img/cards/web"


def get(url, tries=3):
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "FoilScan image index", "Accept": "application/json"})
            return json.load(urllib.request.urlopen(request, timeout=60))
        except Exception:
            time.sleep(1 + attempt)
    return None


def exists(url):
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "FoilScan image index"})
    for attempt in range(2):
        try:
            return urllib.request.urlopen(request, timeout=20).status == 200
        except urllib.error.HTTPError:
            return False
        except Exception:
            time.sleep(1 + attempt)
    return False


def set_codes(set_id):
    """Códigos posibles en pokemon.com para una expansión de TCGdex."""
    upper = set_id.upper()
    codes = [upper.replace(".", ""), upper.replace(".5", "PT5")]
    return list(dict.fromkeys(codes))


def numbers(local_id):
    """«001» → «1», «001»; «TG04» → «TG04»; «SWSH001» → «SWSH001», «SWSH1»."""
    options = [local_id]
    letters = "".join(ch for ch in local_id if ch.isalpha())
    digits = local_id[len(letters):]
    if digits.isdigit():
        options.append(letters + str(int(digits)))
    return list(dict.fromkeys(options))


def find_code(set_id, cards):
    """El código de la expansión que funciona, probando con sus primeras cartas."""
    for code in set_codes(set_id):
        for card in cards[:3]:
            for number in numbers(card["localId"]):
                if exists(f"{CDN}/{code}/{code}_ES_{number}.png"):
                    return code
    return None


def main(output):
    series = [s["id"] for s in get(f"{TCGDEX}/es/series") or [] if s["id"] != "tcgp"]
    set_ids = []
    for serie in series:
        set_ids += [s["id"] for s in (get(f"{TCGDEX}/es/series/{serie}") or {}).get("sets", [])]
    images = {}
    for set_id in set_ids:
        detail = get(f"{TCGDEX}/es/sets/{set_id}") or {}
        spanish = detail.get("cards", [])
        if spanish:
            missing = [c for c in spanish if not c.get("image")]
        else:
            # TCGdex no tiene las cartas en español de muchas expansiones antiguas (Diamante y Perla, HeartGold,
            # Platino…): se buscan todas las de la versión inglesa (mismos ids).
            missing = (get(f"{TCGDEX}/en/sets/{set_id}") or {}).get("cards", [])
        if not missing:
            continue
        code = find_code(set_id, missing)
        if not code:
            print(f"{set_id}: {len(missing)} sin foto, no está en pokemon.com", flush=True)
            continue

        def lookup(card):
            for number in numbers(card["localId"]):
                url = f"{CDN}/{code}/{code}_ES_{number}.png"
                if exists(url):
                    return card["id"], url
            return card["id"], None

        with cf.ThreadPoolExecutor(6) as pool:
            found = {card_id: url for card_id, url in pool.map(lookup, missing) if url}
        images |= found
        print(f"{set_id} ({code}): {len(found)}/{len(missing)} fotos en español", flush=True)
    result = {"actualizado": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "imagenes": images}
    with open(output, "w") as file:
        json.dump(result, file, separators=(",", ":"), sort_keys=True)
    print(len(images), "fotos en español")


if __name__ == "__main__":
    main(sys.argv[1])
