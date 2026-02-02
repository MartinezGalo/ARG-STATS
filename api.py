import requests
import pandas as pd
import time
import cloudscraper
import sqlite3
import os
import logging
from datetime import datetime, timedelta

class FotMob:
    def __init__(self):
        self.scraper = cloudscraper.create_scraper()
        self.base_url = "https://www.fotmob.com"



    def fotmob_request(self, path):
            """
            Realiza la peticion directamente a FotMob gestionando la sesion localmente.
            """
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
            }
            path = path.lstrip('/')      
            url = f"{self.base_url}/{path}"
            
            try:
                # Usamos el scraper en lugar de requests.get directo
                response = self.scraper.get(url, headers=headers, timeout=10)
                
                if response.status_code != 200:
                    print(f"Error en FotMob API: {response.status_code} para la URL: {url}")
                
                # FotMob a veces requiere un pequeño delay para no ser baneado
                time.sleep(1) 
                return response
                
            except Exception as e:
                raise ConnectionError(f"Error al conectar con FotMob: {e}")
            
            
    def request_match_details(self, match_id):
        """Get match deatils with a request.

        Args:
            match_id (str): id of a certain match, could be found in the URL

        Returns:
            response: json with the response.
        """
        path = f'api/matchDetails?matchId={match_id}'
        response = self.fotmob_request(path)
        return response



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
        response = fm.request_match_details(match_id).json()
        if not response: 
            logging.warning(f"Partido {match_id}: Respuesta vacia de la API.")
            return
        
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

            for section in ["starters", "subs"]:
                for p in team_data.get(section, []):

                    pid = str(p.get("id"))
                    p_stat_info = player_stats_map.get(pid, {})
                    all_stats = {}
                    
                    for group in p_stat_info.get("stats", []):
                        for _, item in group.get("stats", {}).items():
                            if item.get("key"): all_stats[item["key"]] = item.get("stat", {}).get("value", 0)
                    
                    p_minutes = int(all_stats.get("minutes_played", 90 if section == "starters" else 0))
                    
                    # Si es titular pero no hay stats, asumimos 90 si el partido terminó (logica de descarga.py)
                    if section == "starters" and p_minutes == 0 and status.get("finished"):
                        p_minutes = 90

                    is_starter = (section == "starters")
                    
                    # Logic for substitutions
                    sub_id = None
                    sub_minute = None
                    if not is_starter:
                        for sub in subs:
                            if sub["p_in"] == pid:
                                sub_id = sub["p_out"]
                                sub_minute = sub["minute"]
                                break

                    player_rows.append({
                        "match_id": str(match_id),
                        "player_id": pid,
                        "team_id": team_id,
                        "first_name": p.get("firstName"),
                        "last_name": p.get("lastName"),
                        "position": pos_map.get(p.get("usualPlayingPositionId"), "N/A"),
                        "shirt_number": p.get("shirtNumber"),
                        "rating": p.get("performance", {}).get("rating", 0.0),
                        "role_x": p.get("verticalLayout", {}).get("y", 0.0),
                        "role_y": p.get("verticalLayout", {}).get("x", 0.0),
                        "is_starter": is_starter, 
                        "minutes_played": int(p_minutes),
                        "substitution": sub_id,
                        "sub_minute": sub_minute,
                        "fouls_committed": int(all_stats.get("fouls", 0)),
                        "fouls_received": int(all_stats.get("was_fouled", 0))
                    })
        if player_rows: 
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
                "first_name": s.get("firstName"),
                "last_name": s.get("lastName"),
                "team_id": str(s.get("teamId")),
                "minute": str(m_base) if m_added is None else f"{m_base} + {m_added}",
                "on_target": s.get("isOnTarget") and not s.get("isBlocked"),
                "shot_type": s.get("shotType"),
                "situation": s.get("situation"),
                "outcome": s.get("eventType"),
                "own_goal": s.get("isOwnGoal", False),
                "assist_id": str(assist_id) if assist_id else None,
                "inside_box": s.get("isFromInsideBox")
            })
        if shot_rows:
            pd.DataFrame(shot_rows).to_sql("shots", connection, if_exists="append", index=False)
            logging.info(f"\tDetalles de {len(shot_rows)} tiros cargados.")

        # 4. Tabla: cards
        general_info = response.get("general", {})
        h_id_card = str(general_info.get("homeTeam", {}).get("id"))
        a_id_card = str(general_info.get("awayTeam", {}).get("id"))

        card_rows = []
        for card in events:     

                desc = card.get("cardDescription", None)
                if desc and desc.get("localizedKey") == "not_on_pitch":
                    continue

                card_rows.append({
                    "match_id": str(match_id),
                    "player_id": str(card.get("id")),
                    "first_name": card.get("firstName"), "last_name": card.get("lastName"),
                    "team_id": h_id_card if card.get("isHome") else a_id_card,
                    "card_type":  card.get("card", None),
                    "minute": str(card.get("timeStr"))
                })
        if card_rows: 
            pd.DataFrame(card_rows).to_sql("cards", connection, if_exists="append", index=False)
            logging.info(f"\tDetalles de {len(card_rows)} tarjetas cargadas.")

        logging.info(f"Partido {match_id}: Actualizacion completa.")

    except Exception as e:
       logging.error(f"Error procesando partido {match_id}: {str(e)}")

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
        last_gw_row = conn.execute('''
            SELECT gameweek FROM matches 
            WHERE finished = 1 
            ORDER BY date DESC LIMIT 1
        ''').fetchone()

        current_gw = int(last_gw_row['gameweek']) if last_gw_row else 1
        next_gw = current_gw + 1
        
        logging.info(f"--- Iniciando ciclo de actualizacion (Jornadas {current_gw} y {next_gw}) ---")

        # 2. Seleccionar partidos no finalizados
        matches_to_update = conn.execute('''
            SELECT id FROM matches 
            WHERE (gameweek = ? OR gameweek = ?) AND finished = 0
        ''', (str(current_gw), str(next_gw))).fetchall()

        match_ids = [row['id'] for row in matches_to_update]

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