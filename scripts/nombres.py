"""Índice de nombres de las cartas de TCGplayer de los juegos con muchas expansiones, para el buscador y el escáner de
FoilScan.

La app busca por nombre en las expansiones más recientes de cada juego; las antiguas no las descarga todas (Magic y
Yu-Gi-Oh! tienen cientos). Con este índice sabe en qué expansiones está una carta («Black Lotus», «Bao Huang» de
Romance Dawn) y solo descarga esas.

Genera un archivo por juego, data/nombres-<juego>.json:
  {"actualizado": "...", "grupos": [groupId, ...], "cartas": [[productId, índice del grupo, "nombre", "número"], ...]}
Grupos de la más nueva a la más antigua. Solo cartas, no sobres ni cajas; número vacío si no tiene (Alpha, Beta…).
Uso: python3 scripts/nombres.py data
"""
import datetime
import json
import os
import sys
import time
import urllib.request

HEADERS = {"User-Agent": "FoilScan card index (+https://foilscan.github.io)"}
# Juego de la app → categoría de TCGplayer. Los de pocas expansiones (Lorcana, Gundam, Riftbound, Star Wars) no lo
# necesitan: la app ya busca en todas.
GAMES = {
    "magic": 1,
    "yugioh": 2,
    "weiss": 20,
    "fleshAndBlood": 62,
    "digimon": 63,
    "onePiece": 68,
    "dragonBall": 80,
    "unionArena": 81,
}


def get(url, tries=3):
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            return json.load(urllib.request.urlopen(request, timeout=60))
        except Exception:
            time.sleep(2 + attempt * 2)
    return None


def build(category):
    groups = (get(f"https://tcgcsv.com/tcgplayer/{category}/groups") or {}).get("results", [])
    if len(groups) < 10:
        return None
    groups.sort(key=lambda g: g.get("publishedOn") or "", reverse=True)
    rows, kept, failed = [], [], 0
    for group in groups:
        data = get(f"https://tcgcsv.com/tcgplayer/{category}/{group['groupId']}/products")
        if not data:
            failed += 1
            continue
        cards = []
        for product in data.get("results", []):
            extended = {e["name"]: e["value"] for e in product.get("extendedData", [])}
            number, rarity = extended.get("Number"), extended.get("Rarity")
            # Como en la app: sin número ni rareza de carta es producto sellado (las cartas de Alpha no llevan número).
            if not number and (not rarity or rarity == "Code Card"):
                continue
            cards.append([product["productId"], product["name"], number or ""])
        if not cards:
            continue
        index = len(kept)
        kept.append(group["groupId"])
        rows += [[card[0], index, card[1], card[2]] for card in cards]
        time.sleep(0.05)
    # Si falla mucho, mejor el índice de ayer que uno a medias.
    if failed > len(groups) // 10:
        return None
    return {"actualizado": datetime.date.today().isoformat(), "grupos": kept, "cartas": rows}


def main(folder, only=None):
    for game, category in GAMES.items():
        if only and game not in only:
            continue
        result = build(category)
        if not result or len(result["cartas"]) < 500:
            print(f"{game}: no se ha podido generar; se deja el anterior")
            continue
        path = os.path.join(folder, f"nombres-{game}.json")
        with open(path, "w", encoding="utf-8") as out:
            json.dump(result, out, ensure_ascii=False, separators=(",", ":"))
        print(f"{game}: {len(result['grupos'])} expansiones, {len(result['cartas'])} cartas, {os.path.getsize(path) // 1024} KB")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:] or None)
