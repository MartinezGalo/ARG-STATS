import sqlite3
import os
import json
import datetime
from flask import Flask, render_template, request, redirect, url_for, jsonify, send_from_directory

app = Flask(__name__)
DB_NAME = "LIGA_ARG_2025.db"

# Diccionario de reemplazo de nombres
TEAM_NAME_MAP = {
    "Club Atletico Platense": "Platense",
    "Central Cordoba de Santiago": "Central Cordoba",
    "Argentinos Juniors": "Argentinos",
    "Atletico Tucuman": "Atl. Tucuman",
    "Defensa y Justicia": "Def. y Justicia",
    "Deportivo Riestra": "Riestra",
    "Independiente Rivadavia": "Ind. Rivadavia",
    "Newell's Old Boys": "Newell's",
    "Velez Sarsfield": "Velez",
    "Estudiantes de Rio Cuarto" : "Est. Rio Cuarto",
    "San Martin San Juan": "San Martin SJ",
    "Barracas Central": "Barracas",
    "Racing Club": "Racing"
}

def get_short_name(full_name):
    """Retorna el nombre corto del equipo si existe en el mapa, sino el original."""
    return TEAM_NAME_MAP.get(full_name, full_name)

def get_weekday(date_str):
    """Convierte una cadena de fecha en el nombre del dia de la semana en español."""
    if not date_str: return ""
    try:
        # Intentar parsear la fecha (asumiendo formato ISO YYYY-MM-DD ...)
        dt = datetime.datetime.fromisoformat(date_str[:10])
        days = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
        return days[dt.weekday()]
    except:
        return ""

# Hacemos disponible la funcion en los templates
app.jinja_env.globals.update(get_short_name=get_short_name, get_weekday=get_weekday)

# --- LoGICA DE BASE DE DATOS Y ESTADiSTICAS ---

def get_db_connection():
    """
    Establece la conexion con la base de datos SQLite.
    row_factory = sqlite3.Row permite acceder a los campos por nombre (ej: fila['id']).
    """
    conn = sqlite3.connect(DB_NAME, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

def init_notes_table():
    """
    Crea las tablas de persistencia para notas de scouting si no existen.
    Asegura que la tabla de partidos tenga la columna 'finished' para diferenciar partidos jugados de pendientes.
    """
    conn = get_db_connection()
    conn.execute('CREATE TABLE IF NOT EXISTS player_notes (player_id TEXT PRIMARY KEY, notes TEXT)')
    conn.execute('CREATE TABLE IF NOT EXISTS match_notes (match_id TEXT PRIMARY KEY, notes TEXT)')
    
    # Views creation
    conn.execute('CREATE VIEW IF NOT EXISTS goals AS SELECT * FROM shots WHERE outcome = "Goal"')
    conn.execute('CREATE VIEW IF NOT EXISTS shots_on_target AS SELECT * FROM shots WHERE on_target = 1')
    conn.execute('''
        CREATE VIEW IF NOT EXISTS shots_received AS 
        SELECT s.*, 
               CASE WHEN s.team_id = m.id_home_team THEN m.id_away_team ELSE m.id_home_team END as against_team_id
        FROM shots s
        JOIN matches m ON s.match_id = m.id
        WHERE s.own_goal = 0
    ''')

    try:
        conn.execute('ALTER TABLE matches ADD COLUMN finished INTEGER DEFAULT 0')
    except:
        pass # La columna ya existe
    conn.commit()
    conn.close()

def get_referee_rankings(order_by='total'):
    """
    Calcula la posicion de cada arbitro en un top basado en el volumen total de eventos.
    Retorna dos diccionarios: {NombreArbitro: PosicionRanking} para tarjetas y faltas.
    """
    conn = get_db_connection()
    
    sort_col = "total" if order_by == 'total' else "avg"
    
    # Ranking por Total de Tarjetas
    ref_cards = conn.execute(f'''
        SELECT m.referee, COALESCE(SUM(CASE WHEN c.card_type = 'Red' THEN 2 WHEN c.card_id IS NOT NULL THEN 1 ELSE 0 END), 0) as total,
        CAST(COALESCE(SUM(CASE WHEN c.card_type = 'Red' THEN 2 WHEN c.card_id IS NOT NULL THEN 1 ELSE 0 END), 0) AS FLOAT) / COUNT(DISTINCT m.id) as avg
        FROM matches m LEFT JOIN cards c ON m.id = c.match_id 
        WHERE m.finished = 1 GROUP BY m.referee ORDER BY {sort_col} DESC
    ''').fetchall()
    # Ranking por Total de Faltas
    ref_fouls = conn.execute(f'''
        SELECT m.referee, SUM(pmd.fouls_committed) as total,
        CAST(SUM(pmd.fouls_committed) AS FLOAT) / COUNT(DISTINCT m.id) as avg
        FROM matches m LEFT JOIN player_match_details pmd ON m.id = pmd.match_id 
        WHERE m.finished = 1 GROUP BY m.referee ORDER BY {sort_col} DESC
    ''').fetchall()
    conn.close()
    return {r['referee']: i+1 for i, r in enumerate(ref_cards)}, {r['referee']: i+1 for i, r in enumerate(ref_fouls)}

def get_referee_detailed_tops():
    """
    Obtiene metricas detalladas (Total, PJ, Promedio) de los arbitros para la pagina /stats.
    Ordena los resultados por el valor Total acumulado.
    """
    conn = get_db_connection()
    ref_c_q = conn.execute('''
        SELECT m.referee as name, COALESCE(SUM(CASE WHEN c.card_type = 'Red' THEN 2 WHEN c.card_id IS NOT NULL THEN 1 ELSE 0 END), 0) as total, COUNT(DISTINCT m.id) as pj,
        CAST(COALESCE(SUM(CASE WHEN c.card_type = 'Red' THEN 2 WHEN c.card_id IS NOT NULL THEN 1 ELSE 0 END), 0) AS FLOAT) / COUNT(DISTINCT m.id) as avg
        FROM matches m LEFT JOIN cards c ON m.id = c.match_id 
        WHERE m.finished = 1 AND m.referee IS NOT NULL GROUP BY m.referee ORDER BY total DESC
    ''').fetchall()
    ref_f_q = conn.execute('''
        SELECT m.referee as name, SUM(pmd.fouls_committed) as total, COUNT(DISTINCT m.id) as pj,
        CAST(SUM(pmd.fouls_committed) AS FLOAT) / COUNT(DISTINCT m.id) as avg
        FROM matches m LEFT JOIN player_match_details pmd ON m.id = pmd.match_id 
        WHERE m.finished = 1 AND m.referee IS NOT NULL GROUP BY m.referee ORDER BY total DESC
    ''').fetchall()
    conn.close()
    return [{"name": r['name'], "total": r['total'], "pj": r['pj'], "avg": round(r['avg'], 2)} for r in ref_c_q], \
           [{"name": r['name'], "total": r['total'], "pj": r['pj'], "avg": round(r['avg'], 2)} for r in ref_f_q]

def get_referee_stats_logic(category='cards', order_by='total', limit=None):
    conn = get_db_connection()
    if limit:
        refs = [r[0] for r in conn.execute('SELECT DISTINCT referee FROM matches WHERE finished=1 AND referee IS NOT NULL').fetchall()]
        results = []
        for ref in refs:
             matches = conn.execute('SELECT id FROM matches WHERE referee = ? AND finished = 1 ORDER BY date DESC LIMIT ?', (ref, limit)).fetchall()
             match_ids = [str(m[0]) for m in matches]
             pj = len(match_ids)
             if pj == 0: continue
             ids_str = ",".join([f"'{m}'" for m in match_ids])
             if category == 'cards':
                 q = f"SELECT COALESCE(SUM(CASE WHEN card_type = 'Red' THEN 2 ELSE 1 END), 0) FROM cards WHERE match_id IN ({ids_str})"
             else:
                 q = f"SELECT SUM(fouls_committed) FROM player_match_details WHERE match_id IN ({ids_str})"
             total = conn.execute(q).fetchone()[0] or 0
             avg = round(total / pj, 2)
             results.append({"name": ref, "total": total, "pj": pj, "avg": avg})
        conn.close()
        key = 'total' if order_by == 'total' else 'avg'
        results.sort(key=lambda x: x[key], reverse=True)
        return results
    else:
        sort_col = "total" if order_by == 'total' else "avg"
        if category == 'cards':
            q = f'''SELECT m.referee as name, COALESCE(SUM(CASE WHEN c.card_type = 'Red' THEN 2 WHEN c.card_id IS NOT NULL THEN 1 ELSE 0 END), 0) as total, COUNT(DISTINCT m.id) as pj, CAST(COALESCE(SUM(CASE WHEN c.card_type = 'Red' THEN 2 WHEN c.card_id IS NOT NULL THEN 1 ELSE 0 END), 0) AS FLOAT) / COUNT(DISTINCT m.id) as avg FROM matches m LEFT JOIN cards c ON m.id = c.match_id WHERE m.finished = 1 AND m.referee IS NOT NULL GROUP BY m.referee ORDER BY {sort_col} DESC'''
        else:
            q = f'''SELECT m.referee as name, SUM(pmd.fouls_committed) as total, COUNT(DISTINCT m.id) as pj, CAST(SUM(pmd.fouls_committed) AS FLOAT) / COUNT(DISTINCT m.id) as avg FROM matches m LEFT JOIN player_match_details pmd ON m.id = pmd.match_id WHERE m.finished = 1 AND m.referee IS NOT NULL GROUP BY m.referee ORDER BY {sort_col} DESC'''
        res = conn.execute(q).fetchall()
        conn.close()
        return [{"name": r['name'], "total": int(r['total'] or 0), "pj": r['pj'], "avg": round(r['avg'] or 0, 2)} for r in res]

def get_last_finished_match_id(team_id):
    """Busca el ID del partido finalizado mas reciente de un equipo para extraer su tactica actual."""
    conn = get_db_connection()
    res = conn.execute('''
        SELECT m.id FROM matches m 
        JOIN player_match_details pmd ON m.id = pmd.match_id 
        WHERE pmd.team_id = ? AND m.finished = 1 AND pmd.role_x IS NOT NULL 
        GROUP BY m.id ORDER BY m.date DESC LIMIT 1
    ''', (str(team_id),)).fetchone()
    conn.close()
    return res['id'] if res else None

def get_lineup_data(match_id, team_id, cards_dict):
    """
    Obtiene titulares y sus posiciones visuales para la pizarra. 
    Normaliza las coordenadas a escala 0-1 e integra las tarjetas del encuentro.
    """
    conn = get_db_connection()
    players = conn.execute('''
        SELECT p.*, 
        EXISTS(SELECT 1 FROM player_notes pn WHERE pn.player_id = p.player_id AND pn.notes IS NOT NULL AND pn.notes != '') as has_note
        FROM player_match_details p 
        WHERE p.match_id = ? AND p.team_id = ? AND p.is_starter = 1 AND p.role_x IS NOT NULL
    ''', (str(match_id), str(team_id))).fetchall()
    conn.close()
    res = []
    for p in players:
        d = dict(p)
        try:
            d['role_x'] = float(d['role_x']) / 100 if float(d['role_x']) > 1 else float(d['role_x'])
            d['role_y'] = float(d['role_y']) / 100 if float(d['role_y']) > 1 else float(d['role_y'])
        except: d['role_x'], d['role_y'] = 0.5, 0.5
        d['card'] = cards_dict.get(str(d['player_id']))
        # Inject player_name for template compatibility
        d['player_name'] = d.get('last_name', '')
        res.append(d)
    return res

def get_team_stats_core(category='shots', filter_type='all', order_by='total', limit=None):
    """
    Funcion unificada que obtiene estadisticas completas de equipos.
    Si `limit` esta presente, calcula las metricas basadas en los ultimos N partidos de cada equipo.
    Retorna dos listas de diccionarios (A favor y En contra).
    """
    conn = get_db_connection()
    # Mapeo de nombres de equipos (id -> nombre)
    teams_map = {str(r['id']): r['name'] for r in conn.execute('''
        SELECT DISTINCT id_home_team as id, home_team as name FROM matches
        UNION ALL
        SELECT DISTINCT id_away_team as id, away_team as name FROM matches
    ''').fetchall()}

    relegated_list = ['10227', '89395']

    # Si no se pide limit, reutilizamos la implementacion previa que usa consultas globales
    if not limit:
        # 1. Calculate PJ for all teams correctly
        pj_map = {}
        matches_all = conn.execute("SELECT id_home_team, id_away_team FROM matches WHERE finished = 1").fetchall()
        for m in matches_all:
            h, a = str(m[0]), str(m[1])
            pj_map[h] = pj_map.get(h, 0) + 1
            pj_map[a] = pj_map.get(a, 0) + 1

        if category == 'shots':
            if filter_type == 'target':
                made_q = "SELECT team_id as rank_team, COUNT(*) as total FROM shots_on_target WHERE own_goal = 0 GROUP BY rank_team"
                against_q = "SELECT (CASE WHEN s.team_id = m.id_home_team THEN m.id_away_team ELSE m.id_home_team END) as rank_team, COUNT(*) as total FROM shots_on_target s JOIN matches m ON s.match_id = m.id WHERE s.own_goal = 0 GROUP BY rank_team"
            else:
                where_f = "AND inside_box = 0" if filter_type == 'long' else ""
                made_q = f"SELECT team_id as rank_team, COUNT(*) as total FROM shots WHERE 1=1 {where_f} AND own_goal = 0 GROUP BY rank_team"
                against_q = f"SELECT (CASE WHEN s.team_id = m.id_home_team THEN m.id_away_team ELSE m.id_home_team END) as rank_team, COUNT(*) as total FROM shots s JOIN matches m ON s.match_id = m.id WHERE 1=1 {where_f} AND s.own_goal = 0 GROUP BY rank_team"

        elif category == 'headers':
            made_q = "SELECT team_id as rank_team, COUNT(*) as total FROM shots WHERE shot_type = 'Header' AND own_goal = 0 GROUP BY rank_team"
            against_q = "SELECT (CASE WHEN s.team_id = m.id_home_team THEN m.id_away_team ELSE m.id_home_team END) as rank_team, COUNT(*) as total FROM shots s JOIN matches m ON s.match_id = m.id WHERE s.shot_type = 'Header' AND s.own_goal = 0 GROUP BY rank_team"

        elif category == 'goals':
            made_q = "SELECT team_id as rank_team, COUNT(*) as total FROM goals GROUP BY rank_team"
            against_q = "SELECT (CASE WHEN s.team_id = m.id_home_team THEN m.id_away_team ELSE m.id_home_team END) as rank_team, COUNT(*) as total FROM goals s JOIN matches m ON s.match_id = m.id GROUP BY rank_team"

        elif category == 'cards':
            made_q = "SELECT team_id as rank_team, COALESCE(SUM(CASE WHEN card_type = 'Red' THEN 2 WHEN card_id IS NOT NULL THEN 1 ELSE 0 END), 0) as total FROM cards GROUP BY rank_team"
            against_q = "SELECT (CASE WHEN c.team_id = m.id_home_team THEN m.id_away_team ELSE m.id_home_team END) as rank_team, COALESCE(SUM(CASE WHEN c.card_type = 'Red' THEN 2 WHEN c.card_id IS NOT NULL THEN 1 ELSE 0 END), 0) as total FROM cards c JOIN matches m ON c.match_id = m.id GROUP BY rank_team"

        elif category == 'fouls':
            made_q = "SELECT team_id as rank_team, SUM(fouls_committed) as total FROM player_match_details GROUP BY rank_team"
            against_q = "SELECT team_id as rank_team, SUM(fouls_received) as total FROM player_match_details GROUP BY rank_team"

        elif category == 'assists':
            made_q = "SELECT team_id as rank_team, COUNT(*) as total FROM goals WHERE assist_id IS NOT NULL AND assist_id != '' GROUP BY rank_team"
            against_q = "SELECT (CASE WHEN g.team_id = m.id_home_team THEN m.id_away_team ELSE m.id_home_team END) as rank_team, COUNT(*) as total FROM goals g JOIN matches m ON g.match_id = m.id WHERE g.assist_id IS NOT NULL AND g.assist_id != '' GROUP BY rank_team"

        res_made = conn.execute(made_q).fetchall()
        res_against = conn.execute(against_q).fetchall()
        conn.close()

        def process_results(query_res):
            data_map = {str(r['rank_team']): int(r['total'] or 0) for r in query_res}
            output = []
            for tid, tname in teams_map.items():
                if tid in relegated_list: continue # Filter relegated
                
                total = data_map.get(tid, 0)
                pj = pj_map.get(tid, 0)
                avg = round(total / pj, 2) if pj > 0 else 0.0
                
                output.append({
                    "id": tid, 
                    "name": tname, 
                    "total": total, 
                    "pj": pj, 
                    "avg": avg
                })
            return output

        made_list = process_results(res_made)
        against_list = process_results(res_against)
        
        # Sort
        key = (lambda x: x['total']) if order_by == 'total' else (lambda x: x['avg'])
        made_list.sort(key=key, reverse=True)
        against_list.sort(key=key, reverse=True)
        
        return made_list, against_list

    # Si se solicita limitar a ultimos N partidos por equipo, hacemos calculo por equipo
    results_made = []
    results_against = []
    team_ids = [t for t in teams_map.keys() if t not in relegated_list]

    for tid in team_ids:
        # Obtener ultimos `limit` partidos finalizados donde participo el equipo
        match_rows = conn.execute('SELECT id FROM matches WHERE (id_home_team = ? OR id_away_team = ?) AND finished = 1 ORDER BY date DESC LIMIT ?', (str(tid), str(tid), limit)).fetchall()
        match_ids = [r[0] for r in match_rows]
        pj = len(match_ids)
        if pj == 0:
            results_made.append({"id": tid, "name": teams_map.get(tid, "N/A"), "total": 0, "pj": 0, "avg": 0})
            results_against.append({"id": tid, "name": teams_map.get(tid, "N/A"), "total": 0, "pj": 0, "avg": 0})
            continue

        ids_str = ",".join([f"'{m}'" for m in match_ids])

        if category == 'shots':
            if filter_type == 'target':
                q_made = f"SELECT COUNT(*) FROM shots_on_target WHERE team_id = ? AND match_id IN ({ids_str}) AND own_goal = 0"
                total_m = conn.execute(q_made, (str(tid),)).fetchone()[0]
                q_against = f"SELECT COUNT(*) FROM shots_on_target s JOIN matches m ON s.match_id = m.id WHERE (CASE WHEN s.team_id = m.id_home_team THEN m.id_away_team ELSE m.id_home_team END) = ? AND s.match_id IN ({ids_str}) AND s.own_goal = 0"
                total_a = conn.execute(q_against, (str(tid),)).fetchone()[0]
            else:
                where_f = "AND inside_box = 0" if filter_type == 'long' else ""
                # A favor: contar eventos del equipo en esos partidos
                q_made = f"SELECT COUNT(*) FROM shots WHERE team_id = ? AND match_id IN ({ids_str}) {where_f} AND own_goal = 0"
                total_m = conn.execute(q_made, (str(tid),)).fetchone()[0]
                # En contra: contar eventos del rival en esos partidos
                q_against = f"SELECT COUNT(*) FROM shots s JOIN matches m ON s.match_id = m.id WHERE (CASE WHEN s.team_id = m.id_home_team THEN m.id_away_team ELSE m.id_home_team END) = ? AND s.match_id IN ({ids_str}) {where_f} AND s.own_goal = 0"
                total_a = conn.execute(q_against, (str(tid),)).fetchone()[0]

        elif category == 'headers':
            q_made = f"SELECT COUNT(*) FROM shots WHERE team_id = ? AND shot_type = 'Header' AND match_id IN ({ids_str}) AND own_goal = 0"
            total_m = conn.execute(q_made, (str(tid),)).fetchone()[0]
            q_against = f"SELECT COUNT(*) FROM shots s JOIN matches m ON s.match_id = m.id WHERE (CASE WHEN s.team_id = m.id_home_team THEN m.id_away_team ELSE m.id_home_team END) = ? AND s.shot_type = 'Header' AND s.match_id IN ({ids_str}) AND s.own_goal = 0"
            total_a = conn.execute(q_against, (str(tid),)).fetchone()[0]

        elif category == 'goals':
            q_made = f"SELECT COUNT(*) FROM goals WHERE team_id = ? AND match_id IN ({ids_str})"
            total_m = conn.execute(q_made, (str(tid),)).fetchone()[0]
            q_against = f"SELECT COUNT(*) FROM goals s JOIN matches m ON s.match_id = m.id WHERE (CASE WHEN s.team_id = m.id_home_team THEN m.id_away_team ELSE m.id_home_team END) = ? AND s.match_id IN ({ids_str})"
            total_a = conn.execute(q_against, (str(tid),)).fetchone()[0]

        elif category == 'cards':
            q_made = f"SELECT COALESCE(SUM(CASE WHEN card_type = 'Red' THEN 2 WHEN card_id IS NOT NULL THEN 1 ELSE 0 END), 0) FROM cards WHERE team_id = ? AND match_id IN ({ids_str})"
            total_m = conn.execute(q_made, (str(tid),)).fetchone()[0]
            q_against = f"SELECT COALESCE(SUM(CASE WHEN c.card_type = 'Red' THEN 2 WHEN c.card_id IS NOT NULL THEN 1 ELSE 0 END), 0) FROM cards c JOIN matches m ON c.match_id = m.id WHERE (CASE WHEN c.team_id = m.id_home_team THEN m.id_away_team ELSE m.id_home_team END) = ? AND c.match_id IN ({ids_str})"
            total_a = conn.execute(q_against, (str(tid),)).fetchone()[0]

        elif category == 'fouls':
            q_made = f"SELECT SUM(pmd.fouls_committed) FROM player_match_details pmd WHERE pmd.team_id = ? AND pmd.match_id IN ({ids_str})"
            total_m = conn.execute(q_made, (str(tid),)).fetchone()[0] or 0
            q_against = f"SELECT SUM(pmd.fouls_received) FROM player_match_details pmd WHERE pmd.team_id = ? AND pmd.match_id IN ({ids_str})"
            total_a = conn.execute(q_against, (str(tid),)).fetchone()[0] or 0

        elif category == 'assists':
            q_made = f"SELECT COUNT(*) FROM goals WHERE team_id = ? AND match_id IN ({ids_str}) AND assist_id IS NOT NULL AND assist_id != ''"
            total_m = conn.execute(q_made, (str(tid),)).fetchone()[0]
            q_against = f"SELECT COUNT(*) FROM goals g JOIN matches m ON g.match_id = m.id WHERE (CASE WHEN g.team_id = m.id_home_team THEN m.id_away_team ELSE m.id_home_team END) = ? AND g.match_id IN ({ids_str}) AND g.assist_id IS NOT NULL AND g.assist_id != ''"
            total_a = conn.execute(q_against, (str(tid),)).fetchone()[0]

        avg_m = round(total_m / pj, 2) if pj > 0 else 0
        avg_a = round(total_a / pj, 2) if pj > 0 else 0

        results_made.append({"id": tid, "name": teams_map.get(tid, "N/A"), "total": int(total_m), "pj": pj, "avg": avg_m})
        results_against.append({"id": tid, "name": teams_map.get(tid, "N/A"), "total": int(total_a), "pj": pj, "avg": avg_a})

    conn.close()

    # Ordenamos por la metrica solicitada
    key = (lambda x: x['total']) if order_by == 'total' else (lambda x: x['avg'])
    results_made.sort(key=key, reverse=True)
    results_against.sort(key=key, reverse=True)

    return results_made, results_against

def _get_stat_sql_config(rank_type, filter_type):
    """Helper para obtener fragmentos SQL segun el tipo de estadistica."""
    base_join = ""
    val_col = ""
    extra_where = ""
    
    if rank_type == 'tiradores' or rank_type == 'shots':
        if filter_type == 'target':
            base_join = "LEFT JOIN shots_on_target s ON pmd.player_id = s.player_id AND pmd.match_id = s.match_id"
            extra_where = "AND s.own_goal = 0"
        else:
            base_join = "LEFT JOIN shots s ON pmd.player_id = s.player_id AND pmd.match_id = s.match_id"
            val_col = "COUNT(s.shot_id)"
            extra_where = "AND s.own_goal = 0"
            if filter_type == 'long': extra_where += " AND s.inside_box = 0"
        val_col = "COUNT(s.shot_id)"
    elif rank_type == 'headers':
        base_join = "LEFT JOIN shots s ON pmd.player_id = s.player_id AND pmd.match_id = s.match_id"
        val_col = "COUNT(s.shot_id)"
        extra_where = "AND s.shot_type = 'Header' AND s.own_goal = 0"
    elif rank_type == 'goals':
        base_join = "LEFT JOIN goals g ON pmd.player_id = g.player_id AND pmd.match_id = g.match_id"
        val_col = "COUNT(g.shot_id)"
    elif rank_type == 'yellows' or rank_type == 'cards':
        base_join = "LEFT JOIN cards c ON pmd.player_id = c.player_id AND pmd.match_id = c.match_id"
        val_col = "COALESCE(SUM(CASE WHEN c.card_type = 'Red' THEN 2 WHEN c.card_id IS NOT NULL THEN 1 ELSE 0 END), 0)"
    elif rank_type == 'assists':
        base_join = "LEFT JOIN goals g ON pmd.player_id = g.assist_id AND pmd.match_id = g.match_id"
        val_col = "COUNT(g.shot_id)"
    elif rank_type == 'fouls':
        val_col = "SUM(pmd.fouls_committed)"
    elif rank_type == 'fouls_rec' or rank_type == 'fouls_received':
        val_col = "SUM(pmd.fouls_received)"
        
    return base_join, val_col, extra_where

def get_rankings_from_stats(category='shots', filter_type='all', order_by='total'):
    """Helper para el predictor: convierte las listas de stats en dicts de ranking {ID: Posicion}"""
    made_list, against_list = get_team_stats_core(category, filter_type, order_by)
    # enumerate genera la posicion basandose en el orden de la consulta SQL
    rank_made = {item['id']: i+1 for i, item in enumerate(made_list)}
    rank_against = {item['id']: i+1 for i, item in enumerate(against_list)}
    return rank_made, rank_against

def get_team_rankings_logic(team_id, rank_type='tiradores', filter_type='all', limit=None, match_id=None, order_by='total', context_match_id=None):
    """
    Ranking de jugadores individuales. 
    Si limit tiene valor (ej: 5), busca solo los ultimos N partidos finalizados del equipo.
    """
    conn = get_db_connection()
    lt_sub = "(SELECT team_id FROM player_match_details pmd2 JOIN matches m2 ON pmd2.match_id = m2.id WHERE pmd2.player_id = pmd.player_id ORDER BY m2.date DESC LIMIT 1)"
    
    unavail_sub = "NULL"
    unavail_param = None
    if context_match_id:
        unavail_sub = "(SELECT unavailability_reason FROM player_match_details WHERE player_id = pmd.player_id AND match_id = ? AND unavailable = 1)"
        unavail_param = str(context_match_id)

    match_filter = ""
    # Determine if we need granular history (breakdown per match)
    include_history = False
    
    if match_id:
        match_filter = f"AND pmd.match_id = '{match_id}'"
        include_history = True
    elif limit:
        match_rows = conn.execute("SELECT id FROM matches WHERE (id_home_team = ? OR id_away_team = ?) AND finished = 1 ORDER BY date DESC LIMIT ?", (str(team_id), str(team_id), limit)).fetchall()
        if match_rows:
            ids_str = ",".join([f"'{mid}'" for mid in [r[0] for r in match_rows]])
            match_filter = f"AND pmd.match_id IN ({ids_str})"
            include_history = True
        else: return []

    join_sql, val_sql, where_sql = _get_stat_sql_config(rank_type, filter_type)
    
    if where_sql:
        join_sql += f" {where_sql}"
        where_sql = ""

    u_map = {"tiradores": "tiros", "shots": "tiros", "goals":"goles", "headers": "cabezazos", "yellows": "tarjetas", "cards": "tarjetas", "fouls": "faltas", "fouls_rec": "faltas rec.", "fouls_received": "recibidas", "assists": "asistencias"}

    if include_history:
        # Granular Query: Group by Player AND Match
        query = f'''
            SELECT pmd.player_id, pmd.match_id, pmd.last_name as player_name, pmd.position, {val_sql} as val, 
            pmd.minutes_played, {lt_sub} as ct,
            (SELECT shirt_number FROM player_match_details pmd3 JOIN matches m3 ON pmd3.match_id = m3.id WHERE pmd3.player_id = pmd.player_id and minutes_played ORDER BY m3.date DESC LIMIT 1) as shirt_number,
            {unavail_sub} as unavail_reason
            FROM player_match_details pmd 
            {join_sql} 
            WHERE pmd.team_id = ? AND pmd.minutes_played > 0 {match_filter} {where_sql}
            GROUP BY pmd.player_id, pmd.match_id
        '''
        
        query_params = []
        if unavail_param: query_params.append(unavail_param)
        query_params.append(str(team_id))
        
        raw_res = conn.execute(query, tuple(query_params)).fetchall()
        conn.close()

        # Aggregation in Python
        players_map = {}
        for r in raw_res:
            pid = r['player_id']
            if pid not in players_map:
                players_map[pid] = {
                    "player_id": pid,
                    "name": r["player_name"],
                    "pos": r["position"],
                    "val": 0,
                    "pj": 0,
                    "unit": u_map.get(rank_type),
                    "is_transferred": str(r["ct"]) != str(team_id),
                    "number": r["shirt_number"],
                    "minutes": 0,
                    "avg": 0.0,
                    "unavail_reason": r["unavail_reason"],
                    "history": {}
                }
            
            p = players_map[pid]
            val = int(r["val"] or 0)
            mins = int(r["minutes_played"] or 0)
            
            p["val"] += val
            p["minutes"] += mins
            p["pj"] += 1
            p["history"][r["match_id"]] = val
        
        # Filter out players with 0 total stats and calculate avg
        output = []
        for p in players_map.values():
            if p["val"] > 0:
                p["avg"] = round((p["val"] / p["minutes"]) * 90, 2) if p["minutes"] > 0 else 0.0
                output.append(p)

    else:
        # Standard Aggregate Query (Season)
        match_filter_sub = match_filter.replace('pmd.', 'pmd2.')
        minutes_sub = f"(SELECT SUM(pmd2.minutes_played) FROM player_match_details pmd2 WHERE pmd2.player_id = pmd.player_id AND pmd2.team_id = ? AND pmd2.minutes_played > 0 {match_filter_sub})"

        query = f'''
            SELECT pmd.player_id, pmd.last_name as player_name, pmd.position, {val_sql} as val, COUNT(DISTINCT pmd.match_id) as pj, {lt_sub} as ct,
            (SELECT shirt_number FROM player_match_details pmd3 JOIN matches m3 ON pmd3.match_id = m3.id WHERE pmd3.player_id = pmd.player_id and minutes_played  ORDER BY m3.date DESC LIMIT 1) as shirt_number,
            {minutes_sub} as minutes_played,
            {unavail_sub} as unavail_reason
            FROM player_match_details pmd 
            {join_sql} 
            WHERE pmd.team_id = ? AND pmd.minutes_played > 0 {match_filter} {where_sql}
            GROUP BY pmd.player_id HAVING val > 0 ORDER BY val DESC
        '''
        
        query_params = []
        query_params.append(str(team_id)) # For minutes_sub
        if unavail_param: query_params.append(unavail_param)
        query_params.append(str(team_id)) # For main WHERE
        
        res = conn.execute(query, tuple(query_params)).fetchall()
        conn.close()
        
        output = []
        for r in res:
            mins = int(r["minutes_played"] or 0)
            val = int(r["val"] or 0)
            avg = round((val / mins) * 90, 2) if mins > 0 else 0.0
            output.append({
                "player_id": r["player_id"], 
                "name": r["player_name"], 
                "pos": r["position"], 
                "val": val, 
                "pj": r["pj"], 
                "unit": u_map.get(rank_type), 
                "is_transferred": str(r["ct"]) != str(team_id),
                "number": r["shirt_number"],
                "minutes": mins,
                "avg": avg,
                "unavail_reason": r["unavail_reason"],
                "history": {} # Empty for season view
            })
    
    if order_by == 'avg':
        output.sort(key=lambda x: x['avg'], reverse=True)
    else:
        output.sort(key=lambda x: x['val'], reverse=True)
        
    return output



def get_prediction_logic(home_id, away_id, category='shots', filter_type='all', referee=None, precalc_ranks=None):
    """
    Motor de prediccion probabilistica. 
    Cruza los rankings de ataque/defensa y aplica la rigurosidad del arbitro en Tarjetas y Faltas.
    Retorna los rankings individuales de cada parte para su visualizacion en la UI.
    """
    if precalc_ranks: m_ranks, a_ranks, ref_ranks = precalc_ranks
    else: m_ranks, a_ranks = get_rankings_from_stats(category, filter_type, order_by= 'avg'); ref_ranks = None
    rm_h = m_ranks.get(str(home_id), 15); ra_h = a_ranks.get(str(home_id), 15)
    rm_v = m_ranks.get(str(away_id), 15); ra_v = a_ranks.get(str(away_id), 15)
    ref_val = None
    if referee and category in ['cards', 'fouls']:
        if not ref_ranks:
            rc, rf = get_referee_rankings(order_by='avg')
            ref_ranks = rc if category == 'cards' else rf
        ref_val = ref_ranks.get(referee, 15)
        h_s = int(((30 - rm_h) + (30 - ra_v) + (30 - ref_val)) / 87 * 100)
        v_s = int(((30 - rm_v) + (30 - ra_h) + (30 - ref_val)) / 87 * 100)
        gen = int(((30 - rm_h) + (30 - ra_h) + (30 - rm_v) + (30 - ra_v) + (30 - ref_val)) / 143 * 100)
    else:
        h_s = int(((30 - rm_h) + (30 - ra_v)) / 58 * 100)
        v_s = int(((30 - rm_v) + (30 - ra_h)) / 58 * 100)
        gen = (h_s + v_s) // 2
    return {"h":  h_s, "v":  v_s, "gen":  gen, "rm_h": rm_h, "ra_h": ra_h, "rm_v": rm_v, "ra_v": ra_v, "ref_rank": ref_val}

def get_team_global_positions(team_id):
    """Calcula rankings detallados (Posicion, Total, PJ) en pares de ataque vs defensa."""
    categories = [
        ('shots', 'all', 'Tiros', 'Tiros Recibidos'),
        ('goals', 'all', 'Goles', 'Goles Recibidos'),
        ('shots', 'target', 'Tiros(arco)', 'Tiros(arco) Recibidos'),
        ('shots', 'long', 'Tiros(lejos)', 'Tiros(lejos) Recibidos'),
        ('headers', 'all', 'Cabezazos', 'Cabezazos Recibidos'),
        ('cards', 'all', 'Tarjetas', 'Tarjetas Generadas'),
        ('fouls', 'all', 'Faltas', 'Faltas Recibidas')
    ]
    
    detailed_ranks = []
    for cat, filt, label_m, label_a in categories:
        made_list, against_list = get_team_stats_core(cat, filt)
        
        # Buscar el equipo en la lista de 'A favor'
        m_stat = next(((i + 1, t) for i, t in enumerate(made_list) if t['id'] == str(team_id)), (None, None))
        # Buscar el equipo en la lista de 'En contra'
        a_stat = next(((i + 1, t) for i, t in enumerate(against_list) if t['id'] == str(team_id)), (None, None))
        
        detailed_ranks.append({
            "made": {"label": label_m, "pos": m_stat[0] or "N/A", "total": m_stat[1]['total'] if m_stat[1] else 0, "pj": m_stat[1]['pj'] if m_stat[1] else 0},
            "against": {"label": label_a, "pos": a_stat[0] or "N/A", "total": a_stat[1]['total'] if a_stat[1] else 0, "pj": a_stat[1]['pj'] if a_stat[1] else 0}
        })
        
    return detailed_ranks

def get_league_player_stats(rank_type='shots', filter_type='all',order_by='total', limit=100):
    conn = get_db_connection()
    # Subconsulta para obtener el nombre del equipo mas reciente del jugador
    team_sub = "(SELECT CASE WHEN pmd2.team_id = m2.id_home_team THEN m2.home_team ELSE m2.away_team END FROM player_match_details pmd2 JOIN matches m2 ON pmd2.match_id = m2.id WHERE pmd2.player_id = pmd.player_id ORDER BY m2.date DESC LIMIT 1)"
    # Subconsulta para obtener el ID del equipo mas reciente
    team_id_sub = "(SELECT pmd2.team_id FROM player_match_details pmd2 JOIN matches m2 ON pmd2.match_id = m2.id WHERE pmd2.player_id = pmd.player_id ORDER BY m2.date DESC LIMIT 1)"
    
    pj = "SELECT player_id, COUNT(DISTINCT pmd.match_id) as pj, SUM(pmd.minutes_played) as minutes_played FROM player_match_details pmd WHERE pmd.minutes_played > 0 GROUP BY player_id"
    order_by_clause = '(pj_table.minutes_played >= 300) DESC, avg' if order_by == 'avg' else 'total'
    
    join_sql, val_sql, where_sql = _get_stat_sql_config(rank_type, filter_type)

    query = f'''
    SELECT pmd.player_id as id, pmd.last_name as name, {team_id_sub} as team_id, {team_sub} as team_name, {val_sql} as total, pj_table.pj as pj, pj_table.minutes_played as minutes_played, (CAST({val_sql} AS FLOAT) / pj_table.minutes_played)*90 as avg 
    FROM player_match_details pmd 
    {join_sql} 
    LEFT JOIN ({pj}) pj_table ON pmd.player_id = pj_table.player_id
    WHERE 1=1 {where_sql} 
    GROUP BY pmd.player_id HAVING total > 0 
    ORDER BY {order_by_clause} DESC LIMIT {limit}'''
    
    res = conn.execute(query).fetchall()
    conn.close()
    return [{"id": r["id"], "name": r["name"], "t_id": r["team_id"], "t_name": r["team_name"], "total": int(r["total"]), "pj": r["pj"], "minutes_played": int(r["minutes_played"]) ,"avg": round(r["avg"], 2)} for r in res]


def get_league_player_stats_last_matches(rank_type='shots', filter_type='all', order_by='total', match_limit=5, limit=100):
    """Calcula estadisticas de jugadores usando los ultimos `match_limit` partidos de cada equipo.

    Para cada equipo, obtenemos sus ultimos `match_limit` partidos finalizados y contamos
    los eventos de los jugadores de ese equipo unicamente en esos partidos. Luego agregamos
    por jugador a nivel de liga para construir el top.
    """
    conn = get_db_connection()
    # Obtener lista de equipos (ids)
    team_rows = conn.execute("SELECT DISTINCT id_home_team as id FROM matches UNION SELECT DISTINCT id_away_team as id FROM matches").fetchall()
    team_ids = [str(r['id']) for r in team_rows if r['id'] is not None]

    player_totals = {}  # pid -> {id, name, t_id, total, pj, minutes_played}

    for tid in team_ids:
        # ultimos match_limit partidos del equipo
        mrows = conn.execute('SELECT id FROM matches WHERE (id_home_team = ? OR id_away_team = ?) AND finished = 1 ORDER BY date DESC LIMIT ?', (str(tid), str(tid), match_limit)).fetchall()
        match_ids = [r[0] for r in mrows]
        if not match_ids:
            continue
        ids_str = ','.join([f"'{m}'" for m in match_ids])

        if rank_type == 'shots':
            if filter_type == 'target':
                q = f"SELECT s.player_id as pid, pmd.last_name as pname, s.team_id as t_id, COUNT(*) as total FROM shots_on_target s JOIN player_match_details pmd ON s.player_id = pmd.player_id AND s.match_id = pmd.match_id WHERE s.team_id = ? AND s.match_id IN ({ids_str}) AND s.own_goal = 0 GROUP BY s.player_id"
            else:
                where_f = "AND inside_box = 0" if filter_type == 'long' else ""
                q = f"SELECT s.player_id as pid, pmd.last_name as pname, s.team_id as t_id, COUNT(*) as total FROM shots s JOIN player_match_details pmd ON s.player_id = pmd.player_id AND s.match_id = pmd.match_id WHERE s.team_id = ? AND s.match_id IN ({ids_str}) {where_f} AND s.own_goal = 0 GROUP BY s.player_id"
            rows = conn.execute(q, (str(tid),)).fetchall()
        elif rank_type == 'headers':
            q = f"SELECT s.player_id as pid, pmd.last_name as pname, s.team_id as t_id, COUNT(*) as total FROM shots s JOIN player_match_details pmd ON s.player_id = pmd.player_id AND s.match_id = pmd.match_id WHERE s.team_id = ? AND s.shot_type = 'Header' AND s.match_id IN ({ids_str}) AND s.own_goal = 0 GROUP BY s.player_id"
            rows = conn.execute(q, (str(tid),)).fetchall()
        elif rank_type == 'goals':
            q = f"SELECT s.player_id as pid, pmd.last_name as pname, s.team_id as t_id, COUNT(*) as total FROM goals s JOIN player_match_details pmd ON s.player_id = pmd.player_id AND s.match_id = pmd.match_id WHERE s.team_id = ? AND s.match_id IN ({ids_str}) GROUP BY s.player_id"
            rows = conn.execute(q, (str(tid),)).fetchall()
        elif rank_type == 'cards':
            q = f"SELECT c.player_id as pid, pmd.last_name as pname, c.team_id as t_id, SUM(CASE WHEN c.card_type = 'Red' THEN 2 WHEN c.card_id IS NOT NULL THEN 1 ELSE 0 END) as total FROM cards c JOIN player_match_details pmd ON c.player_id = pmd.player_id AND c.match_id = pmd.match_id WHERE c.team_id = ? AND c.match_id IN ({ids_str}) GROUP BY c.player_id"
            rows = conn.execute(q, (str(tid),)).fetchall()
        elif rank_type == 'fouls':
            q = f"SELECT pmd.player_id as pid, pmd.last_name as pname, pmd.team_id as t_id, SUM(pmd.fouls_committed) as total FROM player_match_details pmd WHERE pmd.team_id = ? AND pmd.match_id IN ({ids_str}) GROUP BY pmd.player_id"
            rows = conn.execute(q, (str(tid),)).fetchall()
        elif rank_type in ('fouls_rec', 'fouls_received'):
            q = f"SELECT pmd.player_id as pid, pmd.last_name as pname, pmd.team_id as t_id, SUM(pmd.fouls_received) as total FROM player_match_details pmd WHERE pmd.team_id = ? AND pmd.match_id IN ({ids_str}) GROUP BY pmd.player_id"
            rows = conn.execute(q, (str(tid),)).fetchall()
        elif rank_type == 'assists':
            q = f"SELECT g.assist_id as pid, pmd.last_name as pname, g.team_id as t_id, COUNT(*) as total FROM goals g JOIN player_match_details pmd ON g.assist_id = pmd.player_id AND g.match_id = pmd.match_id WHERE g.team_id = ? AND g.match_id IN ({ids_str}) AND g.assist_id IS NOT NULL AND g.assist_id != '' GROUP BY g.assist_id"
            rows = conn.execute(q, (str(tid),)).fetchall()
        else:
            rows = []

        for r in rows:
            pid = str(r['pid'])
            total = int(r['total'] or 0)
            pj = conn.execute(f'SELECT COUNT(DISTINCT pmd.match_id) as pj FROM player_match_details pmd WHERE pmd.player_id = ? AND pmd.team_id = ? AND pmd.match_id IN ({ids_str}) AND pmd.minutes_played > 0' , (pid, str(tid))).fetchone()['pj'] or 0
            minutes_played = conn.execute(f'SELECT SUM(pmd.minutes_played) as mp FROM player_match_details pmd WHERE pmd.player_id = ? AND pmd.team_id = ? AND pmd.match_id IN ({ids_str}) AND pmd.minutes_played > 0' , (pid, str(tid))).fetchone()['mp'] or 0 
            if pid not in player_totals:
                player_totals[pid] = {'id': pid, 'name': r['pname'] if 'pname' in r.keys() and r['pname'] is not None else '', 't_id': r['t_id'] if 't_id' in r.keys() and r['t_id'] is not None else str(tid), 'total': 0, 'pj': 0, 'minutes_played': 0}
            player_totals[pid]['total'] += total
            player_totals[pid]['pj'] += pj
            player_totals[pid]['minutes_played'] += minutes_played

    
    out = []
    for pid, v in player_totals.items():
        if v['pj'] == 0: continue
        team_info_row = conn.execute('SELECT pmd2.team_id, CASE WHEN pmd2.team_id = m2.id_home_team THEN m2.home_team ELSE m2.away_team END as team_name FROM player_match_details pmd2 JOIN matches m2 ON pmd2.match_id = m2.id WHERE pmd2.player_id = ? ORDER BY m2.date DESC LIMIT 1', (pid,)).fetchone()
        team_name = team_info_row['team_name'] if team_info_row else ''
        team_id = team_info_row['team_id'] if team_info_row else v['t_id']
        
        avg = round((v['total'] / v['minutes_played'])*90, 2) if v['minutes_played'] > 0 else 0
        out.append({'id': v['id'], 'name': v['name'], 't_id': str(team_id), 't_name': team_name, 'total': v['total'], 'pj': v['pj'], 'minutes_played': v['minutes_played'], 'avg': avg})

    conn.close()
    if order_by == 'total':
        out.sort(key=lambda x: x[order_by], reverse=True)
    else:
        out.sort(key=lambda x: (x['minutes_played'] >= 150, x[order_by]), reverse=True)
    
    return out[:limit]

# --- RUTAS ---

@app.route('/favicon.ico')
@app.route('/lpf.png')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'lpf.png', mimetype='image/png')
@app.route('/')
def index():
    """Panel principal. Procesa los partidos de la jornada seleccionada."""
    conn = get_db_connection()
    years = [r[0] for r in conn.execute("SELECT DISTINCT strftime('%Y', date) as y FROM matches ORDER BY y DESC").fetchall()]
    year = request.args.get('year'); tournament = request.args.get('tournament'); gameweek = request.args.get('gameweek')
    if year is None or tournament is None or gameweek is None:
        next_m = conn.execute("SELECT strftime('%Y', date) as y, tournament, gameweek FROM matches WHERE finished = 0 ORDER BY date ASC LIMIT 1").fetchone()
        if next_m:
            year, tournament, gameweek = next_m[0], next_m[1], next_m[2]
            if "Apertura" in tournament: tournament = "Liga Profesional Apertura"
            elif "Clausura" in tournament: tournament = "Liga Profesional Clausura"
        else: year = year or (years[0] if years else "2025"); tournament = tournament or "Liga Profesional Apertura"; gameweek = gameweek or "1"
    matches_raw = conn.execute("SELECT * FROM matches WHERE strftime('%Y', date) = ? AND gameweek = ? AND tournament LIKE ? ORDER BY date ASC", (str(year), str(gameweek), f'%{tournament}%')).fetchall()
    # Rankings rapidos para las tarjetas del index
    rs_m, rs_a = get_rankings_from_stats('shots', order_by='avg')
    rh_m, rh_a = get_rankings_from_stats('headers', order_by='avg')
    rc_m, rc_a = get_rankings_from_stats('cards', order_by='avg')
    rf_m, rf_a = get_rankings_from_stats('fouls', order_by='avg')    
    ref_c, ref_f = get_referee_rankings(order_by='avg')
    matches = []
    for m in matches_raw:
        row = dict(m)
        ps = get_prediction_logic(row['id_home_team'], row['id_away_team'], 'shots', precalc_ranks=(rs_m, rs_a, None))
        ph = get_prediction_logic(row['id_home_team'], row['id_away_team'], 'headers', precalc_ranks=(rh_m, rh_a, None))
        pc = get_prediction_logic(row['id_home_team'], row['id_away_team'], 'cards', referee=row['referee'], precalc_ranks=(rc_m, rc_a, ref_c))
        pf = get_prediction_logic(row['id_home_team'], row['id_away_team'], 'fouls', referee=row['referee'], precalc_ranks=(rf_m, rf_a, ref_f))
        row['preds'] = { 's_home': ps['h'], 's_away': ps['v'], 's_gen': ps['gen'], 'h_home': ph['h'], 'h_away': ph['v'], 'h_gen': ph['gen'], 'c_home': pc['h'], 'c_away': pc['v'], 'c_gen': pc['gen'],'c_ref': pc['ref_rank'], 'f_home': pf['h'], 'f_away': pf['v'], 'f_gen': pf['gen'], 'f_ref': pf['ref_rank']}
        matches.append(row)

    sort_by = request.args.get('sort_by')
    if sort_by:
        key_map = {'shots': 's', 'headers': 'h', 'fouls': 'f', 'cards': 'c'}
        prefix = key_map.get(sort_by)
        if prefix:
            matches.sort(key=lambda x: max(x['preds'][f'{prefix}_home'], x['preds'][f'{prefix}_away'], x['preds'][f'{prefix}_gen']), reverse=True)

    conn.close()
    return render_template('index.html', matches=matches, years=years, current_year=year, current_tournament=tournament, current_gameweek=gameweek, current_sort=sort_by)
#STATS
@app.route('/stats')
def stats_page():
    return render_template('stats.html', team_map=json.dumps(TEAM_NAME_MAP))

#MATCH DETAIL
@app.route('/match/<match_id>')
def match_detail(match_id):
    """Analisis profundo con pizarra y predicciones"""
    conn = get_db_connection()
    match = conn.execute('SELECT * FROM matches WHERE id = ?', (str(match_id),)).fetchone()
    m_note = conn.execute('SELECT notes FROM match_notes WHERE match_id = ?', (str(match_id),)).fetchone()
    if not match: return "No existe", 404

    sf = request.args.get('shot_filter', 'all')
    pred_s = get_prediction_logic(match['id_home_team'], match['id_away_team'], 'shots', sf)
    pred_h = get_prediction_logic(match['id_home_team'], match['id_away_team'], 'headers')
    pred_c = get_prediction_logic(match['id_home_team'], match['id_away_team'], 'cards', referee=match['referee'])
    pred_f = get_prediction_logic(match['id_home_team'], match['id_away_team'], 'fouls', referee=match['referee'])

    cards_dict = {str(r['player_id']): r['card_type'] for r in conn.execute('SELECT player_id, card_type FROM cards WHERE match_id = ?', (str(match_id),)).fetchall()}

    h_mid = match_id if match['finished'] == 1 else get_last_finished_match_id(match['id_home_team'])
    a_mid = match_id if match['finished'] == 1 else get_last_finished_match_id(match['id_away_team'])

    # Fetch all players for substitution name resolution
    m_ids = {str(match_id)}
    if h_mid: m_ids.add(str(h_mid))
    if a_mid: m_ids.add(str(a_mid))
    
    home_lineup = get_lineup_data(h_mid, match['id_home_team'], cards_dict) if h_mid else []
    away_lineup = get_lineup_data(a_mid, match['id_away_team'], cards_dict) if a_mid else []

    def process_subs(rows):
        res = []
        for r in rows:
            d = dict(r)
            d['player_name'] = d.get('last_name', '')
            res.append(d)
        return sorted(res, key=lambda x: {"ARQ":0,"DF":1,"M":2,"DL":3}.get(x['position'],99))

    home_subs = process_subs(conn.execute('SELECT * FROM player_match_details WHERE match_id=? AND team_id=? AND is_starter=0 AND unavailable=0', (str(h_mid or match_id), str(match['id_home_team']))).fetchall())
    away_subs = process_subs(conn.execute('SELECT * FROM player_match_details WHERE match_id=? AND team_id=? AND is_starter=0 AND unavailable=0', (str(a_mid or match_id), str(match['id_away_team']))).fetchall())

    stats = {
        "home": {"shots": 0, "target": 0, "outside": 0, "headers": 0, "fouls": 0, "corners": 0, "offsides": 0, "tackles": 0, "yellows": 0, "reds": 0},
        "away": {"shots": 0, "target": 0, "outside": 0, "headers": 0, "fouls": 0, "corners": 0, "offsides": 0, "tackles": 0, "yellows": 0, "reds": 0}
    }
    
    if match['finished'] == 1:
        # Shots summaries
        for r in conn.execute('SELECT team_id, COUNT(*) as tot FROM shots WHERE match_id=? AND own_goal=0 GROUP BY team_id', (str(match_id),)).fetchall():
            k = "home" if str(r['team_id']) == str(match['id_home_team']) else "away"; stats[k]["shots"] = r['tot']
        
        for r in conn.execute('SELECT team_id, COUNT(*) as tar FROM shots_on_target WHERE match_id=? AND own_goal=0 GROUP BY team_id', (str(match_id),)).fetchall():
            k = "home" if str(r['team_id']) == str(match['id_home_team']) else "away"; stats[k]["target"] = r['tar']

        for r in conn.execute('SELECT team_id, COUNT(*) as outs FROM shots WHERE match_id=? AND own_goal=0 AND inside_box=0 GROUP BY team_id', (str(match_id),)).fetchall():
            k = "home" if str(r['team_id']) == str(match['id_home_team']) else "away"; stats[k]["outside"] = r['outs']

        for r in conn.execute('SELECT team_id, COUNT(*) as heads FROM shots WHERE match_id=? AND own_goal=0 AND shot_type="Header" GROUP BY team_id', (str(match_id),)).fetchall():
            k = "home" if str(r['team_id']) == str(match['id_home_team']) else "away"; stats[k]["headers"] = r['heads']

        # Player stats summary
        for r in conn.execute('SELECT team_id, SUM(fouls_committed) as f, SUM(corners) as c, SUM(offsides) as o, SUM(tackles) as t FROM player_match_details WHERE match_id=? GROUP BY team_id', (str(match_id),)).fetchall():
            k = "home" if str(r['team_id']) == str(match['id_home_team']) else "away"
            stats[k]["fouls"] = r['f'] or 0
            stats[k]["corners"] = r['c'] or 0
            stats[k]["offsides"] = r['o'] or 0
            stats[k]["tackles"] = r['t'] or 0

        # Cards
        for r in conn.execute('SELECT team_id, card_type, COUNT(*) as tot FROM cards WHERE match_id=? GROUP BY team_id, card_type', (str(match_id),)).fetchall():
            k = "home" if str(r['team_id']) == str(match['id_home_team']) else "away"
            if r['card_type'] == 'Yellow': stats[k]["yellows"] += r['tot']
            else: stats[k]["reds"] += r['tot']

    # Unavailable players
    unavail_home = conn.execute('SELECT first_name ,last_name, unavailability_reason as reason FROM player_match_details WHERE match_id=? AND team_id=? AND unavailable=1', (str(match_id), str(match['id_home_team']))).fetchall()
    unavail_away = conn.execute('SELECT first_name , last_name, unavailability_reason as reason FROM player_match_details WHERE match_id=? AND team_id=? AND unavailable=1', (str(match_id), str(match['id_away_team']))).fetchall()

    # H2H: Partidos previos entre estos dos equipos
    h2h_matches = conn.execute('''
        SELECT id, date, tournament, home_team, away_team, score, id_home_team, id_away_team
        FROM matches
        WHERE ((id_home_team = ? AND id_away_team = ?) OR (id_home_team = ? AND id_away_team = ?))
          AND finished = 1 AND id != ?
        ORDER BY date DESC LIMIT 5
    ''', (str(match['id_home_team']), str(match['id_away_team']), str(match['id_away_team']), str(match['id_home_team']), str(match_id))).fetchall()

    # Ultimos 5 partidos de cada equipo (para contexto del ranking)
    def get_last_5_context(tid):
        rows = conn.execute('SELECT id, id_home_team, id_away_team, home_team, away_team, score FROM matches WHERE (id_home_team = ? OR id_away_team = ?) AND finished = 1 ORDER BY date DESC LIMIT 5', (str(tid), str(tid))).fetchall()
        res = []
        for r in rows:
            is_home = str(r['id_home_team']) == str(tid)
            rival_id = r['id_away_team'] if is_home else r['id_home_team']
            rival_name = r['away_team'] if is_home else r['home_team']
            
            # Determinar resultado
            res_val = 'D'
            if r['score'] and '-' in r['score']:
                try:
                    h_s, a_s = map(int, r['score'].split('-'))
                    if h_s == a_s: res_val = 'D'
                    elif (is_home and h_s > a_s) or (not is_home and a_s > h_s): res_val = 'W'
                    else: res_val = 'L'
                except: pass
                
            res.append({'rival_id': rival_id, 'rival_name': rival_name, 'cond': 'L' if is_home else 'V', 'id': str(r['id']), 'score': r['score'], 'result': res_val})
        return res

    l5_home = get_last_5_context(match['id_home_team'])
    l5_away = get_last_5_context(match['id_away_team'])
    
    # Contexto del "Ultimo Partido" (para cuando el actual esta pendiente)
    def get_single_context(mid, tid):
        if not mid: return None
        r = conn.execute('SELECT id, id_home_team, id_away_team, home_team, away_team, score FROM matches WHERE id = ?', (str(mid),)).fetchone()
        if not r: return None
        is_home = str(r['id_home_team']) == str(tid)
        rival_id = r['id_away_team'] if is_home else r['id_home_team']
        rival_name = r['away_team'] if is_home else r['home_team']
        
        res_val = 'D'
        if r['score'] and '-' in r['score']:
            try:
                h_s, a_s = map(int, r['score'].split('-'))
                if h_s == a_s: res_val = 'D'
                elif (is_home and h_s > a_s) or (not is_home and a_s > h_s): res_val = 'W'
                else: res_val = 'L'
            except: pass
            
        return {'rival_id': rival_id, 'rival_name': rival_name, 'cond': 'L' if is_home else 'V', 'id': str(mid), 'score': r['score'], 'result': res_val}

    last_match_home = get_single_context(h_mid, match['id_home_team']) if match['finished'] == 0 else None
    last_match_away = get_single_context(a_mid, match['id_away_team']) if match['finished'] == 0 else None

    # Historial del Arbitro con estos equipos
    ref_history = []
    if match['referee']:
        # Buscar partidos de este arbitro dirigiendo a CUALQUIERA de los dos equipos
        ref_matches_raw = conn.execute('''
            SELECT m.id, m.date, m.tournament, m.home_team, m.away_team, m.id_home_team, m.id_away_team, m.score
            FROM matches m
            WHERE m.referee = ?
              AND (m.id_home_team IN (?, ?) OR m.id_away_team IN (?, ?))
              AND m.finished = 1 AND m.id != ?
            ORDER BY m.date DESC LIMIT 10
        ''', (match['referee'], str(match['id_home_team']), str(match['id_away_team']), str(match['id_home_team']), str(match['id_away_team']), str(match_id))).fetchall()

        for m in ref_matches_raw:
            mid = str(m['id'])
            # Faltas por equipo
            f_h = conn.execute('SELECT SUM(fouls_committed) FROM player_match_details WHERE match_id=? AND team_id=?', (mid, str(m['id_home_team']))).fetchone()[0] or 0
            f_v = conn.execute('SELECT SUM(fouls_committed) FROM player_match_details WHERE match_id=? AND team_id=?', (mid, str(m['id_away_team']))).fetchone()[0] or 0
            # Tarjetas por equipo
            c_h = conn.execute("SELECT COALESCE(SUM(CASE WHEN card_type = 'Red' THEN 2 WHEN card_id IS NOT NULL THEN 1 ELSE 0 END), 0) FROM cards WHERE match_id=? AND team_id=?", (mid, str(m['id_home_team']))).fetchone()[0]
            c_v = conn.execute("SELECT COALESCE(SUM(CASE WHEN card_type = 'Red' THEN 2 WHEN card_id IS NOT NULL THEN 1 ELSE 0 END), 0) FROM cards WHERE match_id=? AND team_id=?", (mid, str(m['id_away_team']))).fetchone()[0]
            
            ref_history.append({
                'date': m['date'], 'match_id': m['id'], 
                'home_team': m['home_team'], 'away_team': m['away_team'], 
                'score': m['score'], 'tournament': m['tournament'],
                'stats': {'h_cards': c_h, 'h_fouls': f_h, 'v_cards': c_v, 'v_fouls': f_v},
                'id_home_team': m['id_home_team'], 'id_away_team': m['id_away_team']
            })

    # GOLES DEL PARTIDO
    match_goals = []
    if match['finished'] == 1:
        goals_data = conn.execute('''
            SELECT g.minute, g.team_id, g.own_goal, pmd.first_name, pmd.last_name,
                   (SELECT last_name FROM player_match_details WHERE player_id = g.assist_id AND match_id = g.match_id) as assist_name 
            FROM goals g
            LEFT JOIN player_match_details pmd ON g.player_id = pmd.player_id AND g.match_id = pmd.match_id
            WHERE g.match_id = ? ORDER BY CAST(g.minute as INTEGER) ASC
        ''', (str(match_id),)).fetchall()
        for g in goals_data:
            tid = str(g['team_id'])
            # Si no hay player_details (ej. gol en contra sin player linkeado), usar "Gol en Contra" o similar si fuera necesario.
            # Asumimos que siempre hay link o es NULL
            scorer = g['last_name'] if g['last_name'] else "Desconocido"
            
            if g['own_goal']:
                scorer += " (EC)"
                # Asignar al equipo contrario
                if tid == str(match['id_home_team']):
                    tid = str(match['id_away_team'])
                else:
                    tid = str(match['id_home_team'])
            
            match_goals.append({
                'minute': g['minute'], 'team_id': tid, 
                'scorer': scorer, 'assist': g['assist_name']
            })

    # SHOTMAP DATA
    match_shots = []
    if match['finished'] == 1:
        shots_rows = conn.execute('''
            SELECT s.*, pmd.last_name as player_name 
            FROM shots s 
            LEFT JOIN player_match_details pmd ON s.player_id = pmd.player_id AND s.match_id = pmd.match_id
            WHERE s.match_id = ? 
        ''', (str(match_id),)).fetchall()
        
        for s in shots_rows:
            
            match_shots.append({
                "x": s['y'], "y": s['x'], 
                "blocked_x": s['blocked_y'],
                "blocked_y": s['blocked_x'],
                "goal_cross_x": s['goal_cross_y'], 
                "goal_cross_y": s['goal_cross_x'], 
                "is_blocked": s['is_blocked'],
                "own_goal": s['own_goal'],
                "outcome": s['outcome'],
                "situation": s['situation'],
                "team_id": str(s['team_id']),
                "player_name": s['player_name'] or "Desconocido",
                "on_target": s['on_target'],
                "minute": s['minute']
            })

    # Find Previous and Next Match
    prev_match = conn.execute('SELECT * FROM matches WHERE date < ? OR (date = ? AND id < ?) ORDER BY date DESC, id DESC LIMIT 1', (match['date'], match['date'], str(match_id))).fetchone()
    next_match = conn.execute('SELECT * FROM matches WHERE date > ? OR (date = ? AND id > ?) ORDER BY date ASC, id ASC LIMIT 1', (match['date'], match['date'], str(match_id))).fetchone()

    conn.close()
    return render_template('detail.html', match=match, prev_match=prev_match, next_match=next_match, home_lineup=home_lineup, away_lineup=away_lineup, home_subs=home_subs, away_subs=away_subs, home_top=get_team_rankings_logic(match['id_home_team']), away_top=get_team_rankings_logic(match['id_away_team']), stats=stats, m_note=m_note, pred_s=pred_s, pred_h=pred_h, pred_c=pred_c, pred_f=pred_f, lineup_label="Formacion" if match['finished'] else "ultimo 11", current_filter=sf, h2h_matches=h2h_matches, ref_history=ref_history, l5_home=l5_home, l5_away=l5_away, last_match_home=last_match_home, last_match_away=last_match_away, h_mid=h_mid, a_mid=a_mid, match_goals=match_goals, match_shots=match_shots, unavail_home=unavail_home, unavail_away=unavail_away)

@app.route('/api/team_ranking/<team_id>')
def api_team_ranking(team_id):
    limit = request.args.get('limit', type=int)
    match_id = request.args.get('match_id') # Capturamos el match_id
    context_match_id = request.args.get('context_match_id') # Nuevo
    order_by = request.args.get('order_by', 'total')
    return jsonify(get_team_rankings_logic(
        team_id, 
        request.args.get('type', 'tiradores'), 
        request.args.get('filter', 'all'), 
        limit,
        match_id,
        order_by,
        context_match_id
    ))

@app.route('/api/team_stats')
def api_team_stats():
    """Devuelve estadisticas de equipos por categoria/side. Parametros: category, filter, side (made|against), limit (opcional)."""
    category = request.args.get('category', 'shots')
    filter_type = request.args.get('filter', 'all')
    side = request.args.get('side', 'made')
    limit = request.args.get('limit', type=int)
    order_by = request.args.get('order_by', 'total')
    made, against = get_team_stats_core(category, filter_type, order_by=order_by, limit=limit)
    data = made if side == 'made' else against
    return jsonify(data)


@app.route('/api/player_stats')
def api_player_stats():
    """Devuelve estadisticas de jugadores. Parametros: rank_type, filter, limit_matches (opcional).
       Si se pasa `limit_matches`, calcula metricas usando solo los ultimos N partidos por jugador.
    """
    rank_type = request.args.get('rank_type', 'shots')
    filter_type = request.args.get('filter', 'all')
    limit_matches = request.args.get('limit_matches', type=int)
    order_by = request.args.get('order_by', 'total')
    if limit_matches:
        limit = request.args.get('limit', type=int) or 100
        data = get_league_player_stats_last_matches(rank_type, filter_type, order_by=order_by, match_limit=limit_matches, limit=limit)
    else:
        limit = request.args.get('limit', type=int) or 100
        data = get_league_player_stats(rank_type, filter_type, order_by=order_by, limit=limit)
    return jsonify(data)

@app.route('/api/referee_stats')
def api_referee_stats():
    category = request.args.get('category', 'cards')
    limit = request.args.get('limit', type=int)
    order_by = request.args.get('order_by', 'total')
    return jsonify(get_referee_stats_logic(category, order_by, limit))

@app.route('/player_info/<player_id>/<match_id>')
def player_info(player_id, match_id):
    conn = get_db_connection()
    
    # 1. Info basica
    info = conn.execute('''
        SELECT pmd.*, m.home_team, m.away_team, m.id_home_team, m.id_away_team 
        FROM player_match_details pmd 
        JOIN matches m ON pmd.match_id = m.id 
        WHERE pmd.player_id = ? and minutes_played
        ORDER BY m.date DESC LIMIT 1
    ''', (player_id,)).fetchone()

    teams_history = [r['team_id'] for r in conn.execute('SELECT DISTINCT team_id FROM player_match_details WHERE player_id = ?', (player_id,)).fetchall()]

    if not info: 
        conn.close()
        return jsonify({"error": "No data"}), 404

    # Helper para stats
    def get_stats_summary(m_ids):
        if not m_ids: return {}
        ids_str = ",".join([f"'{i}'" for i in m_ids])
        s = conn.execute(f'''
            SELECT 
                COUNT(*) as pj, SUM(minutes_played) as mins,
                SUM(fouls_committed) as f_c, SUM(fouls_received) as f_r,
                (SELECT COUNT(*) FROM shots WHERE player_id = ? AND match_id IN ({ids_str}) AND own_goal=0) as shots,
                (SELECT COUNT(*) FROM shots_on_target WHERE player_id = ? AND match_id IN ({ids_str}) AND own_goal=0) as target,
                (SELECT COUNT(*) FROM shots WHERE player_id = ? AND match_id IN ({ids_str}) AND inside_box=0 AND own_goal=0) as long,
                (SELECT COUNT(*) FROM shots WHERE player_id = ? AND match_id IN ({ids_str}) AND shot_type='Header' AND own_goal=0) as headers,
                (SELECT COALESCE(SUM(CASE WHEN card_type = 'Red' THEN 2 WHEN card_id IS NOT NULL THEN 1 ELSE 0 END), 0) FROM cards WHERE player_id = ? AND match_id IN ({ids_str})) as cards
            FROM player_match_details WHERE player_id = ? AND match_id IN ({ids_str})
        ''', (player_id, player_id, player_id, player_id, player_id, player_id)).fetchone()
        return dict(s)

    # Logica de Rankings (Top 20)
    def get_top_rankings():
        # Definimos los componentes de cada metrica
        # Estructura: (Etiqueta, Tabla/Join, Funcion Agregada, Filtro extra)
        metrics = [
            ("Tiros Totales", "shots s JOIN player_match_details p ON s.player_id = p.player_id AND s.match_id = p.match_id", "COUNT(*)", ["s.own_goal=0"]),
            ("Tiros al Arco", "shots_on_target s JOIN player_match_details p ON s.player_id = p.player_id AND s.match_id = p.match_id", "COUNT(*)", ["s.own_goal=0"]),
            ("Tiros Lejanos", "shots s JOIN player_match_details p ON s.player_id = p.player_id AND s.match_id = p.match_id", "COUNT(*)", ["s.inside_box=0", "s.own_goal=0"]),
            ("Faltas Cometidas", "player_match_details p", "SUM(p.fouls_committed)", []),
            ("Faltas Recibidas", "player_match_details p", "SUM(p.fouls_received)", []),
            ("Tarjetas", "cards c JOIN player_match_details p ON c.player_id = p.player_id AND c.match_id = p.match_id", "COALESCE(SUM(CASE WHEN c.card_type = 'Red' THEN 2 WHEN c.card_id IS NOT NULL THEN 1 ELSE 0 END), 0)", []),
            ("Cabezazos", "shots s JOIN player_match_details p ON s.player_id = p.player_id AND s.match_id = p.match_id", "COUNT(*)", ["s.shot_type='Header'", "s.own_goal=0"])
        ]
        
        scopes = {
            "liga": "1=1",
            "equipo": f"p.team_id = '{info['team_id']}'",
            "posicion": f"p.position = '{info['position']}'"
        }

        results = {"liga": [], "equipo": [], "posicion": []}

        for scope_name, scope_filter in scopes.items():
            for label, table_clause, agg_func, metric_filters in metrics:
                # Construccion limpia de la clausula WHERE
                where_conditions = [scope_filter] + metric_filters
                where_clause = " WHERE " + " AND ".join(where_conditions)
                
                # Query final limpia
                query = f"""
                    SELECT p.player_id, {agg_func} as val 
                    FROM {table_clause} 
                    {where_clause} 
                    GROUP BY p.player_id 
                    ORDER BY val DESC
                """
                
                res = conn.execute(query).fetchall()
                
                # Buscamos al jugador en el ranking
                for i, r in enumerate(res):
                    pos = i + 1
                    if pos > 20: break # Limite Top 20 solicitado
                    
                    if str(r[0]) == str(player_id):
                        results[scope_name].append({
                            "label": label,
                            "pos": pos,
                            "total": int(r['val'])
                        })
                        break
        return results    
    match_stats = get_stats_summary([match_id])
    
    # Contexto Equipo: Buscar team_id en el partido actual o usar el del ultimo partido jugado
    ctx_team = conn.execute('SELECT team_id FROM player_match_details WHERE match_id=? AND player_id=?', (str(match_id), str(player_id))).fetchone()
    tid_l5 = str(ctx_team['team_id']) if ctx_team else str(info['team_id'])

    # Ultimos 5 partidos DEL EQUIPO (jugados o no por el player)
    team_l5_rows = conn.execute('SELECT id, date, home_team, away_team, id_home_team, id_away_team FROM matches WHERE (id_home_team = ? OR id_away_team = ?) AND finished = 1 ORDER BY date DESC LIMIT 5', (tid_l5, tid_l5)).fetchall()
    last_5_ids = [str(r['id']) for r in team_l5_rows]
    l5_stats = get_stats_summary(last_5_ids)

    # Detalles visuales para la modal (Rival, Minutos)
    l5_details = []
    for m in team_l5_rows:
        mr = conn.execute('SELECT minutes_played FROM player_match_details WHERE match_id=? AND player_id=?', (str(m['id']), str(player_id))).fetchone()
        mins = mr['minutes_played'] if mr else 0
        is_home = str(m['id_home_team']) == tid_l5
        rival = m['away_team'] if is_home else m['home_team']
        rival_id = m['id_away_team'] if is_home else m['id_home_team']
        l5_details.append({"date": m['date'], "rival": rival, "rival_id": rival_id, "minutes": mins, "cond": 'L' if is_home else 'V', "match_id": str(m['id'])})
    
    all_ids = [r[0] for r in conn.execute('SELECT match_id FROM player_match_details WHERE player_id=? and minutes_played > 0', (player_id,)).fetchall()]
    gen_stats = get_stats_summary(all_ids)
    
    rankings_top = get_top_rankings()
    note = conn.execute('SELECT notes FROM player_notes WHERE player_id = ?', (player_id,)).fetchone()

    conn.close()

    return jsonify({
        "team_id": str(info['team_id']),
        "name": f"{info['first_name']} {info['last_name']}",
        "team": info["home_team"] if str(info["team_id"]) == str(info["id_home_team"]) else info["away_team"],
        "pos": "Delantero" if info["position"] == "DL" else "Mediocampista" if info["position"] == "M" else "Defensor" if info["position"] == "DF" else "Arquero" if info["position"] == "ARQ" else "Desconocido",
        "number": info["shirt_number"],
        "age": info["age"],
        "teams_history": teams_history,
        "stats": {"partido": match_stats, "l5": l5_stats, "general": gen_stats},
        "last_5_details": l5_details,
        "rankings_top": rankings_top,
        "notes": note["notes"] if note else ""
    })


@app.route('/save_player_note/<player_id>', methods=['POST'])
def save_player_note(player_id):
    conn = get_db_connection(); conn.execute('INSERT OR REPLACE INTO player_notes (player_id, notes) VALUES (?, ?)', (str(player_id), request.form.get('notes'))); conn.commit(); conn.close(); return "OK"

@app.route('/save_match_note/<match_id>', methods=['POST'])
def save_match_note(match_id):
    conn = get_db_connection(); conn.execute('INSERT OR REPLACE INTO match_notes (match_id, notes) VALUES (?, ?)', (str(match_id), request.form.get('notes'))); conn.commit(); conn.close(); return redirect(url_for('match_detail', match_id=match_id))

@app.route('/api/match_prediction/<match_id>')
def api_match_prediction(match_id):
    conn = get_db_connection()
    match = conn.execute('SELECT id_home_team, id_away_team, referee FROM matches WHERE id = ?', (str(match_id),)).fetchone()
    conn.close()
    if not match: return jsonify({"error": "N/A"}), 404
    ft = request.args.get('shot_filter', 'all')
    return jsonify({
        "shots": get_prediction_logic(match['id_home_team'], match['id_away_team'], 'shots', ft),
        "headers": get_prediction_logic(match['id_home_team'], match['id_away_team'], 'headers'),
        "cards": get_prediction_logic(match['id_home_team'], match['id_away_team'], 'cards', referee=match['referee']),
        "fouls": get_prediction_logic(match['id_home_team'], match['id_away_team'], 'fouls', referee=match['referee'])
    })

@app.route('/api/match_heatmap/<match_id>')
def api_match_heatmap(match_id):
    conn = get_db_connection()
    match = conn.execute('SELECT id_home_team, id_away_team FROM matches WHERE id = ?', (str(match_id),)).fetchone()
    if not match: return jsonify({"error": "Match not found"}), 404
    
    home_id = str(match['id_home_team'])
    away_id = str(match['id_away_team'])
    limit = request.args.get('limit', type=int)

    def get_shots(team_id, is_home , type_shot, limit_n=None):
        # Determinamos la condicion de busqueda segun si es realizado o recibido
        if type_shot == 'made':
            where = f"s.team_id = {team_id}"
        else:
            where = f"(m.id_home_team = {team_id} OR m.id_away_team = {team_id}) AND s.team_id != {team_id}"
        
        limit_sql = ""
        if limit_n:
            sub_q = "SELECT id FROM matches WHERE (id_home_team = ? OR id_away_team = ?) AND finished = 1 ORDER BY date DESC LIMIT ?"
            m_rows = conn.execute(sub_q, (team_id, team_id, limit_n)).fetchall()
            if not m_rows: return []
            ids = ",".join([f"'{r[0]}'" for r in m_rows])
            limit_sql = f"AND s.match_id IN ({ids})"
        
        # Obtenemos las coordenadas y si el que pateo era visitante en ese partido para normalizar
        query = f"""
            SELECT s.x as y, s.y as x, (m.id_home_team = {team_id}) as was_home, inside_box
            FROM shots s
            JOIN matches m ON s.match_id = m.id
            WHERE {where} {limit_sql} AND s.own_goal = 0 AND s.x IS NOT NULL AND s.y IS NOT NULL
        """
        
        rows = conn.execute(query).fetchall()
        return [{"x": r['x'], "y": r['y'], "inside_box": bool(r['inside_box'])} for r in rows]

    data = {
        "home_made": get_shots(home_id, is_home= True, type_shot='made', limit_n=limit),
        "home_received": get_shots(home_id, is_home= True, type_shot='received', limit_n=limit),
        "away_made": get_shots(away_id, is_home= False, type_shot='made', limit_n=limit),
        "away_received": get_shots(away_id, is_home= False, type_shot='received', limit_n=limit)
    }
    conn.close()
    return jsonify(data)

@app.route('/search_players/<team_id>')
def search_players(team_id):
    q = request.args.get('q', '')
    conn = get_db_connection()
    # Busca jugadores unicos por nombre que hayan jugado en ese equipo
    players = conn.execute('''
        SELECT player_id, first_name, last_name, position, shirt_number
        FROM (
            SELECT 
                pmd.player_id, 
                pmd.first_name, 
                pmd.last_name, 
                pmd.position, 
                pmd.shirt_number,
                ROW_NUMBER() OVER (
                    PARTITION BY pmd.player_id 
                    ORDER BY m.date DESC
                ) as rn
            FROM player_match_details pmd
            JOIN matches m ON pmd.match_id = m.id
            WHERE pmd.team_id = ? 
            AND (pmd.first_name || ' ' || pmd.last_name) LIKE ?
            AND pmd.unavailable = 0
        )
        WHERE rn = 1
        LIMIT 8
    ''', (str(team_id), f'%{q}%')).fetchall()
    conn.close()
    res = []
    for p in players:
        d = dict(p)
        d['player_name'] = f"{d['first_name']} {d['last_name']}"
        d['last_name'] = d['last_name']
        d['number'] = d['shirt_number']
        d['id'] = str(d['player_id'])
        res.append(d)
    return jsonify(res)

@app.route('/team/<team_id>')
def team_page(team_id):
    conn = get_db_connection()
    # Obtener nombre del equipo
    team_name = conn.execute('SELECT home_team FROM matches WHERE id_home_team = ? UNION SELECT away_team FROM matches WHERE id_away_team = ? LIMIT 1', (str(team_id), str(team_id))).fetchone()
    if not team_name: return "Equipo no encontrado", 404
    
    # Historial de partidos (Finalizados)
    matches_finished = conn.execute('''
        SELECT * FROM matches 
        WHERE (id_home_team = ? OR id_away_team = ?) AND finished = 1
        ORDER BY date DESC
    ''', (str(team_id), str(team_id))).fetchall()

    # Partidos Proximos (Pendientes)
    matches_upcoming = conn.execute('''
        SELECT * FROM matches 
        WHERE (id_home_team = ? OR id_away_team = ?) AND finished = 0
        ORDER BY date ASC
    ''', (str(team_id), str(team_id))).fetchall()
    
    global_ranks = get_team_global_positions(team_id)
    conn.close()
    
    return render_template('team.html', 
                                  team_id=team_id, 
                                  team_name=team_name[0], 
                                  matches_finished=matches_finished,
                                  matches_upcoming=matches_upcoming,
                                  global_ranks=global_ranks)


@app.route('/referee/<name>')
def referee_page(name):
    conn = get_db_connection()
    
    # 1. Historial de partidos con stats de tarjetas y faltas
    matches_raw = conn.execute('''
        SELECT m.* FROM matches m WHERE m.referee = ? AND m.finished = 1 ORDER BY m.date DESC
    ''', (name,)).fetchall()
    
    matches = []
    total_cards_acc, total_fouls_acc = 0, 0
    
    for m in matches_raw:
        mid = str(m['id'])
        # Faltas por equipo
        f_h = conn.execute('SELECT SUM(fouls_committed) FROM player_match_details WHERE match_id=? AND team_id=?', (mid, str(m['id_home_team']))).fetchone()[0] or 0
        f_v = conn.execute('SELECT SUM(fouls_committed) FROM player_match_details WHERE match_id=? AND team_id=?', (mid, str(m['id_away_team']))).fetchone()[0] or 0
        # Tarjetas por equipo
        c_h = conn.execute("SELECT COALESCE(SUM(CASE WHEN card_type = 'Red' THEN 2 WHEN card_id IS NOT NULL THEN 1 ELSE 0 END), 0) FROM cards WHERE match_id=? AND team_id=?", (mid, str(m['id_home_team']))).fetchone()[0]
        c_v = conn.execute("SELECT COALESCE(SUM(CASE WHEN card_type = 'Red' THEN 2 WHEN card_id IS NOT NULL THEN 1 ELSE 0 END), 0) FROM cards WHERE match_id=? AND team_id=?", (mid, str(m['id_away_team']))).fetchone()[0]
        
        row = dict(m)
        row['stats'] = {'h_fouls': f_h, 'v_fouls': f_v, 'h_cards': c_h, 'v_cards': c_v}
        matches.append(row)
        total_cards_acc += (c_h + c_v)
        total_fouls_acc += (f_h + f_v)

    # 2. Rankings Globales de arbitros
    rc, rf = get_referee_rankings()
    ranks = {'cards': rc.get(name, "N/A"), 'fouls': rf.get(name, "N/A")}

    # 3. Equipos mas castigados (Top Targets)
    # Mapping de IDs a Nombres para el arbitraje
    t_map = {str(r['id']): r['name'] for r in conn.execute('SELECT DISTINCT id_home_team as id, home_team as name FROM matches').fetchall()}

    def get_top_teams(metric_type):
        if metric_type == 'cards':
            q = "SELECT team_id, COALESCE(SUM(CASE WHEN card_type = 'Red' THEN 2 WHEN card_id IS NOT NULL THEN 1 ELSE 0 END), 0) as tot, COUNT(DISTINCT match_id) as pj FROM cards WHERE match_id IN (SELECT id FROM matches WHERE referee=?) GROUP BY team_id ORDER BY tot DESC LIMIT 5"
        elif metric_type == 'fouls_committed':
            q = 'SELECT team_id, SUM(fouls_committed) as tot, COUNT(DISTINCT match_id) as pj FROM player_match_details WHERE match_id IN (SELECT id FROM matches WHERE referee=?) GROUP BY team_id ORDER BY tot DESC LIMIT 5'
        else: # fouls_received
            q = 'SELECT team_id, SUM(fouls_received) as tot, COUNT(DISTINCT match_id) as pj FROM player_match_details WHERE match_id IN (SELECT id FROM matches WHERE referee=?) GROUP BY team_id ORDER BY tot DESC LIMIT 5'
        
        res = conn.execute(q, (name,)).fetchall()
        return [{"id": str(r[0]), "name": t_map.get(str(r[0]), "N/A"), "total": r[1], "pj": r[2]} for r in res]

    top_targets = {
        "cards": get_top_teams('cards'),
        "fouls_committed": get_top_teams('fouls_committed'),
        "fouls_received": get_top_teams('fouls_received')
    }

    # Promedios
    pj_total = len(matches) if matches else 1
    stats_avg = {
        "cards": round(total_cards_acc / pj_total, 2),
        "fouls": round(total_fouls_acc / pj_total, 2)
    }

    conn.close()
    return render_template('referee.html', ref_name=name, matches=matches, ranks=ranks, top_targets=top_targets, stats_avg=stats_avg)



if __name__ == '__main__':
    init_notes_table()
    
    is_render = os.environ.get("RENDER", False)
    
    if is_render:
        # Configuracion para Render
        port = int(os.environ.get("PORT", 5000))
        app.run(host='0.0.0.0', port=port)
    else:
        # Configuracion para LOCAL
        print("--- CORRIENDO EN MODO LOCAL ---")
        app.run(host='127.0.0.1', port=5001, debug=True)