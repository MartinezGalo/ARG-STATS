"""
Módulo de peticiones a Sofascore utilizando ChromeDriver / undetected-chromedriver.
Diseñado para la actualización automática del repositorio en entornos como GitHub Actions
y ejecución automatizada, evitando bloqueos de Cloudflare y sin depender de ScraperAPI.
"""
import atexit
import json
import logging
import os
import re
import subprocess
import sys
import time

try:
    import undetected_chromedriver as uc
    HAS_UC = True
except ImportError:
    HAS_UC = False

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.common.by import By
    HAS_SELENIUM = True
except ImportError:
    HAS_SELENIUM = False

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

base_url = "https://www.sofascore.com/api/v1"
_shared_driver = None
_warmed_up = False


def _get_chrome_major_version():
    """Detecta la versión principal de Google Chrome instalada en el sistema."""
    cmds = []
    if sys.platform == "win32":
        cmds = [
            ['reg', 'query', r'HKEY_CURRENT_USER\Software\Google\Chrome\BLBeacon', '/v', 'version'],
            ['reg', 'query', r'HKEY_LOCAL_MACHINE\SOFTWARE\Google\Chrome\BLBeacon', '/v', 'version'],
        ]
    elif sys.platform == "darwin":
        cmds = [['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '--version']]
    else:
        cmds = [
            ['google-chrome', '--version'],
            ['google-chrome-stable', '--version'],
            ['/usr/bin/google-chrome', '--version'],
            ['/usr/bin/google-chrome-stable', '--version'],
            ['chromium-browser', '--version'],
            ['chromium', '--version'],
        ]

    for cmd in cmds:
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
            match = re.search(r'(\d+)\.\d+\.\d+', out)
            if match:
                return int(match.group(1))
        except Exception:
            continue
    return None


def kill_orphaned_chrome():
    """Elimina procesos huérfanos de Chrome o ChromeDriver."""
    try:
        if sys.platform == "win32":
            cmd = 'powershell -Command "Get-CimInstance Win32_Process -Filter \\"Name = \'chrome.exe\' OR Name = \'chromedriver.exe\'\\" | Where-Object { $_.CommandLine -like \'*headless*\' -or $_.CommandLine -like \'*undetected_chromedriver*\' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"'
            subprocess.run(cmd, shell=True, capture_output=True, timeout=5)
        else:
            cmd = "pkill -f 'chrome.*headless|chromedriver.*undetected_chromedriver'"
            subprocess.run(cmd, shell=True, capture_output=True, timeout=5)
    except Exception:
        pass


def _build_driver():
    """Construye e inicializa una instancia optimizada y ligera de ChromeDriver."""
    kill_orphaned_chrome()
    major_version = _get_chrome_major_version() or 124
    user_agent = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major_version}.0.0.0 Safari/537.36"

    # 1. Intentar con undetected_chromedriver para evadir bloqueos de Cloudflare
    if HAS_UC:
        try:
            chrome_options = uc.ChromeOptions()
            chrome_options.page_load_strategy = 'eager'
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("--blink-settings=imagesEnabled=false")
            chrome_options.add_argument(f'--user-agent={user_agent}')

            driver = uc.Chrome(
                options=chrome_options,
                headless=True,
                version_main=major_version
            )
            logging.info("🚀 [ChromeDriver] Sesión iniciada con undetected-chromedriver (Headless).")
            return driver
        except Exception as e:
            logging.warning(f"⚠️ [ChromeDriver] Falló undetected-chromedriver ({e}). Intentando con Selenium estándar...")

    # 2. Respaldo con Selenium estándar
    if HAS_SELENIUM:
        chrome_options = ChromeOptions()
        chrome_options.page_load_strategy = 'eager'
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--blink-settings=imagesEnabled=false")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_argument(f'--user-agent={user_agent}')

        driver = webdriver.Chrome(options=chrome_options)
        logging.info("🚀 [ChromeDriver] Sesión iniciada con Selenium Chrome estándar (Headless=new).")
        return driver

    raise RuntimeError("No se encontró undetected-chromedriver ni selenium instalados.")


def _warmup_driver(driver):
    """Establece la sesión visitando Sofascore para resolver tokens iniciales de Cloudflare."""
    global _warmed_up
    if _warmed_up:
        return
    try:
        logging.info("🌐 [ChromeDriver] Inicializando sesión en Sofascore...")
        driver.set_page_load_timeout(30)
        driver.set_script_timeout(30)
        driver.get("https://www.sofascore.com/")
        time.sleep(2.0)
        _warmed_up = True
        logging.info("✅ [ChromeDriver] Sesión inicializada correctamente en Sofascore.")
    except Exception as e:
        logging.warning(f"⚠️ [ChromeDriver] Aviso durante warmup en Sofascore: {e}")
        _warmed_up = True


def get_driver():
    """Obtiene o crea la instancia global de ChromeDriver."""
    global _shared_driver, _warmed_up
    if _shared_driver is None:
        _shared_driver = _build_driver()
        _warmup_driver(_shared_driver)
    else:
        try:
            _ = _shared_driver.current_url
        except Exception:
            try:
                _shared_driver.quit()
            except Exception:
                pass
            _warmed_up = False
            _shared_driver = _build_driver()
            _warmup_driver(_shared_driver)
    return _shared_driver


def get_session():
    """Alias para obtener la sesión/driver activo."""
    return get_driver()


def ensure_session():
    """Asegura que el driver esté instanciado y listo."""
    return get_driver()


def close_driver():
    """Cierra el driver de Chrome y libera memoria RAM y procesos huérfanos."""
    global _shared_driver, _warmed_up
    if _shared_driver is not None:
        try:
            _shared_driver.quit()
        except Exception:
            pass
        try:
            if hasattr(_shared_driver, 'service') and hasattr(_shared_driver.service, 'process') and _shared_driver.service.process:
                _shared_driver.service.process.kill()
        except Exception:
            pass
        _shared_driver = None
    _warmed_up = False
    kill_orphaned_chrome()


@atexit.register
def _cleanup():
    close_driver()


def sofa_request(endpoint, params=None, max_retries=3):
    """
    Realiza una petición a la API de Sofascore usando ChromeDriver.
    Ejecuta un fetch() asíncrono en el contexto del navegador para máxima velocidad,
    y utiliza navegación directa como respaldo si es necesario.
    """
    if endpoint.startswith("/"):
        endpoint = endpoint[1:]

    endpoint_path = f"/api/v1/{endpoint}"
    if params:
        query_string = '&'.join(f"{k}={v}" for k, v in params.items())
        endpoint_path += f"?{query_string}"

    target_url = f"https://www.sofascore.com{endpoint_path}"

    for attempt in range(1, max_retries + 1):
        try:
            driver = get_driver()

            # Método 1: Async JavaScript Fetch dentro de la sesión activa de sofascore.com (Ultra-rápido ~50ms)
            js_script = """
            const callback = arguments[arguments.length - 1];
            const url = arguments[0];
            fetch(url, {
                headers: {
                    'Accept': '*/*',
                    'X-Requested-With': 'a25661',
                    'Cache-Control': 'no-cache'
                }
            })
            .then(res => {
                if (res.status === 404) {
                    return { __status: 404, data: {} };
                }
                if (!res.ok) {
                    return { __status: res.status, __error: true };
                }
                return res.json().then(data => ({ __status: 200, data: data }));
            })
            .then(result => callback(result))
            .catch(err => callback({ __error: true, message: err.toString() }));
            """

            res = driver.execute_async_script(js_script, endpoint_path)

            if isinstance(res, dict):
                if res.get("__status") == 200 and "data" in res:
                    return res["data"]
                elif res.get("__status") == 404:
                    logging.warning(f"⚠️ [Sofascore] 404 Not Found en {endpoint}")
                    return {}
                elif res.get("__status") == 403 or res.get("__error"):
                    logging.warning(f"⚠️ [ChromeDriver] Fetch retornó {res.get('__status', 'error')} para {endpoint}. Intentando navegación directa...")

            # Método 2: Navegación directa como respaldo
            driver.get(target_url)
            time.sleep(1.0)

            try:
                page_text = driver.find_element(By.TAG_NAME, "pre").text.strip()
            except Exception:
                try:
                    page_text = driver.find_element(By.TAG_NAME, "body").text.strip()
                except Exception:
                    page_text = ""

            if page_text:
                try:
                    data = json.loads(page_text)
                    return data
                except json.JSONDecodeError:
                    if "Cloudflare" in driver.page_source or "Just a moment" in driver.page_source:
                        logging.warning(f"⚠️ [ChromeDriver] Desafío Cloudflare detectado (Intento {attempt}/{max_retries}). Esperando...")
                        time.sleep(3.0)
                    else:
                        logging.warning(f"⚠️ [ChromeDriver] Respuesta no JSON en {endpoint}: {page_text[:120]}")

        except Exception as e:
            logging.error(f"❌ [ChromeDriver] Error consultando {endpoint} (Intento {attempt}/{max_retries}): {e}")
            time.sleep(1.5)

    logging.error(f"❌ [ChromeDriver] Fallaron todos los {max_retries} intentos para {endpoint}")
    return {}


def get_match_all_heatmaps(match_id, player_ids):
    """
    Descarga concurrentemente los mapas de calor de todos los jugadores de un partido
    mediante Promise.all() en JavaScript para máxima velocidad y eficiencia.
    """
    if not player_ids:
        return {}

    try:
        driver = get_driver()
        js_batch = """
        const callback = arguments[arguments.length - 1];
        const matchId = arguments[0];
        const playerIds = arguments[1];

        Promise.all(playerIds.map(pid => {
            const url = '/api/v1/event/' + matchId + '/player/' + pid + '/heatmap';
            return fetch(url, {
                headers: {
                    'Accept': '*/*',
                    'X-Requested-With': 'a25661',
                    'Cache-Control': 'no-cache'
                }
            })
            .then(r => r.ok ? r.json() : {})
            .then(d => ({ pid: pid, heatmap: d.heatmap || [] }))
            .catch(() => ({ pid: pid, heatmap: [] }));
        }))
        .then(results => callback(results))
        .catch(err => callback([]));
        """

        clean_pids = [int(p) for p in player_ids if str(p).isdigit()]
        batch_results = driver.execute_async_script(js_batch, match_id, clean_pids)

        normalized = {}
        if isinstance(batch_results, list) and len(batch_results) > 0:
            for item in batch_results:
                pid = item.get("pid")
                heatmap = item.get("heatmap", [])
                normalized[str(pid)] = heatmap
                normalized[int(pid)] = heatmap
            return normalized
    except Exception as e:
        logging.warning(f"⚠️ [ChromeDriver] Falló batch heatmaps vía JS ({e}). Intentando individualmente...")

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
