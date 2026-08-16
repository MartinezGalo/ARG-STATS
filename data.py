import sofa_request
import sqlite3
import pandas as pd
import datetime
import math
import sys

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

DB_NAME = "ARGSTATS.db"

ARG_TZ = datetime.timezone(datetime.timedelta(hours=-3))

def adjust_utc_to_arg(timestamp):
    """
    Convierte un timestamp a fecha y hora en zona horaria de Argentina (UTC-3),
    garantizando coherencia sin importar la zona horaria del servidor (GitHub Actions/Ubuntu o Local).
    """
    if timestamp is None:
        return None
    dt_utc = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)
    dt_arg = dt_utc.astimezone(ARG_TZ).replace(tzinfo=None)
    return dt_arg.strftime('%Y-%m-%d %H:%M:%S')

def load_match_data(match_data):
    connection = sqlite3.connect(DB_NAME, timeout=30)
    home_team_name = match_data.get("homeTeam", {}).get("shortName", None) if match_data.get("homeTeam", {}).get("shortName", None) else match_data.get("homeTeam", {}).get("name", None)
    away_team_name = match_data.get("awayTeam", {}).get("shortName", None) if match_data.get("awayTeam", {}).get("shortName", None) else match_data.get("awayTeam", {}).get("name", None)
    if match_data.get("roundInfo", {}).get("cupRoundType", None):
        gameweek = 20 - int(math.log2(match_data.get("roundInfo", {}).get("cupRoundType", 0)))
    else:
        gameweek = match_data.get("roundInfo", {}).get("round", None)

    score = None
    if match_data.get("status", {}).get("type", None) == "finished":
        if match_data.get('homeScore', {}).get('overtime', None):
            score = f"{match_data.get('homeScore', {}).get('normaltime', 0) + match_data.get('homeScore', {}).get('overtime', 0)} - {match_data.get('awayScore', {}).get('normaltime', 0) + match_data.get('awayScore', {}).get('overtime', 0)} "
        else:
            score = f"{match_data.get('homeScore', {}).get('normaltime', 0)} - {match_data.get('awayScore', {}).get('normaltime', 0)}"
        if match_data.get('homeScore', {}).get('penalties', None):
            score = f"({match_data.get('homeScore', {}).get('penalties', 0)}) {score} ({match_data.get('awayScore', {}).get('penalties', 0)})"

    match = {
        "id": match_data.get("id", None),
        "date": adjust_utc_to_arg(match_data.get("startTimestamp", None)),
        "finished": match_data.get("status", {}).get("type", None) == "finished",
        "cancelled": match_data.get("status", {}).get("type", None) == "postponed",
        "tournament": match_data.get("tournament", {}).get("name", None).split(", ")[1] if match_data.get("tournament", {}).get("name", None) else None,
        "gameweek": gameweek,
        "score": score,
        "home_team_id": match_data.get("homeTeam", {}).get("id", None),
        "home_team": home_team_name,
        "away_team_id": match_data.get("awayTeam", {}).get("id", None),
        "away_team": away_team_name,
        "referee_id": match_data.get("referee", {}).get("id", None) if match_data.get("referee", {}) else None,
        "referee": match_data.get("referee", {}).get("name", None) if match_data.get("referee", {}) else None,
    }
    if match:
        connection.cursor().execute("DELETE FROM matches WHERE id = ?", (match.get("id"),))
        pd.DataFrame([match]).to_sql("matches", connection, if_exists="append", index=False)
        if match.get("finished", False) is False:
                print(f"⚠️ El partido {match.get('id')} no ha finalizado. Solo se cargó la info básica.")
                return
    connection.close()
    
def formation_positions(formation, is_home):
    """
    Convierte una formación en una lista de posiciones (x, y) para cada jugador.
    Retorna un array de tuplas donde el índice 0 siempre es el arquero.
    """
    if not formation:
        return []

    positions = []
    rows = formation.split("-")
    
    # 1. ARQUERO: Posición fija bien cerca de la línea de fondo (4%)
    gk_y = 8.0 if is_home else 92.0
    positions.append((50.0, gk_y))

    # 2. JUGADORES DE CAMPO: Distribuimos las líneas entre el 20% y el 50% de la cancha
    y_min = 18.0
    y_max = 45.0
    num_rows = len(rows)
    
    for row_index, players_in_row in enumerate(rows):
        players_in_row = int(players_in_row)
        
        # Calcular Y (Largo de la cancha)
        if num_rows == 1:
            y_base = (y_min + y_max) / 2
        else:
            y_base = y_min + (row_index / (num_rows-1)) * (y_max - y_min)
            
        y = y_base if is_home else 100.0 - y_base
        
        # Calcular X (Ancho de la cancha) dependiendo de la cantidad de jugadores
        for player_index in range(players_in_row):
            if players_in_row == 1:
                x_base = 50.0
            elif players_in_row == 2:
                x_base = 35.0 + player_index * 30.0  # 35% y 65%
            elif players_in_row == 3:
                x_base = 20.0 + (player_index / 2.0) * 60.0  # 20%, 50%, 80%
            elif players_in_row == 4:
                x_base = 12.0 + (player_index / 3.0) * 76.0  # 12%, 37.3%, 62.6%, 88%
            else: 
                # Líneas de 5 o más jugadores
                margin = 8.0
                x_base = margin + (player_index / (players_in_row - 1)) * (100.0 - 2 * margin)
            
            # Invertimos X si es local para mantener coherencia Derecha/Izquierda
            x = 100.0 - x_base if is_home else x_base
            
            positions.append((round(x, 2), round(y, 2)))
            
    return positions

def load_player_match_details(player_match_data, match_id, home_team_id, away_team_id, match_timestamp):
    connection = sqlite3.connect(DB_NAME, timeout=30)

    player_details = []
    for team in ["home", "away"]:
        team_id = home_team_id if team == "home" else away_team_id 
        formation = player_match_data.get(team, {}).get("formation", None)
        positions = formation_positions(formation, is_home=(team == "home"))
        starter_index = 0
        for player in player_match_data.get(team, {}).get("players", []):     
            birthdate_timestamp = player.get("player", {}).get("dateOfBirthTimestamp", None)   
            is_starter = not player.get("substitute", False)
            role_x = None
            role_y = None   
            if is_starter and starter_index < len(positions):
                role_x, role_y = positions[starter_index]
                starter_index += 1
            player_details.append({
                "match_id": match_id,
                "player_id": player.get("player", {}).get("id", None),
                "team_id": team_id,
                "name": player.get("player", {}).get("name", None),
                "short_name": player.get("player", {}).get("shortName", None),
                "position": {"G": "ARQ", "D": "DF", "M": "M", "F": "DL"}.get(player.get("position", None), None),
                "shirt_number": player.get("jerseyNumber", None),
                "age": int((match_timestamp - birthdate_timestamp) / 31557600) if birthdate_timestamp else None,
                "is_starter": is_starter,
                "minutes_played": player.get("statistics", {}).get("minutesPlayed", None),
                "rating": player.get("statistics", {}).get("rating", None),
                "role_x": role_x,
                "role_y": role_y,
                "fouls_committed": player.get("statistics", {}).get("fouls", 0),
                "fouls_received": player.get("statistics", {}).get("wasFouled", 0),
                "tackles": player.get("statistics", {}).get("totalTackle", 0),
                "offsides": player.get("statistics", {}).get("totalOffside", 0),
                "unavailable": False,
                "unavailability_reason": None
            })
        for missing_player in player_match_data.get(team, {}).get("missingPlayers", []):
            missing_id = missing_player.get("player", {}).get("id", None)
            if any(p.get("player_id") == missing_id for p in player_details):            
                continue  # Evitar duplicados si el jugador ya está en la lista

            birthdate_timestamp = missing_player.get("player", {}).get("dateOfBirthTimestamp", None)
            player_details.append({
                "match_id": match_id,
                "player_id": missing_id,
                "team_id": team_id,
                "name": missing_player.get("player", {}).get("name", None),
                "short_name": missing_player.get("player", {}).get("shortName", None),
                "position": {"G": "ARQ", "D": "DF", "M": "M", "F": "DL"}.get(missing_player.get("position", None), None),
                "shirt_number": missing_player.get("jerseyNumber", None),
                "age": int((match_timestamp - birthdate_timestamp) / 31557600) if birthdate_timestamp else None,
                "is_starter": False,
                "minutes_played": None,
                "rating": None,
                "role_x": None,
                "role_y": None,
                "fouls_committed": None ,
                "fouls_received": None,
                "tackles": None,
                "offsides": None,
                "unavailable": True,
                "unavailability_reason": missing_player.get("description", None)
            })
    try:
        connection.cursor().execute('''
            UPDATE matches 
            SET home_team_formation = ?, away_team_formation = ?
            WHERE id = ?
        ''', (player_match_data.get("home", {}).get("formation", None), player_match_data.get("away", {}).get("formation", None), match_id))
    except sqlite3.Error as e:
        print(f"no se actualizo la formación del partido {match_id}: {e}")
    if player_details:
        connection.cursor().execute("DELETE FROM player_match_details WHERE match_id = ?", (match_id,))
        pd.DataFrame(player_details).to_sql("player_match_details", connection, if_exists="append", index=False)
        print(f"✅ Detalles de {len(player_details)} jugadores cargados para el partido {match_id}.")
    connection.close()

def load_match_incidents(incidents_data, match_id, home_team_id, away_team_id):
    connection = sqlite3.connect(DB_NAME, timeout=30)

    cards = []
    goals = []
    substitutions = []
    for incident in incidents_data:
        if incident.get("time", -1) < 0:
            continue  # Ignorar incidentes con tiempo negativo
        time_str = str(incident.get("time", None)) + (" + " +str(incident.get("addedTime")) if incident.get("addedTime", None) else '')

        if incident.get("incidentType") == "card":
            if incident.get("manager") is not None:
                continue  # Ignorar tarjetas a managers
            cards.append({
                "match_id": match_id,
                "player_id": incident.get("player", {}).get("id", None),
                "team_id": home_team_id if incident.get("isHome", None) == True else away_team_id,
                "card_type": incident.get("incidentClass", None),
                "minute": time_str
            })
        elif incident.get("incidentType") == "goal":
            is_home = incident.get("isHome", None) == True
            own_goal = incident.get("incidentClass", None) == "ownGoal"
            if not own_goal:
                team_id = home_team_id if is_home else away_team_id
            else:
                team_id = away_team_id if is_home else home_team_id
            goals.append({
                "match_id": match_id,
                "player_id": incident.get("player", {}).get("id", None),
                "team_id":  team_id,
                "minute": time_str,
                "situation": incident.get("incidentClass", None),
                "is_own_goal": incident.get("incidentClass", None) == "ownGoal",
                "assist_id": incident.get("assist1", {}).get("id", None)
            })
        elif incident.get("incidentType") == "substitution":
            substitutions.append({
                "match_id": match_id,
                "player_out_id": incident.get("playerOut", {}).get("id", None),
                "player_in_id": incident.get("playerIn", {}).get("id", None),
                "team_id": home_team_id if incident.get("isHome", None) == True else away_team_id,
                "minute": time_str,
                "injury": incident.get("injury", False)
            })
    if cards:
        connection.cursor().execute("DELETE FROM cards WHERE match_id = ?", (match_id,))
        pd.DataFrame(cards).to_sql("cards", connection, if_exists="append", index=False)
    if goals:
        connection.cursor().execute("DELETE FROM goals WHERE match_id = ?", (match_id,))
        pd.DataFrame(goals).to_sql("goals", connection, if_exists="append", index=False)
    if substitutions:
        connection.cursor().execute("DELETE FROM substitutions WHERE match_id = ?", (match_id,))
        pd.DataFrame(substitutions).to_sql("substitutions", connection, if_exists="append", index=False)
    print(f"✅ Detalles de {len(cards)} tarjetas, {len(goals)} goles y {len(substitutions)} sustituciones cargadas para el partido {match_id}.")
    connection.close()

def load_match_shots(shotmap_data, match_id, home_team_id, away_team_id):
    connection = sqlite3.connect(DB_NAME, timeout=30)

    shots = []
    for shot in shotmap_data:
        if shot.get("shotType", None) == "goal" and shot.get("goalType", None) == "own":
            continue  # Ignorar goles en contra
        if shot.get("situation", None) == "shootout":
            continue  # Ignorar penales en shootout

        time_str = str(shot.get("time", None)) + (" + " +str(shot.get("addedTime")) if shot.get("addedTime", None) else '')
        is_home = shot.get("isHome", None) == True
        team_id = home_team_id if is_home else away_team_id
        x_raw = shot.get("draw", {}).get("start", {}).get("x", 0)
        y_raw = shot.get("draw", {}).get("start", {}).get("y", 0)
        inside_box = (y_raw < 17) and (12 <= x_raw <= 79)
        #UPDATE shots 
        # SET is_inside_box = 1 
        # WHERE (y < 17) AND (x BETWEEN 11 AND 79);
        shots.append({
            "match_id": match_id,
            "player_id": shot.get("player", {}).get("id", None),
            "team_id": team_id,
            "is_home_team": is_home,
            "minute": time_str,
            "on_target": shot.get("shotType", None) in ["save", "goal"],
            "inside_box": inside_box,
            "shot_type": shot.get("bodyPart", None),
            "situation": shot.get("situation", None),
            "outcome": shot.get("shotType", None),
            "x": 100 - x_raw if not is_home else x_raw,
            "y": 100 - y_raw if is_home else y_raw,
            "goal_cross_x": 100 - shot.get("draw", {}).get("end", {}).get("x", 0) if not is_home else shot.get("draw", {}).get("end", {}).get("x", 0),
            "goal_cross_y": 100 - shot.get("draw", {}).get("end", {}).get("y", 0) if is_home else shot.get("draw", {}).get("end", {}).get("y", 0),
            "blocked_x": 100 - shot.get("draw", {}).get("block", {}).get("x") if not is_home and shot.get("draw", {}).get("block") else (shot.get("draw", {}).get("block", {}).get("x") if shot.get("draw", {}).get("block") else None),
            "blocked_y": 100 - shot.get("draw", {}).get("block", {}).get("y") if is_home and shot.get("draw", {}).get("block") else (shot.get("draw", {}).get("block", {}).get("y") if  shot.get("draw", {}).get("block") else None),
            "is_blocked": shot.get("shotType", None) in ["block", "save"],
        })

    if shots:
        connection.cursor().execute("DELETE FROM shots WHERE match_id = ?", (match_id,))
        pd.DataFrame(shots).to_sql("shots", connection, if_exists="append", index=False)
    print(f"✅ Detalles de {len(shots)} tiros cargados para el partido {match_id}.")
    connection.close()


def load_match_player_heatmap(heatmaps_data, match_id):
    connection = sqlite3.connect(DB_NAME, timeout=30)

    heatmaps = []
    for player in heatmaps_data:
        player_point_count = {}
        if not player.get("heatmap", None):
            continue  # Ignorar jugadores sin puntos de mapa de calor
        for point in player.get("heatmap", []):
            if player_point_count.get((point.get("x", 0), point.get("y", 0)), 0) >= 1:
                player_point_count[(point.get("x", 0), point.get("y", 0))] += 1
            else:
                player_point_count[(point.get("x", 0), point.get("y", 0))] = 1
        for (x, y), count in player_point_count.items():
            heatmaps.append({
                "match_id": match_id,
                "player_id": player.get("playerId", None),
                "team_id": player.get("teamId", None),
                "x": x,
                "y": y,
                "count": count
            })


    if heatmaps:
        connection.cursor().execute("DELETE FROM player_heatmap_points WHERE match_id = ?", (match_id,))
        pd.DataFrame(heatmaps).to_sql("player_heatmap_points", connection, if_exists="append", index=False)
    print(f"✅ Detalles de {len(heatmaps)} puntos de mapa de calor cargados para el partido {match_id}.")
    connection.close()