"""
Captura precios del Ministerio de Energía y añade snapshot a data/historico_precios.json
Corre en GitHub Actions dos veces al día (9h y 15h hora Madrid).
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

HISTORICO_PATH = 'data/historico_precios.json'
API_URL = 'https://sedeaplicaciones.minetur.gob.es/ServiciosRESTCarburantes/PreciosCarburantes/EstacionesTerrestres/'

# Mismas marcas que BPP_GROUPS en la app
BRANDS = {
    'ballenoil':     {'brands': ['BALLENOIL'],               'exact': False},
    'plenergy':      {'brands': ['PLENERGY'],                 'exact': False},
    'petroprix':     {'brands': ['PETROPRIX'],                'exact': False},
    'carrefour':     {'brands': ['CARREFOUR'],                'exact': False},
    'carrefour_club':{'brands': ['CARREFOUR'],                'exact': False},
    'alcampo':       {'brands': ['ALCAMPO'],                  'exact': False},
    'galp':          {'brands': ['GALP'],                     'exact': True },
    'galp_energia':  {'brands': ['GALP ENERGIA'],             'exact': True },
    'repsol':        {'brands': ['REPSOL', 'CAMPSA'],         'exact': False},
    'bp':            {'brands': ['BP'],                       'exact': False},
    'shell':         {'brands': ['SHELL'],                    'exact': True },
    'shell_express': {'brands': ['SHELL EXPRESS'],            'exact': True },
    'moeve':         {'brands': ['MOEVE', 'CEPSA'], 'exact': False},
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


def fetch_ministry():
    req = urllib.request.Request(
        API_URL,
        headers={
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0 ballenoil-strategy/1.0'
        }
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode('utf-8')
    return json.loads(raw)


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
    print('Descargando precios del Ministerio…')
    try:
        data = fetch_ministry()
    except Exception as e:
        print(f'ERROR al descargar: {e}', file=sys.stderr)
        sys.exit(1)

    lista = data.get('ListaEESSPrecio', [])
    if not lista:
        print('ERROR: respuesta vacía', file=sys.stderr)
        sys.exit(1)

    print(f'{len(lista)} gasolineras recibidas')

    # Calcular medias por marca
    brands_out = {}
    for brand_id, cfg in BRANDS.items():
        stations = [s for s in lista if match_brand(cfg, s.get('Rótulo', ''))]
        brands_out[brand_id] = {
            'ga':  avg([parse_price(s.get('Precio Gasoleo A'))     for s in stations]),
            'g95': avg([parse_price(s.get('Precio Gasolina 95 E5')) for s in stations]),
            'n':   len(stations)
        }

    now_utc = datetime.now(timezone.utc)
    today   = now_utc.strftime('%Y-%m-%d')
    ts      = int(now_utc.timestamp() * 1000)

    snap = {'ts': ts, 'date': today, 'brands': brands_out}

    # Cargar existentes — guardar todos (varios snapshots por día)
    existing = load_historico()

    # Evitar duplicado si el último snapshot fue hace menos de 20 min
    if existing:
        last_ts = existing[-1]['ts']
        if (ts - last_ts) < 20 * 60 * 1000:
            print(f'Snapshot reciente ({(ts - last_ts)//60000} min) — omitido')
            return

    existing.append(snap)
    existing.sort(key=lambda s: s['ts'])

    save_historico(existing)

    ball = brands_out.get('ballenoil', {})
    print(f"✓ {today}  Ballenoil GA={ball.get('ga')}  G95={ball.get('g95')}  n={ball.get('n')}")
    print(f"  Total snapshots: {len(existing)}")


if __name__ == '__main__':
    main()
