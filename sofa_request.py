"""
Módulo de peticiones a Sofascore.
Utiliza curl_cffi para emulación TLS a nivel C.
"""
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from curl_cffi import requests

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

base_url = "https://www.sofascore.com/api/v1"
_session = None

_DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Accept': '*/*',
    'Accept-Language': 'es-ES,es;q=0.9',
    'X-Requested-With': 'a25661',
    'Origin': 'https://www.sofascore.com',
    'Referer': 'https://www.sofascore.com/'
}

def get_session():
    """
    Inicializa o retorna la sesión HTTP ligera con impersonación TLS de Chrome y soporte para proxy.
    """
    global _session
    if _session is None:
        proxy = os.getenv("SOFA_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
        if proxy and proxy.strip():
            proxy = proxy.strip()
            proxies = {"http": proxy, "https": proxy}
            _session = requests.Session(impersonate="chrome124", proxies=proxies)
            # Log sin exponer posibles contraseñas en URL del proxy
            safe_proxy = proxy.split('@')[-1] if '@' in proxy else proxy
            logging.info(f"🌐 [Proxy] Sesión inicializada con proxy: {safe_proxy}")
        else:
            _session = requests.Session(impersonate="chrome124")
        _session.headers.update(_DEFAULT_HEADERS)
    return _session

def ensure_session():
    return get_session()

def renew_session():
    """
    Reinicia la sesión HTTP ligera en caso de necesitar renovar conexiones.
    """
    global _session
    if _session is not None:
        try:
            _session.close()
        except Exception:
            pass
    _session = None
    return get_session()

def close_driver():
    """
    Cierra la sesión HTTP. Mantenido por compatibilidad con scripts existentes.
    """
    global _session
    if _session is not None:
        try:
            _session.close()
        except Exception:
            pass
        _session = None

def sofa_request(endpoint, params=None, max_retries=3):
    """
    Realiza peticiones HTTP ultrarrápidas a la API de Sofascore con reintentos y logging detallado.
    """
    session = get_session()
    target_url = f"{base_url}/{endpoint}"
    if params:
        query_string = '&'.join(f"{k}={v}" for k, v in params.items())
        target_url += f"?{query_string}"

    for attempt in range(1, max_retries + 1):
        try:
            r = session.get(target_url, timeout=10)
            if r.status_code == 200:
                try:
                    return r.json()
                except Exception as json_err:
                    logging.warning(f"⚠️ [Sofascore] Error parseando JSON de {endpoint}: {json_err}")
                    return {}
            elif r.status_code == 404:
                logging.warning(f"⚠️ [Sofascore] 404 Not Found: {endpoint}")
                return {}
            elif r.status_code == 403:
                logging.error(f"❌ [Sofascore] 403 Forbidden (Bloqueo de IP / Cloudflare) al consultar {endpoint} (Intento {attempt}/{max_retries})")
                time.sleep(1.0 + attempt * 0.5)
            elif r.status_code == 429:
                logging.warning(f"⚠️ [Sofascore] 429 Too Many Requests (Rate limit) en {endpoint} (Intento {attempt}/{max_retries})")
                time.sleep(2.0 + attempt * 1.0)
            else:
                logging.warning(f"⚠️ [Sofascore] HTTP {r.status_code} en {endpoint} (Intento {attempt}/{max_retries})")
                time.sleep(0.5)
        except Exception as e:
            logging.error(f"❌ [Sofascore] Error de conexión en {endpoint} (Intento {attempt}/{max_retries}): {e}")
            time.sleep(0.8)

    logging.error(f"❌ [Sofascore] Fallaron todos los {max_retries} intentos para {endpoint}")
    return {}

def _fetch_single_heatmap(session, match_id, player_id):
    url = f"{base_url}/event/{match_id}/player/{player_id}/heatmap"
    for _ in range(2):
        try:
            r = session.get(url, timeout=6)
            if r.status_code == 200:
                try:
                    return player_id, r.json().get('heatmap', [])
                except Exception:
                    return player_id, []
            elif r.status_code == 404:
                return player_id, []
            elif r.status_code in (403, 429):
                time.sleep(0.5)
        except Exception:
            pass
    return player_id, []

def get_match_all_heatmaps(match_id, player_ids, max_workers=5):
    """
    Descarga concurrentemente los mapas de calor de todos los jugadores de un partido
    mediante hilos ligeros en milisegundos sin abrir navegadores.
    """
    if not player_ids:
        return {}

    session = get_session()
    raw_data = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_fetch_single_heatmap, session, match_id, pid) for pid in player_ids]
        for f in as_completed(futures):
            try:
                pid, heatmap = f.result()
                raw_data[pid] = heatmap
            except Exception:
                pass

    normalized = {}
    for pid in player_ids:
        h = raw_data.get(pid) or raw_data.get(str(pid)) or raw_data.get(int(pid)) or []
        normalized[str(pid)] = h
        try:
            normalized[int(pid)] = h
        except Exception:
            pass

    return normalized

def get_match_data(match_id):
    return sofa_request(f"event/{match_id}")

def get_match_lineups(match_id):
    return sofa_request(f"event/{match_id}/lineups")

def get_match_incidents(match_id):
    return sofa_request(f"event/{match_id}/incidents")

def get_match_shotmap(match_id):
    return sofa_request(f"event/{match_id}/shotmap")

def get_tournament_round(tournament_id=155, season_id=87913, round=1):
    return sofa_request(f"unique-tournament/{tournament_id}/season/{season_id}/events/round/{round}")

def get_tournament_round_playoff(tournament_id=155, season_id=87913, round=29, slug="final"):
    return sofa_request(f"unique-tournament/{tournament_id}/season/{season_id}/events/round/{round}/slug/{slug}")

def get_tournament_next_matches(tournament_id=155, season_id=87913, page=0):
    return sofa_request(f"unique-tournament/{tournament_id}/season/{season_id}/events/next/{page}")

def get_match_player_heatmap(match_id, player_id):
    return sofa_request(f"event/{match_id}/player/{player_id}/heatmap")
