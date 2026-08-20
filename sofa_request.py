"""
Módulo de peticiones a Sofascore utilizando curl_cffi con impersonación TLS de Chrome.
Optimizado para ejecución local ultra-rápida y consumo mínimo de memoria RAM (<20 MB).
"""
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from curl_cffi import requests as curl_requests

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

base_url = "https://www.sofascore.com/api/v1"
_curl_session = None

_DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': '*/*',
    'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
    'Origin': 'https://www.sofascore.com',
    'Referer': 'https://www.sofascore.com/'
}


def get_curl_session():
    """Obtiene o inicializa la sesión persistente de curl_cffi."""
    global _curl_session
    if _curl_session is None:
        _curl_session = curl_requests.Session(impersonate="chrome124")
        _curl_session.headers.update(_DEFAULT_HEADERS)
    return _curl_session


def get_session():
    """Alias para obtener la sesión activa."""
    return get_curl_session()


def ensure_session():
    """Asegura que la sesión esté lista."""
    return get_curl_session()


def close_driver():
    """Cierra la sesión HTTP de curl_cffi y libera recursos."""
    global _curl_session
    if _curl_session is not None:
        try:
            _curl_session.close()
        except Exception:
            pass
        _curl_session = None


def close_session():
    """Alias explícito para cerrar la sesión."""
    close_driver()


def sofa_request(endpoint, params=None, max_retries=3):
    """
    Realiza peticiones HTTP a la API de Sofascore usando curl_cffi con TLS impersonation.
    """
    if endpoint.startswith("/"):
        endpoint = endpoint[1:]

    target_url = f"{base_url}/{endpoint}"
    if params:
        query_string = '&'.join(f"{k}={v}" for k, v in params.items())
        target_url += f"?{query_string}"

    session = get_curl_session()

    for attempt in range(1, max_retries + 1):
        try:
            r = session.get(target_url, timeout=12)
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
                logging.error(f"❌ [Sofascore] 403 Forbidden al consultar {endpoint} (Intento {attempt}/{max_retries})")
                time.sleep(1.0 + attempt * 0.5)
            elif r.status_code == 429:
                logging.warning(f"⚠️ [Sofascore] 429 Too Many Requests en {endpoint} (Intento {attempt}/{max_retries})")
                time.sleep(2.0 + attempt * 1.0)
            else:
                logging.warning(f"⚠️ [Sofascore] HTTP {r.status_code} en {endpoint} (Intento {attempt}/{max_retries})")
                time.sleep(0.5)
        except Exception as e:
            logging.error(f"❌ [Sofascore] Error de conexión en {endpoint} (Intento {attempt}/{max_retries}): {e}")
            time.sleep(0.8)

    logging.error(f"❌ [Sofascore] Fallaron todos los {max_retries} intentos para {endpoint}")
    return {}


def _fetch_single_heatmap(match_id, player_id):
    """Descarga el mapa de calor de un jugador individual."""
    endpoint = f"event/{match_id}/player/{player_id}/heatmap"
    data = sofa_request(endpoint, max_retries=2)
    return player_id, data.get("heatmap", []) if data else []


def get_match_all_heatmaps(match_id, player_ids, max_workers=6):
    """
    Descarga concurrentemente los mapas de calor de todos los jugadores de un partido
    usando ThreadPoolExecutor en paralelo para máxima velocidad.
    """
    if not player_ids:
        return {}

    raw_data = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_fetch_single_heatmap, match_id, pid) for pid in player_ids]
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
    """Descarga información básica del partido."""
    return sofa_request(f"event/{match_id}")


def get_match_lineups(match_id):
    """Descarga alineaciones y estadísticas de jugadores."""
    return sofa_request(f"event/{match_id}/lineups")


def get_match_incidents(match_id):
    """Descarga incidentes (goles, tarjetas, sustituciones)."""
    return sofa_request(f"event/{match_id}/incidents")


def get_match_shotmap(match_id):
    """Descarga el mapa de tiros del partido."""
    return sofa_request(f"event/{match_id}/shotmap")


def get_match_player_heatmap(match_id, player_id):
    """Descarga el mapa de calor individual de un jugador."""
    return sofa_request(f"event/{match_id}/player/{player_id}/heatmap")


def get_tournament_round(tournament_id=155, season_id=87913, round=1):
    """Descarga partidos de una ronda regular del torneo."""
    return sofa_request(f"unique-tournament/{tournament_id}/season/{season_id}/events/round/{round}")


def get_tournament_round_playoff(tournament_id=155, season_id=87913, round=29, slug="final"):
    """Descarga partidos de fase eliminatoria / playoffs."""
    return sofa_request(f"unique-tournament/{tournament_id}/season/{season_id}/events/round/{round}/slug/{slug}")


def get_tournament_next_matches(tournament_id=155, season_id=87913, page=0):
    """Descarga próximos partidos del torneo."""
    return sofa_request(f"unique-tournament/{tournament_id}/season/{season_id}/events/next/{page}")
