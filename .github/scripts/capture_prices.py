"""
Captura precios del Ministerio de Energía y añade snapshot a data/historico_precios.json
Corre en GitHub Actions cada 5 minutos.
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

HISTORICO_PATH = 'data/historico_precios.json'
# Snapshot por estación (último precio visto, para poder detectar cambios) y eventos de
# cambio de precio por estación — esto corre en GitHub Actions cada 30 min, así que a
# diferencia del histórico local del navegador NO depende de que nadie tenga la app abierta.
STATION_SNAPSHOT_PATH = 'data/estaciones_snapshot.json'
STATION_EVENTS_PATH   = 'data/estaciones_eventos.json'
STATION_EVENTS_MAX_AGE_DAYS = 60
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
    'moeve':         {'brands': ['MOEVE', 'CEPSA'],           'exact': False},
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


def load_station_snapshot():
    if not os.path.exists(STATION_SNAPSHOT_PATH):
        return {}
    with open(STATION_SNAPSHOT_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_station_snapshot(snap):
    os.makedirs('data', exist_ok=True)
    with open(STATION_SNAPSHOT_PATH, 'w', encoding='utf-8') as f:
        json.dump(snap, f, ensure_ascii=False, separators=(',', ':'))


def load_station_events():
    if not os.path.exists(STATION_EVENTS_PATH):
        return []
    with open(STATION_EVENTS_PATH, 'r', encoding='utf-8') as f:
        return json.load(f).get('events', [])


def save_station_events(events):
    os.makedirs('data', exist_ok=True)
    with open(STATION_EVENTS_PATH, 'w', encoding='utf-8') as f:
        json.dump({'v': 1, 'events': events}, f, ensure_ascii=False, separators=(',', ':'))


def detect_station_events(lista, ts):
    """Compara el precio de cada estación (de las marcas seguidas) contra el último
    snapshot conocido y genera un evento por cada cambio real de precio. La primera vez
    que se ve una estación solo se guarda su precio (sin generar un evento falso)."""
    prev_snapshot = load_station_snapshot()
    new_snapshot  = dict(prev_snapshot)
    events = []
    for s in lista:
        rotulo = s.get('Rótulo', '') or ''
        brand_id = next((bid for bid, cfg in BRANDS.items() if match_brand(cfg, rotulo)), None)
        if not brand_id:
            continue
        ideess = s.get('IDEESS')
        if not ideess:
            continue
        curr = {'ga': parse_price(s.get('Precio Gasoleo A')), 'g95': parse_price(s.get('Precio Gasolina 95 E5'))}
        prev = prev_snapshot.get(ideess)
        if prev:
            for fuel in ('ga', 'g95'):
                op, np = prev.get(fuel), curr.get(fuel)
                if op is not None and np is not None and abs(np - op) > 0.0005:
                    events.append({'pollTs': ts, 'stationId': ideess, 'brand': rotulo, 'fuel': fuel, 'oldPrice': op, 'newPrice': np})
        new_snapshot[ideess] = curr
    save_station_snapshot(new_snapshot)

    existing = load_station_events()
    cutoff = ts - STATION_EVENTS_MAX_AGE_DAYS * 24 * 3600 * 1000
    existing = [e for e in existing if e['pollTs'] >= cutoff]
    existing.extend(events)
    save_station_events(existing)
    return events


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

    # La detección de eventos por estación corre SIEMPRE, en cada ejecución (cada 5 min) —
    # independiente del guard de abajo, que solo controla cada cuánto se guarda un nuevo
    # snapshot de MEDIAS por marca (eso sí puede seguir siendo menos frecuente sin perder
    # precisión, ya que la media apenas se mueve de una ejecución a la siguiente).
    station_events = detect_station_events(lista, ts)
    if station_events:
        print(f'  {len(station_events)} cambios de precio por estación detectados')

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
