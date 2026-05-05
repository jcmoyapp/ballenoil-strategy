"""
Importa histórico de precios desde Wayback Machine para cada día desde 'desde' hasta hoy.
Corre en GitHub Actions (sin restricciones CORS). Lanzar manualmente una sola vez.
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

HISTORICO_PATH = 'data/historico_precios.json'
CDX_URL = (
    'https://web.archive.org/cdx/search/cdx'
    '?url=sedeaplicaciones.minetur.gob.es/ServiciosRESTCarburantes/PreciosCarburantes/EstacionesTerrestres/'
    '&output=json&collapse=timestamp:8&filter=statuscode:200&limit=600&from={desde}'
)
ARCH_URL = 'https://web.archive.org/web/{ts}if_/https://sedeaplicaciones.minetur.gob.es/ServiciosRESTCarburantes/PreciosCarburantes/EstacionesTerrestres/'

BRANDS = {
    'ballenoil':     {'brands': ['BALLENOIL'],               'exact': False},
    'plenergy':      {'brands': ['PLENERGY'],                 'exact': False},
    'petroprix':     {'brands': ['PETROPRIX'],                'exact': False},
    'carrefour':     {'brands': ['CARREFOUR'],                'exact': False},
    'carrefour_club':{'brands': ['CARREFOUR'],                'exact': False},
    'alcampo':       {'brands': ['ALCAMPO'],                  'exact': False},
    'galp':          {'brands': ['GALP'],                     'exact': True },
    'galp_energia':  {'brands': ['GALP ENERGIA'],             'exact': True },
    'repsol':        {'brands': ['REPSOL'],                   'exact': False},
    'bp':            {'brands': ['BP'],                       'exact': False},
    'shell':         {'brands': ['SHELL'],                    'exact': True },
    'shell_express': {'brands': ['SHELL EXPRESS'],            'exact': True },
    'moeve':         {'brands': ['MOEVE', 'CEPSA', 'CAMPSA'], 'exact': False},
}


def match_brand(cfg, rotulo):
    r = rotulo.strip().upper()
    if cfg['exact']:
        return any(b == r for b in cfg['brands'])
    return any(b in r or r in b for b in cfg['brands'])


def parse_price(s):
    if not s or not s.strip():
        return None
    try:
        return float(s.strip().replace(',', '.'))
    except ValueError:
        return None


def avg(values):
    v = [x for x in values if x is not None]
    return round(sum(v) / len(v), 4) if v else None


def fetch_url(url, retries=3, timeout=60):
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 ballenoil-strategy/1.0'})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode('utf-8')
        except (urllib.error.URLError, TimeoutError) as e:
            print(f'  intento {attempt}/{retries} fallido: {e}')
            if attempt < retries:
                time.sleep(5 * attempt)
    return None


def load_historico():
    if not os.path.exists(HISTORICO_PATH):
        return []
    with open(HISTORICO_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data.get('snapshots', [])


def save_historico(snapshots):
    os.makedirs('data', exist_ok=True)
    with open(HISTORICO_PATH, 'w', encoding='utf-8') as f:
        json.dump({'v': 1, 'snapshots': snapshots}, f, ensure_ascii=False, separators=(',', ':'))


def main():
    desde = sys.argv[1] if len(sys.argv) > 1 else '2025-01-01'
    desde_clean = desde.replace('-', '')
    print(f'Backfill desde {desde} → hoy')

    # Consultar Wayback Machine CDX
    print('Consultando Wayback Machine CDX…')
    cdx_raw = fetch_url(CDX_URL.format(desde=desde_clean))
    if not cdx_raw:
        print('ERROR: no se pudo obtener el CDX', file=sys.stderr)
        sys.exit(1)

    cdx_data = json.loads(cdx_raw)
    if len(cdx_data) < 2:
        print('No hay capturas disponibles')
        sys.exit(0)

    captures = [
        {
            'timestamp': row[1],
            'date': f'{row[1][:4]}-{row[1][4:6]}-{row[1][6:8]}'
        }
        for row in cdx_data[1:]
    ]
    print(f'{len(captures)} capturas encontradas')

    # Cargar histórico existente
    existing = load_historico()
    existing_dates = {s['date'] for s in existing}
    pending = [c for c in captures if c['date'] not in existing_dates]
    print(f'{len(pending)} días a importar (ya tenemos {len(existing_dates)} días)')

    if not pending:
        print('Nada que importar')
        return

    added, errors = 0, 0

    for i, cap in enumerate(pending):
        url = ARCH_URL.format(ts=cap['timestamp'])
        print(f'[{i+1}/{len(pending)}] {cap["date"]}…', end=' ', flush=True)

        raw = fetch_url(url)
        if not raw:
            print('ERROR')
            errors += 1
            continue

        try:
            data = json.loads(raw)
            lista = data.get('ListaEESSPrecio', [])
            if not lista:
                print('lista vacía')
                errors += 1
                continue

            brands = {}
            for brand_id, cfg in BRANDS.items():
                stations = [s for s in lista if match_brand(cfg, s.get('Rótulo', ''))]
                brands[brand_id] = {
                    'ga':  avg([parse_price(s.get('Precio Gasoleo A'))      for s in stations]),
                    'g95': avg([parse_price(s.get('Precio Gasolina 95 E5')) for s in stations]),
                    'n':   len(stations)
                }

            ts = int(datetime.strptime(cap['date'], '%Y-%m-%d').replace(
                hour=12, tzinfo=timezone.utc).timestamp() * 1000)
            existing.append({'ts': ts, 'date': cap['date'], 'brands': brands})
            existing.sort(key=lambda s: s['ts'])
            added += 1

            ball = brands.get('ballenoil', {})
            print(f"GA={ball.get('ga')}  G95={ball.get('g95')}  n={ball.get('n')}")

            # Guardar cada 10 días para no perder progreso
            if added % 10 == 0:
                save_historico(existing)
                print(f'  → guardado parcial ({added} días añadidos)')

        except (json.JSONDecodeError, KeyError) as e:
            print(f'parse error: {e}')
            errors += 1

        time.sleep(1.5)   # respetar Wayback Machine

    save_historico(existing)
    print(f'\n✓ Completado: {added} días añadidos, {errors} errores')
    print(f'  Total snapshots en archivo: {len(existing)}')


if __name__ == '__main__':
    main()
