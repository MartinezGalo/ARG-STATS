"""
Módulo de peticiones a Sofascore utilizando Camoufox (Firefox Anti-Detect / Playwright).
Diseñado para la actualización automática del repositorio en entornos locales y CI/CD (GitHub Actions),
evitando bloqueos de Cloudflare y sin depender de ChromeDriver ni ScraperAPI.
"""
import atexit
import logging
import os
import sys
import time

try:
    from camoufox.sync_api import Camoufox
    HAS_CAMOUFOX = True
except ImportError:
    HAS_CAMOUFOX = False

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

base_url = "https://www.sofascore.com/api/v1"
_camoufox_cm = None
_shared_browser = None
_shared_page = None
_warmed_up = False


def _build_session():
    """Construye e inicializa una instancia optimizada de Camoufox con evasión avanzada."""
    global _camoufox_cm, _shared_browser, _shared_page, _warmed_up
    if not HAS_CAMOUFOX:
        raise RuntimeError("No se encontró la librería 'camoufox'. Instálala con 'pip install camoufox'.")

    # Si ya existía una sesión, limpiarla
    close_driver()

    is_linux = sys.platform.startswith("linux")
    has_display = bool(os.getenv("DISPLAY"))
    # Si estamos en Linux con DISPLAY (ej. xvfb-run en GitHub Actions), usamos headless=False para evasión 100% nativa
    headless_mode = not (is_linux and has_display)

    try:
        mode_str = "Headless" if headless_mode else "Xvfb Display (Headful)"
        logging.info(f"🚀 [Camoufox] Iniciando navegador anti-detección ({mode_str})...")
        _camoufox_cm = Camoufox(
            headless=headless_mode,
            os=("windows", "macos"),
            humanize=True,
            geoip=False
        )
        _shared_browser = _camoufox_cm.__enter__()
        _shared_page = _shared_browser.new_page()
        _warmed_up = False
        logging.info(f"🚀 [Camoufox] Sesión del navegador iniciada exitosamente ({mode_str}).")
        return _shared_page
    except Exception as e:
        logging.error(f"❌ [Camoufox] Error al iniciar Camoufox: {e}")
        close_driver()
        raise


def _wait_for_cloudflare(page, timeout=30):
    """Espera activamente a que se resuelva cualquier desafío de Cloudflare o Turnstile."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            title = (page.title() or "").lower()
            if "just a moment" in title or "attention required" in title or "checking your browser" in title:
                # Intentar interactuar con el checkbox de Turnstile si está presente
                try:
                    for frame in page.frames:
                        try:
                            checkbox = frame.locator('input[type="checkbox"], .ctp-checkbox-label, #challenge-stage')
                            if checkbox.count() > 0:
                                checkbox.first.click(timeout=1000)
                                break
                        except Exception:
                            pass
                except Exception:
                    pass
                time.sleep(1.0)
            else:
                return True
        except Exception:
            time.sleep(1.0)
    return False


def _warmup_session(page):
    """Establece la sesión visitando Sofascore para resolver tokens de Cloudflare y cookies de sesión."""
    global _warmed_up
    if _warmed_up:
        return
    try:
        warmup_url = "https://www.sofascore.com/tournament/football/argentina/liga-profesional-de-futbol/155"
        logging.info(f"🌐 [Camoufox] Inicializando sesión en Sofascore ({warmup_url})...")
        page.goto(warmup_url, timeout=45000)
        
        _wait_for_cloudflare(page, timeout=25)

        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        # Esperar activamente a que las cookies y el entorno de Sofascore estén listos
        for _ in range(10):
            cookies = page.context.cookies()
            cookie_names = {c.get("name") for c in cookies}
            if "browser_data" in cookie_names or len(cookies) >= 8:
                break
            time.sleep(0.5)

        time.sleep(1.5)
        logging.info("✅ [Camoufox] Sesión de Sofascore inicializada y cookies listas.")
        _warmed_up = True
    except Exception as e:
        logging.warning(f"⚠️ [Camoufox] Aviso durante warmup en Sofascore: {e}")
        _warmed_up = True


def get_page():
    """Obtiene o crea la instancia global de la página de Camoufox."""
    global _shared_browser, _shared_page, _warmed_up
    if _shared_page is None or _shared_browser is None:
        _shared_page = _build_session()
        _warmup_session(_shared_page)
    else:
        try:
            if _shared_page.is_closed():
                logging.info("🔄 [Camoufox] Página cerrada. Creando nueva página en el navegador...")
                _shared_page = _shared_browser.new_page()
                _warmup_session(_shared_page)
        except Exception:
            _shared_page = _build_session()
            _warmup_session(_shared_page)
    return _shared_page


def get_driver():
    """Alias compatible con la API anterior."""
    return get_page()


def get_session():
    """Alias para obtener la sesión activa de Camoufox."""
    return get_page()


def ensure_session():
    """Asegura que la sesión esté instanciada y lista."""
    return get_page()


def close_driver():
    """Cierra la sesión de Camoufox y libera memoria RAM y subprocesos."""
    global _camoufox_cm, _shared_browser, _shared_page, _warmed_up
    if _shared_page is not None:
        try:
            if not _shared_page.is_closed():
                _shared_page.close()
        except Exception:
            pass
        _shared_page = None

    if _camoufox_cm is not None:
        try:
            _camoufox_cm.__exit__(None, None, None)
        except Exception:
            pass
        _camoufox_cm = None

    _shared_browser = None
    _warmed_up = False
    logging.info("🛑 [Camoufox] Sesión y navegador cerrados correctamente.")


def close_session():
    """Alias para cerrar la sesión."""
    close_driver()


@atexit.register
def _cleanup():
    close_driver()


def sofa_request(endpoint, params=None, max_retries=3):
    """
    Realiza una petición a la API de Sofascore usando Camoufox.
    Ejecuta un fetch() asíncrono en el contexto del navegador para máxima velocidad,
    preservando la sesión y cookies de Cloudflare en todo momento.
    """
    if endpoint.startswith("/"):
        endpoint = endpoint[1:]

    endpoint_path = f"/api/v1/{endpoint}"
    if params:
        query_string = '&'.join(f"{k}={v}" for k, v in params.items())
        endpoint_path += f"?{query_string}"

    js_fetch = """
    async (url) => {
        try {
            const res = await fetch(url, {
                method: 'GET',
                headers: {
                    'Accept': '*/*',
                    'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
                    'Sec-Fetch-Dest': 'empty',
                    'Sec-Fetch-Mode': 'cors',
                    'Sec-Fetch-Site': 'same-origin',
                    'Cache-Control': 'no-cache',
                    'Pragma': 'no-cache'
                },
                credentials: 'include'
            });
            if (res.status === 404) {
                return { __status: 404, data: {} };
            }
            if (!res.ok) {
                return { __status: res.status, __error: true };
            }
            const data = await res.json();
            return { __status: 200, data: data };
        } catch (err) {
            return { __error: true, message: err.toString() };
        }
    }
    """

    for attempt in range(1, max_retries + 1):
        try:
            page = get_page()
            res = page.evaluate(js_fetch, endpoint_path)

            if isinstance(res, dict):
                if res.get("__status") == 200 and "data" in res:
                    return res["data"]
                elif res.get("__status") == 404:
                    logging.warning(f"⚠️ [Sofascore] 404 Not Found en {endpoint}")
                    return {}
                else:
                    logging.warning(f"⚠️ [Camoufox] Fetch retornó {res.get('__status', 'error')} en {endpoint} (Intento {attempt}/{max_retries}). Revalidando página...")
                    try:
                        page.goto("https://www.sofascore.com/tournament/football/argentina/liga-profesional-de-futbol/155", timeout=35000)
                        _wait_for_cloudflare(page, timeout=20)
                        try:
                            page.wait_for_load_state("networkidle", timeout=10000)
                        except Exception:
                            pass
                        time.sleep(2.0)
                    except Exception:
                        pass
        except Exception as e:
            logging.error(f"❌ [Camoufox] Error consultando {endpoint} (Intento {attempt}/{max_retries}): {e}")
            time.sleep(1.5)

    logging.error(f"❌ [Camoufox] Fallaron todos los {max_retries} intentos para {endpoint}")
    return {}


def get_match_all_heatmaps(match_id, player_ids):
    """
    Descarga concurrentemente los mapas de calor de todos los jugadores de un partido
    mediante Promise.all() en JavaScript para máxima velocidad y eficiencia.
    """
    if not player_ids:
        return {}

    clean_pids = [int(p) for p in player_ids if str(p).isdigit()]
    if not clean_pids:
        return {}

    js_batch = """
    async (params) => {
        const { matchId, playerIds } = params;
        const results = await Promise.all(playerIds.map(async (pid) => {
            const url = `/api/v1/event/${matchId}/player/${pid}/heatmap`;
            try {
                const r = await fetch(url, {
                    method: 'GET',
                    headers: {
                        'Accept': '*/*',
                        'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
                        'Sec-Fetch-Dest': 'empty',
                        'Sec-Fetch-Mode': 'cors',
                        'Sec-Fetch-Site': 'same-origin'
                    },
                    credentials: 'include'
                });
                if (r.ok) {
                    const d = await r.json();
                    return { pid: pid, heatmap: d.heatmap || [] };
                }
                return { pid: pid, heatmap: [] };
            } catch (e) {
                return { pid: pid, heatmap: [] };
            }
        }));
        return results;
    }
    """

    try:
        page = get_page()
        batch_results = page.evaluate(js_batch, {"matchId": match_id, "playerIds": clean_pids})

        normalized = {}
        if isinstance(batch_results, list) and len(batch_results) > 0:
            for item in batch_results:
                pid = item.get("pid")
                heatmap = item.get("heatmap", [])
                normalized[str(pid)] = heatmap
                normalized[int(pid)] = heatmap
            return normalized
    except Exception as e:
        logging.warning(f"⚠️ [Camoufox] Falló batch heatmaps vía JS ({e}). Intentando individualmente...")

    normalized = {}
    for pid in player_ids:
        data = get_match_player_heatmap(match_id, pid)
        heatmap = (data.get("heatmap", []) if data else [])
        normalized[str(pid)] = heatmap
        normalized[int(pid)] = heatmap
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

