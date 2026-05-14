import requests
import pandas as pd
import time
from bs4 import BeautifulSoup
import json
import sqlite3
import os
import logging
from datetime import datetime, timedelta

class FotMob:
    def __init__(self):
        self.session = requests.Session()
        self.base_url = "https://www.fotmob.com"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        }


    def fotmob_request(self, path):
            """
            Realiza la peticion directamente a FotMob gestionando la sesion localmente.
            """
            path = path.lstrip('/')      
            url = f"{self.base_url}/{path}"
            
            try:
                response = self.session.get(url, headers=self.headers, timeout=10)
                
                if response.status_code != 200:
                    print(f"Error en FotMob API: {response.status_code} para la URL: {url}")
                
                # FotMob a veces requiere un pequeño delay para no ser baneado
                time.sleep(1) 
                return response
                
            except Exception as e:
                raise ConnectionError(f"Error al conectar con FotMob: {e}")
            
            
    def request_match_details(self, match_id):
        """Get match details by scraping the match page and parsing __NEXT_DATA__.
        This bypasses the Turnstile protection on the direct API endpoint.

        Args:
            match_id (str): id of a certain match

        Returns:
            MockResponse: An object with a .json() method returning the match details.
        """
        url = f"{self.base_url}/match/{match_id}"
        try:
            response = self.session.get(url, headers=self.headers, timeout=10)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                script = soup.find('script', id='__NEXT_DATA__')
                if script:
                    data = json.loads(script.string)
                    match_details = data.get('props', {}).get('pageProps', {})
                    
                    # Wrap in a MockResponse to maintain compatibility with .json() calls
                    class MockResponse:
                        def __init__(self, data):
                            self.data = data
                            self.status_code = 200
                        def json(self):
                            return self.data
                    
                    return MockResponse(match_details)
            
            print(f"Error al obtener detalles del partido {match_id}: {response.status_code}")
            return None
        except Exception as e:
            print(f"Excepción al obtener detalles del partido {match_id}: {e}")
            return None

    def request_league_details(self):
        url = f"{self.base_url}/leagues/112/overview"
        try:
            response = self.session.get(url, headers=self.headers, timeout=10)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                script = soup.find('script', id='__NEXT_DATA__')
                if script:
                    data = json.loads(script.string)
                    league_details = data.get('props', {}).get('pageProps', {})
                    
                    class MockResponse:
                        def __init__(self, data):
                            self.data = data
                            self.status_code = 200
                        def json(self):
                            return self.data
                    
                    return MockResponse(league_details)
            
            print(f"Error al obtener detalles de la liga: {response.status_code}")
            return None
        except Exception as e:
            print(f"Excepción al obtener detalles de la liga: {e}")
            return None



DB_NAME = "LIGA_ARG_2025.db"
LOG_FILE = "update_log.txt"

# --- CONFIGURACION DE LOGGING ---
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


# --- HELPERS DE PROCESAMIENTO ---

def adjust_utc_to_arg(utc_str):
    """Ajusta fecha UTC a hora local de Argentina."""
    try:
        clean_str = utc_str.replace("Z", "+00:00")
        utc_dt = datetime.fromisoformat(clean_str)
        arg_dt = utc_dt - timedelta(hours=3)
        return arg_dt.strftime("%Y-%m-%d %H:%M:%S")
    except:
        return utc_str

def convert_round_to_number(round_str):
    """Mapeo de rondas eliminatorias a numeros de jornada."""
    mapping = {"1/8": 17, "1/4": 18, "1/2": 19, "Semi-final": 19, "Final": 20}
    return mapping.get(round_str, round_str)

# --- LOGICA DE CARGA DIRECTA (API -> DB) ---

def load_match_directly(match_id, connection):
    """
    Descarga los detalles del partido y los guarda en la DB.
    """
    fm = FotMob()
    cursor = connection.cursor()

    try:
        res_obj = fm.request_match_details(match_id)
        if not res_obj: 
            logging.warning(f"Partido {match_id}: Respuesta vacia o error de scraping.")
            return
        response = res_obj.json()
        
        general = response.get("general", {})
        header = response.get("header", {})
        status = header.get("status", {})
        content = response.get("content", {})
        info_box = content.get("matchFacts", {}).get("infoBox", {})
        
        # 1. Tabla: matches
        match_row = {
            "id": str(match_id),
            "date": adjust_utc_to_arg(general.get("matchTimeUTCDate")),
            "finished": status.get("finished", False),
            "cancelled": status.get("cancelled", False),
            "tournament": str(info_box.get("Tournament", {}).get("leagueName", "")),
            "gameweek": str(convert_round_to_number(general.get("leagueRoundName"))),
            "id_home_team": str(general.get("homeTeam", {}).get("id")),
            "home_team": general.get("homeTeam", {}).get("name"),
            "id_away_team": str(general.get("awayTeam", {}).get("id")),
            "away_team": general.get("awayTeam", {}).get("name"),
            "score": status.get("scoreStr"),
            "referee": info_box.get("Referee", {}).get("text")
        }
        cols = ', '.join(match_row.keys())
        placeholders = ', '.join(['?'] * len(match_row))

        cursor.execute(f"INSERT OR REPLACE INTO matches ({cols}) VALUES ({placeholders})", list(match_row.values()))
        cursor.connection.commit()

        if not status.get("finished", False):
            logging.info(f"Partido {match_id}: Info actualizada (Pendiente).")

            unavailable_players = []
            for side in ["homeTeam", "awayTeam"]:
                if content["lineup"]:
                    team_data = content.get("lineup", {}).get(side, None)
                    for p in team_data.get("unavailable", []):
                        unavailable_players.append({
                                "match_id": str(match_id),
                                "player_id": p.get("id"),
                                "team_id": str(team_data.get("id")),
                                "first_name": p.get("firstName"),
                                "last_name": p.get("lastName"),
                                "position": None,
                                "shirt_number": None,
                                "age":  None,
                                "rating": None,
                                "role_x": None,
                                "role_y": None,
                                "is_starter": False,
                                "minutes_played": None,
                                "substitution": None,
                                "sub_minute": None,
                                "fouls_committed": None,
                                "fouls_received": None,
                                "tackles": None,
                                "offsides": None,
                                "corners": None,
                                "unavailable": True,
                                "unavailability_reason": p.get("unavailability", {}).get("type", None)
                            })
            if unavailable_players:
                cursor.execute("DELETE FROM player_match_details WHERE match_id = ?", (str(match_id),))
                pd.DataFrame(unavailable_players).to_sql("player_match_details", connection, if_exists="append", index=False)
                logging.info(f"\tDetalles de {len(unavailable_players)} jugadores no disponibles cargados.")

            return
        logging.info(f"Partido {match_id}:")

        # 2. Tabla: player_match_details
        lineup = content.get("lineup", {})
        player_stats_map = content.get("playerStats", {})
        events = response.get("content", {}).get("matchFacts", {}).get("events", {}).get("events", [])
        subs = []
        goals = []
        cards = []
        for ev in events:
            if ev.get("type",None) == "Substitution":
                swap = ev.get("swap", [])
                subs.append({
                    "p_in": str(swap[0].get("id")),
                    "p_out": str(swap[1].get("id")),
                    "minute": str(ev.get("timeStr", 0))
                    }) 
                
            if ev.get("type",None) == "Goal":
                goals.append({"id": ev.get("shotmapEvent", {}).get("id"), "assistPlayerId": ev.get("assistPlayerId", None)})

            if  ev.get("type",None) == "Card":
                cards.append(ev)        

        pos_map = {0: "ARQ", 1: "DF", 2: "M", 3: "DL"}
        player_rows = []
        for side in ["homeTeam", "awayTeam"]:
            team_data = lineup.get(side, {})
            team_id = str(team_data.get("id"))

            for section in ["starters", "subs", "unavailable"]:
                for p in team_data.get(section, []):


                    pid = str(p.get("id"))
                    # Extracción de posición y stats básicas
                    p_pos_id = p.get("usualPlayingPositionId", None)
                    
                    p_stat_info = player_stats_map.get(pid, {})

                    all_stats = {}
                    for group in p_stat_info.get("stats", []):
                        for _, item_data in group.get("stats", {}).items():
                            key = item_data.get("key")
                            if key: all_stats[key] = item_data.get("stat", {}).get("value", 0)
                    
                    # Configuración de estadísticas: (Nombre interno, Clave en API)
                    stat_map = {
                        "fouls_committed": "fouls",
                        "fouls_received": "was_fouled",
                        "minutes_played": "minutes_played",
                        "tackles": "matchstats.headers.tackles",
                        "offsides": "Offsides",
                        "corners": "corners"
                    }

                    current_stats = {k: all_stats.get(v, 0) for k, v in stat_map.items()}

                    is_starter = (section == "starters")
                    
                    # Lógica generalizada para limpiar stats
                    if section == "unavailable":
                        for k in current_stats:
                            current_stats[k] = None
                    elif section == "subs":
                        # Si no jugó (minutos 0), limpiar el resto
                        if current_stats["minutes_played"] == 0:
                            for k in current_stats:
                                if k != "minutes_played":
                                    current_stats[k] = None

                    sub_id = None
                    sub_minute = None
                    if not is_starter:
                        for sub in subs:
                            if sub["p_in"] == pid:
                                sub_id = sub["p_out"]
                                sub_minute = sub["minute"]
                                break
                    
                    def safe_int(val):
                        return int(val) if val is not None else None

                    player_rows.append({
                        "match_id": str(match_id),
                        "player_id": pid,
                        "team_id": team_id,
                        "first_name": p.get("firstName"),
                        "last_name": p.get("lastName"),
                        "position": pos_map.get(p_pos_id, None),
                        "shirt_number": p.get("shirtNumber", None),
                        "age": safe_int(p.get("age", None)),
                        "rating": p.get("performance", {}).get("rating", None),
                        "role_x": p.get("verticalLayout", {}).get("x", None),
                        "role_y": p.get("verticalLayout", {}).get("y", None),
                        "is_starter": is_starter,
                        "minutes_played": safe_int(current_stats["minutes_played"]),
                        "substitution": sub_id,
                        "sub_minute": sub_minute,
                        "fouls_committed": safe_int(current_stats["fouls_committed"]),
                        "fouls_received": safe_int(current_stats["fouls_received"]),
                        "tackles": safe_int(current_stats["tackles"]),
                        "offsides": safe_int(current_stats["offsides"]),
                        "corners": safe_int(current_stats["corners"]),
                        "unavailable": section == "unavailable",
                        "unavailability_reason": p.get("unavailability", {}).get("type", None)
                    })
        if player_rows: 
            cursor.execute("DELETE FROM player_match_details WHERE match_id = ?", (str(match_id),))
            pd.DataFrame(player_rows).to_sql("player_match_details", connection, if_exists="append", index=False)
            logging.info(f"\tDetalles de {len(player_rows)} jugadores cargados.")


        # 3. Tabla: shots
        shots_data = content.get("shotmap", {}).get("shots", [])
        

        shot_rows = []
        for s in shots_data:
            if s.get("period", None) == "PenaltyShootout":
                continue

            m_base = s.get("min")
            m_added = s.get("minAdded")
            assist_id = None

            if s.get("eventType","") == "Goal":
                g_id = s.get("id")
                for goal in goals:
                    if goal.get("id","") == g_id:
                        assist_id = goal.get("assistPlayerId", None)

            shot_rows.append({
                "match_id": str(match_id),
                "player_id": str(s.get("playerId")),
                "team_id": str(s.get("teamId")),
                "minute": str(m_base) if m_added is None else f"{m_base} + {m_added}",
                "on_target": s.get("isOnTarget") and not s.get("isBlocked"),
                "shot_type": s.get("shotType", None),
                "situation": s.get("situation", None),
                "outcome": s.get("eventType", None),
                "x": s.get("x", None),
                "y": s.get("y", None),
                "goal_cross_x": s.get("goalCrossedX", None),
                "goal_cross_y": s.get("goalCrossedY", None),
                "blocked_x": s.get("blockedX", None),
                "blocked_y": s.get("blockedY",None),
                "is_blocked": s.get("isBlocked", False),
                "own_goal": s.get("isOwnGoal", False),
                "assist_id": str(assist_id) if assist_id else None,
                "inside_box": s.get("isFromInsideBox")
            })
        if shot_rows:
            cursor.execute("DELETE FROM shots WHERE match_id = ?", (str(match_id),))
            pd.DataFrame(shot_rows).to_sql("shots", connection, if_exists="append", index=False)
            logging.info(f"\tDetalles de {len(shot_rows)} tiros cargados.")

        # 4. Tabla: cards
        general_info = response.get("general", {})
        h_id_card = str(general_info.get("homeTeam", {}).get("id"))
        a_id_card = str(general_info.get("awayTeam", {}).get("id"))

        card_rows = []
        for card in cards:     

                desc = card.get("cardDescription", None)
                if desc and desc.get("localizedKey") == "not_on_pitch":
                    continue

                card_rows.append({
                    "match_id": str(match_id),
                    "player_id": str(card.get("playerId")),
                    "team_id": h_id_card if card.get("isHome") else a_id_card,
                    "card_type":  card.get("card", None),
                    "minute": str(card.get("timeStr"))
                })
        if card_rows: 
            cursor.execute("DELETE FROM cards WHERE match_id = ?", (str(match_id),))
            pd.DataFrame(card_rows).to_sql("cards", connection, if_exists="append", index=False)
            logging.info(f"\tDetalles de {len(card_rows)} tarjetas cargadas.")

        logging.info(f"Partido {match_id}: Actualizacion completa.")

    except Exception as e:
       logging.error(f"Error procesando partido {match_id}: {str(e)}")

def get_playoffs():
    fm = FotMob()
    league = fm.request_league_details().json()
    test = league.get("playoff", {}).get("rounds", [])
    match_ids = []
    for round in league.get("playoff", {}).get("rounds", []):
        for matchup in round.get("matchups", []):
            for match in matchup.get("matches", []):
                match_ids.append(str(match.get("matchId", None)))
    return match_ids





# --- FLUJO PRINCIPAL ---

def get_automated_updates():
    """
    Identifica y actualiza los partidos pendientes de la jornada actual y la siguiente.
    """
    if not os.path.exists(DB_NAME):
        logging.error(f"Base de datos {DB_NAME} no encontrada.")
        return

    conn = sqlite3.connect(DB_NAME, timeout=30)
    conn.row_factory = sqlite3.Row

    try:
        # 1. Obtener la gameweek del ultimo partido finalizado
        last_match=conn.execute('''
            SELECT date, gameweek, tournament
            FROM matches 
            WHERE finished = 1 
            ORDER BY date DESC LIMIT 1
        ''').fetchone()

        next_gameweek = conn.execute('''
            SELECT date, gameweek, tournament
            FROM matches 
            WHERE date > ? AND gameweek != ? AND cancelled = 0
            ORDER BY date ASC LIMIT 1
        ''', (last_match['date'], last_match['gameweek'])).fetchone()

        matches_to_update = conn.execute(f'''
            SELECT id 
            FROM matches
            WHERE (date > ? 
            AND (tournament LIKE ? OR tournament LIKE ?)  
            AND (gameweek = ? OR gameweek = ?)) OR cancelled = 1
            ''',
            (
                last_match['date'],
                f"%{last_match['tournament'].split()[2]}%",
                f"%{next_gameweek['tournament'].split()[2]}%",
                last_match['gameweek'],
                next_gameweek['gameweek']
            )
        ).fetchall()
        

        
        logging.info(f"--- Iniciando ciclo de actualizacion (Jornadas {last_match['gameweek']} y {int(next_gameweek['gameweek'])}) ---")


        match_ids = [row['id'] for row in matches_to_update]
        if(next_gameweek['gameweek'] == '1'):
            logging.info(f"Jornada de playoffs detectada...")
            for id in get_playoffs():
                match_ids.append(id)

        if not match_ids:
            logging.info("Sin partidos pendientes. Todo al dia.")
            return

        for m_id in match_ids:
            load_match_directly(m_id, conn)
            conn.commit() 
            time.sleep(1.5)

    except Exception as e:
        logging.error(f"Error critico en automatizacion: {str(e)}")
    finally:
        conn.close()
        logging.info("--- Fin del ciclo de actualizacion ---")

if __name__ == "__main__":
    get_automated_updates()
