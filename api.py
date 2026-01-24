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
        self.player_possible_stats = ['goals',
            'goal_assist',
            '_goals_and_goal_assist',
            'rating',
            'goals_per_90',
            'expected_goals',
            'expected_goals_per_90',
            'expected_goalsontarget',
            'ontarget_scoring_att',
            'total_scoring_att',
            'accurate_pass',
            'big_chance_created',
            'total_att_assist',
            'accurate_long_balls',
            'expected_assists',
            'expected_assists_per_90',
            '_expected_goals_and_expected_assists_per_90',
            'won_contest',
            'big_chance_missed',
            'penalty_won',
            'won_tackle',
            'interception',
            'effective_clearance',
            'outfielder_block',
            'penalty_conceded',
            'poss_won_att_3rd',
            'clean_sheet',
            '_save_percentage',
            'saves',
            '_goals_prevented',
            'goals_conceded',
            'fouls',
            'yellow_card',
            'red_card'
        ]

        self.team_possible_stats = ['rating_team',
            'goals_team_match',
            'goals_conceded_team_match',
            'possession_percentage_team',
            'clean_sheet_team',
            'expected_goals_team',
            'ontarget_scoring_att_team',
            'big_chance_team',
            'big_chance_missed_team',
            'accurate_pass_team',
            'accurate_long_balls_team',
            'accurate_cross_team',
            'penalty_won_team',
            'touches_in_opp_box_team',
            'corner_taken_team',
            'expected_goals_conceded_team',
            'interception_team',
            'won_tackle_team',
            'effective_clearance_team',
            'poss_won_att_3rd_team',
            'penalty_conceded_team',
            'saves_team',
            'fk_foul_lost_team',
            'total_yel_card_team',
            'total_red_card_team'
        ]
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

                    player_rows.append({
                        "match_id": str(match_id), "player_id": pid, "team_id": team_id,
                        "player_name": p.get("name"), "position": pos_map.get(p.get("usualPlayingPositionId"), "N/A"),
                        "shirt_number": p.get("shirtNumber"), "rating": p.get("performance", {}).get("rating", 0),
                        "role_x": p.get("verticalLayout", {}).get("y"), "role_y": p.get("verticalLayout", {}).get("x"),
                        "is_starter": (section == "starters"), 
                        "minutes_played": int(all_stats.get("minutes_played", 90 if section == "starters" else 0)),
                        "fouls_committed": int(all_stats.get("fouls", 0)), "fouls_received": int(all_stats.get("was_fouled", 0))
                    })
        if player_rows: 
            pd.DataFrame(player_rows).to_sql("player_match_details", connection, if_exists="append", index=False)
            logging.info(f"\tDetalles de {len(player_rows)} jugadores cargados.")


        # 3. Tabla: shots
        shots_data = content.get("shotmap", {}).get("shots", [])
        shot_rows = [{
            "match_id": str(match_id), "player_id": str(s.get("playerId")), "player_name": s.get("playerName"),
            "team_id": str(s.get("teamId")), "minute": str(s.get("min")),
            "on_target": s.get("isOnTarget") and not s.get("isBlocked"),
            "shot_type": s.get("shotType"), "situation": s.get("situation"),
            "outcome": s.get("eventType"), "inside_box": s.get("isFromInsideBox")
        } for s in shots_data]
        if shot_rows:
            pd.DataFrame(shot_rows).to_sql("shots", connection, if_exists="append", index=False)
            logging.info(f"\tDetalles de {len(shot_rows)} tiros cargados.")

        # 4. Tabla: cards
        events = content.get("matchFacts", {}).get("events", {}).get("events", [])
        general_info = response.get("general", {})
        h_id_card = str(general_info.get("homeTeam", {}).get("id"))
        a_id_card = str(general_info.get("awayTeam", {}).get("id"))

        card_rows = []
        for ev in events:     
            c_type = ev.get("card", None)
            if c_type:

                desc = ev.get("cardDescription", {})
                if desc and desc.get("localizedKey") == "not_on_pitch":
                    continue

                p_obj = ev.get("player", {})
                card_rows.append({
                    "match_id": str(match_id),
                    "player_id": str(p_obj.get("id")),
                    "player_name": p_obj.get("name"),
                    "team_id": h_id_card if ev.get("isHome") else a_id_card,
                    "card_type": c_type,
                    "minute": str(ev.get("timeStr"))
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