import datetime
import logging
import os
import sqlite3
import sys
import time

import data
import sofa_request as sofa

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

DB_NAME = "ARGSTATS.db"
LOG_FILE = "update_log.txt"

# Configuración de Logging
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Logger para consola simultánea
console = logging.StreamHandler()
console.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', '%Y-%m-%d %H:%M:%S')
console.setFormatter(formatter)
logging.getLogger('').addHandler(console)


def update_match(match_id):
    """
    Descarga directamente desde Sofascore los datos del partido, alineaciones,
    incidentes, tiros y mapas de calor, y los inserta en la base de datos ARGSTATS.db.
    """
    try:
        match_res = sofa.get_match_data(match_id)
        if not match_res or not match_res.get("event"):
            logging.warning(f"⚠️ Partido {match_id}: Sin respuesta o datos de evento vacíos.")
            return

        match_data = match_res.get("event")
        status_type = match_data.get("status", {}).get("type", "")
        home_team = match_data.get("homeTeam", {}).get("name", "Local")
        away_team = match_data.get("awayTeam", {}).get("name", "Visitante")
        home_team_id = match_data.get("homeTeam", {}).get("id", None)
        away_team_id = match_data.get("awayTeam", {}).get("id", None)

        # Cargar info básica del partido
        data.load_match_data(match_data)

        if status_type == "postponed":
            logging.info(f"Partido {match_id} ({home_team} vs {away_team}): Postergado.")
            return
        elif status_type != "finished":
            logging.info(f"Partido {match_id} ({home_team} vs {away_team}): No finalizado ({status_type}). Solo info básica actualizada.")
            return

        # Si el partido finalizó, descargamos detalles completos
        player_match_data = sofa.get_match_lineups(match_id) or {}
        incidents_res = sofa.get_match_incidents(match_id) or {}
        incidents_data = incidents_res.get("incidents", None)
        shotmap_res = sofa.get_match_shotmap(match_id) or {}
        shotmap_data = shotmap_res.get("shotmap", None)

        # Mapas de calor en lote
        heatmaps_data = []
        home_players = (player_match_data.get("home") or {}).get("players", []) or []
        away_players = (player_match_data.get("away") or {}).get("players", []) or []
        all_players = home_players + away_players

        active_players = [
            p for p in all_players 
            if (p.get("statistics") or {}).get("minutesPlayed", None) is not None
        ]
        player_ids = [p.get("player", {}).get("id") for p in active_players if p.get("player", {}).get("id")]

        if player_ids:
            batch_heatmaps = sofa.get_match_all_heatmaps(match_id, player_ids)
            for player in active_players:
                player_id = player.get("player", {}).get("id")
                team_id = player.get("teamId", None)
                heatmap_data = batch_heatmaps.get(player_id) or batch_heatmaps.get(str(player_id))
                heatmaps_data.append({"playerId": player_id, "heatmap": heatmap_data, "teamId": team_id})

        # Cargar datos detallados a la base de datos
        data.load_player_match_details(
            player_match_data,
            match_id,
            home_team_id,
            away_team_id,
            match_data.get("startTimestamp", None)
        )

        if incidents_data:
            data.load_match_incidents(incidents_data, match_id, home_team_id, away_team_id)

        if shotmap_data:
            data.load_match_shots(shotmap_data, match_id, home_team_id, away_team_id)

        if heatmaps_data:
            data.load_match_player_heatmap(heatmaps_data, match_id)

        logging.info(f"✅ Partido {match_id} ({home_team} vs {away_team}): Actualizado completamente.")

    except Exception as e:
        logging.error(f"❌ Error al procesar el partido {match_id}: {e}")


def get_match_ids(round, tournament):
    if not round or not tournament:
        return []

    try:
        round = int(round)
    except Exception:
        logging.error(f"Ronda {round} no es un número válido.")
        return []

    if 0 < round < 17:
        tournament_round_data = sofa.get_tournament_round(round=round)
    elif round == 17:
        tournament_round_data = sofa.get_tournament_round_playoff(round=5, slug="round-of-16")
    elif round == 18:
        tournament_round_data = sofa.get_tournament_round_playoff(round=27, slug="quarterfinals")
    elif round == 19:
        tournament_round_data = sofa.get_tournament_round_playoff(round=28, slug="semifinals")
    elif round == 20:
        tournament_round_data = sofa.get_tournament_round_playoff(round=29, slug="final")
    else:
        logging.error(f"Ronda {round} no válida. Debe estar entre 1 y 20.")
        return []

    events = tournament_round_data.get("events", []) if tournament_round_data else []
    if len(events) == 0:
        logging.error(f"⚠️ No se encontraron partidos para la ronda {round}.")
        return []

    match_ids = []

    for event in events:
        tournament_name = event.get("tournament", {}).get("name", '')

        if ("apertura" in tournament_name.lower() and "apertura" in tournament.lower()) or ("clausura" in tournament_name.lower() and "clausura" in tournament.lower()):
            match_id = event.get("id", None)
            status = event.get("status", {}).get("type", None)
            
            if status == "postponed":
                logging.warning(f"⚠️Partido {match_id} postpuesto.")
            elif status == "notstarted":
                logging.warning(f"⚠️Partido {match_id} no iniciado.")
            else:
                logging.info(f"✅Partido {match_id} finalizado.")

            match_ids.append(match_id)
    logging.info(f"Se encontraron {len(match_ids)} partidos para la ronda {round} del torneo {tournament}.")
    return match_ids

def get_automated_updates():
    """
    Identifica la fecha actual (última con partidos finalizados) y la siguiente fecha,
    y actualiza todos sus partidos en la base de datos ARGSTATS.db.
    """
    if not os.path.exists(DB_NAME):
        logging.error(f"Base de datos {DB_NAME} no encontrada.")
        return

    conn = sqlite3.connect(DB_NAME, timeout=30)
    conn.row_factory = sqlite3.Row

    try:
        # 1. Obtener la gameweek del último partido finalizado
        last_match = conn.execute('''
            SELECT id, date, gameweek, tournament 
            FROM matches 
            WHERE finished = 1 
            ORDER BY date DESC LIMIT 1
        ''').fetchone()

        if not last_match:
            logging.warning("No se encontraron partidos finalizados en la base de datos.")
            return
        
        next_match = conn.execute('''
            SELECT id, date, gameweek, tournament 
            FROM matches 
            WHERE date > ? and cancelled = 0
            ORDER BY date ASC LIMIT 1
        ''', (last_match['date'],)).fetchone()

        current_gameweek = last_match['gameweek']
        current_tournament = last_match['tournament']
        next_gameweek = next_match['gameweek'] if next_match else None
        next_tournament = next_match['tournament'] if next_match else None

        logging.info(f"Último partido finalizado: {last_match['date']} | Torneo: {current_tournament} | Fecha: {current_gameweek}")
        logging.info(f"Próximo partido: {next_match['date'] if next_match else 'N/A'} | Torneo: {next_tournament or 'N/A'} | Fecha: {next_gameweek or 'N/A'}")


        # Inicializar sesión de Sofascore antes de iterar
        sofa.get_session()
        gameweeks = []
        if current_gameweek and current_tournament:
            gameweeks.append((current_gameweek, current_tournament))
            gameweeks.append((str(int(current_gameweek)+1), current_tournament))
        if next_gameweek and next_tournament and (next_gameweek, next_tournament) not in gameweeks:
            gameweeks.append((next_gameweek, next_tournament))

        match_ids = []
        for gw, tournament in gameweeks:
            ids = get_match_ids(gw, tournament)
            if ids:
                match_ids.extend(ids)

        total = len(match_ids)

        for idx, m_id in enumerate(match_ids, start=1):
            logging.info(f"[{idx}/{total}] Procesando partido ID: {m_id}...")
            update_match(m_id)
            time.sleep(0.5)

        logging.info(f"✅ Ciclo de actualización completado exitosamente ({total} partidos procesados).")

    except Exception as e:
        logging.error(f"Error crítico en automatización: {e}")
    finally:
        conn.close()
        sofa.close_driver()
        logging.info("--- Fin del ciclo de actualización ---")


if __name__ == "__main__":
    get_automated_updates()
