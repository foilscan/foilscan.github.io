"""Índice de las cartas Pokémon japonesas de TCGplayer (categoría 85, «Pokemon Japan») para el escáner de FoilScan.

Las cartas chinas y coreanas casi siempre son la misma carta que una japonesa (mismo dibujo y PS; las coreanas, también
el mismo número). La app lee el nombre del Pokémon, lo pasa al inglés y busca aquí todas sus cartas japonesas; con los
PS y la comparación con la foto elige la que es. TCGdex no tiene todas las expansiones japonesas: este índice sí.

Genera data/pokemon-japon.json:
  {"actualizado": "...", "grupos": [[groupId, "abreviatura", "aaaa-mm-dd"], ...],
   "cartas": [[productId, índice del grupo, "nombre", "número", PS], ...]}
Grupos de la más nueva a la más antigua; PS 0 si no tiene (entrenadores, energías).
Uso: python3 scripts/japon.py data/pokemon-japon.json
"""
import datetime
import json
import sys
import time
import urllib.request

BASE = "https://tcgcsv.com/tcgplayer/85"
HEADERS = {"User-Agent": "FoilScan card index (+https://foilscan.github.io)"}


def get(url, tries=3):
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            return json.load(urllib.request.urlopen(request, timeout=60))
        except Exception:
            time.sleep(2 + attempt * 2)
    return None


def main(out):
    groups = (get(f"{BASE}/groups") or {}).get("results", [])
    if len(groups) < 100:
        sys.exit("No se han podido descargar los grupos de TCGplayer")
    groups.sort(key=lambda g: g.get("publishedOn") or "", reverse=True)
    rows, kept = [], []
    for group in groups:
        data = get(f"{BASE}/{group['groupId']}/products")
        if not data:
            continue
        cards = []
        for product in data.get("results", []):
            extended = {e["name"]: e["value"] for e in product.get("extendedData", [])}
            number = extended.get("Number")
            # Sin número no es una carta (sobres, cajas, mazos).
            if not number:
                continue
            hp = extended.get("HP") or ""
            cards.append([product["productId"], product["name"], number, int(hp) if hp.isdigit() else 0])
        if not cards:
            continue
        index = len(kept)
        kept.append([group["groupId"], group.get("abbreviation") or "", (group.get("publishedOn") or "")[:10]])
        rows += [[card[0], index] + card[1:] for card in cards]
        time.sleep(0.1)
    if len(rows) < 10000:
        sys.exit(f"Muy pocas cartas ({len(rows)}): no se sobrescribe el índice")
    result = {"actualizado": datetime.date.today().isoformat(), "grupos": kept, "cartas": rows}
    with open(out, "w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, separators=(",", ":"))
    print(f"{len(rows)} cartas en {len(kept)} expansiones")


if __name__ == "__main__":
    main(sys.argv[1])
