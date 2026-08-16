"""
Módulo de peticiones a Sofascore.
Soporta:
1. ScraperAPI (https://api.scraperapi.com) para ejecución en entornos como GitHub Actions (evita bloqueos Cloudflare).
2. curl_cffi con impersonación TLS de Chrome para ejecución local ultrarrápida.
"""
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests as std_requests
from curl_cffi import requests as curl_requests

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

base_url = "https://www.sofascore.com/api/v1"
_curl_session = None
_scraper_session = None

_DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Accept': '*/*',
    'Accept-Language': 'es-ES,es;q=0.9',
    'X-Requested-With': 'a25661',
    'Origin': 'https://www.sofascore.com',
    'Referer': 'https://www.sofascore.com/'
}

def get_scraperapi_key():
    return os.getenv("SCRAPERAPI_KEY") or os.getenv("SCRAPER_API_KEY")

def get_curl_session():
    global _curl_session
    if _curl_session is None:
        proxy = os.getenv("SOFA_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
        if proxy and proxy.strip():
            proxy = proxy.strip()
            proxies = {"http": proxy, "https": proxy}
            _curl_session = curl_requests.Session(impersonate="chrome124", proxies=proxies)
            safe_proxy = proxy.split('@')[-1] if '@' in proxy else proxy
            logging.info(f"🌐 [Proxy] Sesión curl_cffi con proxy: {safe_proxy}")
        else:
            _curl_session = curl_requests.Session(impersonate="chrome124")
        _curl_session.headers.update(_DEFAULT_HEADERS)
    return _curl_session

def get_scraper_session():
    global _scraper_session
    if _scraper_session is None:
        _scraper_session = std_requests.Session()
        logging.info("🌐 [ScraperAPI] Modo ScraperAPI activado para peticiones.")
    return _scraper_session

def get_session():
    if get_scraperapi_key():
        return get_scraper_session()
    return get_curl_session()

def ensure_session():
    return get_session()

def renew_session():
    global _curl_session, _scraper_session
    if _curl_session is not None:
        try:
            _curl_session.close()
        except Exception:
            pass
        _curl_session = None
    if _scraper_session is not None:
        try:
            _scraper_session.close()
        except Exception:
            pass
        _scraper_session = None
    return get_session()

def close_driver():
    global _curl_session, _scraper_session
    if _curl_session is not None:
        try:
            _curl_session.close()
        except Exception:
            pass
        _curl_session = None
    if _scraper_session is not None:
        try:
            _scraper_session.close()
        except Exception:
            pass
        _scraper_session = None

def sofa_request(endpoint, params=None, max_retries=3):
    """
    Realiza peticiones HTTP a la API de Sofascore usando ScraperAPI si está configurado,
    o curl_cffi directamente.
    """
    target_url = f"{base_url}/{endpoint}"
    if params:
        query_string = '&'.join(f"{k}={v}" for k, v in params.items())
        target_url += f"?{query_string}"

    scraper_key = get_scraperapi_key()

    if scraper_key:
        session = get_scraper_session()
        scraper_url = "https://api.scraperapi.com"
        req_params = {
            "api_key": scraper_key.strip(),
            "url": target_url
        }

        for attempt in range(1, max_retries + 1):
            try:
                r = session.get(scraper_url, params=req_params, timeout=40)
                if r.status_code == 200:
                    try:
                        return r.json()
                    except Exception as json_err:
                        logging.warning(f"⚠️ [ScraperAPI] Error parseando JSON de {endpoint}: {json_err}")
                        return {}
                elif r.status_code == 404:
                    logging.warning(f"⚠️ [ScraperAPI] 404 Not Found: {endpoint}")
                    return {}
                elif r.status_code == 403:
                    logging.error(f"❌ [ScraperAPI] 403 Forbidden en {endpoint} (Intento {attempt}/{max_retries})")
                    time.sleep(2.0)
                elif r.status_code == 429:
                    logging.warning(f"⚠️ [ScraperAPI] 429 Too Many Requests / Rate limit en {endpoint} (Intento {attempt}/{max_retries})")
                    time.sleep(3.0)
                else:
                    logging.warning(f"⚠️ [ScraperAPI] HTTP {r.status_code} en {endpoint} (Intento {attempt}/{max_retries})")
                    time.sleep(1.0)
            except Exception as e:
                logging.error(f"❌ [ScraperAPI] Error de conexión en {endpoint} (Intento {attempt}/{max_retries}): {e}")
                time.sleep(1.5)

        logging.error(f"❌ [ScraperAPI] Fallaron todos los {max_retries} intentos para {endpoint}")
        return {}

    # Modo Directo (curl_cffi)
    session = get_curl_session()
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
    endpoint = f"event/{match_id}/player/{player_id}/heatmap"
    data = sofa_request(endpoint, max_retries=2)
    return player_id, data.get("heatmap", []) if data else []

def get_match_all_heatmaps(match_id, player_ids, max_workers=5):
    """
    Descarga concurrentemente los mapas de calor de todos los jugadores de un partido.
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
