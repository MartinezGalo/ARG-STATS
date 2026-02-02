import sqlite3
import os
import json
from flask import Flask, render_template_string, request, redirect, url_for, jsonify, send_from_directory

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

# Hacemos disponible la funcion en los templates
app.jinja_env.globals.update(get_short_name=get_short_name)

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
        SELECT * FROM player_match_details 
        WHERE match_id = ? AND team_id = ? AND is_starter = 1 AND role_x IS NOT NULL
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

def get_team_rankings_logic(team_id, rank_type='tiradores', filter_type='all', limit=None, match_id=None):
    """
    Ranking de jugadores individuales. 
    Si limit tiene valor (ej: 5), busca solo los ultimos N partidos finalizados del equipo.
    """
    conn = get_db_connection()
    lt_sub = "(SELECT team_id FROM player_match_details pmd2 JOIN matches m2 ON pmd2.match_id = m2.id WHERE pmd2.player_id = pmd.player_id ORDER BY m2.date DESC LIMIT 1)"
    match_filter = ""

    if match_id:
        match_filter = f"AND pmd.match_id = '{match_id}'"
    elif limit:
        match_rows = conn.execute("SELECT id FROM matches WHERE (id_home_team = ? OR id_away_team = ?) AND finished = 1 ORDER BY date DESC LIMIT ?", (str(team_id), str(team_id), limit)).fetchall()
        if match_rows:
            ids_str = ",".join([f"'{mid}'" for mid in [r[0] for r in match_rows]])
            match_filter = f"AND pmd.match_id IN ({ids_str})"
        else: return []

    join_sql, val_sql, where_sql = _get_stat_sql_config(rank_type, filter_type)
    
    # Mover condiciones del WHERE al ON del LEFT JOIN para que no filtre filas de pmd (partidos jugados)
    if where_sql:
        join_sql += f" {where_sql}"
        where_sql = ""
    
    query = f'''
        SELECT pmd.player_id, pmd.last_name as player_name, pmd.position, {val_sql} as val, COUNT(DISTINCT pmd.match_id) as pj, {lt_sub} as ct,
        (SELECT shirt_number FROM player_match_details pmd3 JOIN matches m3 ON pmd3.match_id = m3.id WHERE pmd3.player_id = pmd.player_id ORDER BY m3.date DESC LIMIT 1) as shirt_number,
        SUM(pmd.minutes_played) as minutes_played
        FROM player_match_details pmd 
        {join_sql} 
        WHERE pmd.team_id = ? AND pmd.minutes_played > 0 {match_filter} {where_sql}
        GROUP BY pmd.player_id HAVING val > 0 ORDER BY val DESC
    '''
    
    res = conn.execute(query, (str(team_id),)).fetchall()
    u_map = {"tiradores": "tiros", "shots": "tiros", "goals":"goles", "headers": "cabezazos", "yellows": "tarjetas", "cards": "tarjetas", "fouls": "faltas", "fouls_rec": "faltas recibidas", "fouls_received": "recibidas"}
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
            "avg": avg
        })
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
                q = f"SELECT s.player_id as pid, s.last_name as pname, s.team_id as t_id, COUNT(*) as total FROM shots_on_target s WHERE s.team_id = ? AND s.match_id IN ({ids_str}) AND s.own_goal = 0 GROUP BY s.player_id"
            else:
                where_f = "AND inside_box = 0" if filter_type == 'long' else ""
                q = f"SELECT s.player_id as pid, s.last_name as pname, s.team_id as t_id, COUNT(*) as total FROM shots s WHERE s.team_id = ? AND s.match_id IN ({ids_str}) {where_f} AND s.own_goal = 0 GROUP BY s.player_id"
            rows = conn.execute(q, (str(tid),)).fetchall()
        elif rank_type == 'headers':
            q = f"SELECT s.player_id as pid, s.last_name as pname, s.team_id as t_id, COUNT(*) as total FROM shots s WHERE s.team_id = ? AND s.shot_type = 'Header' AND s.match_id IN ({ids_str}) AND s.own_goal = 0 GROUP BY s.player_id"
            rows = conn.execute(q, (str(tid),)).fetchall()
        elif rank_type == 'goals':
            q = f"SELECT s.player_id as pid, s.last_name as pname, s.team_id as t_id, COUNT(*) as total FROM goals s WHERE s.team_id = ? AND s.match_id IN ({ids_str}) GROUP BY s.player_id"
            rows = conn.execute(q, (str(tid),)).fetchall()
        elif rank_type == 'cards':
            q = f"SELECT c.player_id as pid, c.last_name as pname, c.team_id as t_id, SUM(CASE WHEN c.card_type = 'Red' THEN 2 WHEN c.card_id IS NOT NULL THEN 1 ELSE 0 END) as total FROM cards c WHERE c.team_id = ? AND c.match_id IN ({ids_str}) GROUP BY c.player_id"
            rows = conn.execute(q, (str(tid),)).fetchall()
        elif rank_type == 'fouls':
            q = f"SELECT pmd.player_id as pid, pmd.last_name as pname, pmd.team_id as t_id, SUM(pmd.fouls_committed) as total FROM player_match_details pmd WHERE pmd.team_id = ? AND pmd.match_id IN ({ids_str}) GROUP BY pmd.player_id"
            rows = conn.execute(q, (str(tid),)).fetchall()
        elif rank_type in ('fouls_rec', 'fouls_received'):
            q = f"SELECT pmd.player_id as pid, pmd.last_name as pname, pmd.team_id as t_id, SUM(pmd.fouls_received) as total FROM player_match_details pmd WHERE pmd.team_id = ? AND pmd.match_id IN ({ids_str}) GROUP BY pmd.player_id"
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
    conn.close()
    return render_template_string(INDEX_HTML, matches=matches, years=years, current_year=year, current_tournament=tournament, current_gameweek=gameweek)

@app.route('/stats')
def stats_page():
    return render_template_string(STATS_HTML, team_map=json.dumps(TEAM_NAME_MAP))

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

    home_subs = process_subs(conn.execute('SELECT * FROM player_match_details WHERE match_id=? AND team_id=? AND is_starter=0', (str(h_mid or match_id), str(match['id_home_team']))).fetchall())
    away_subs = process_subs(conn.execute('SELECT * FROM player_match_details WHERE match_id=? AND team_id=? AND is_starter=0', (str(a_mid or match_id), str(match['id_away_team']))).fetchall())

    stats = {"home": {"shots": 0, "target": 0, "fouls": 0, "cards": 0}, "away": {"shots": 0, "target": 0, "fouls": 0, "cards": 0}}
    if match['finished'] == 1:
        # Total Shots
        for r in conn.execute('SELECT team_id, COUNT(*) as tot FROM shots WHERE match_id=? AND own_goal=0 GROUP BY team_id', (str(match_id),)).fetchall():
            k = "home" if str(r['team_id']) == str(match['id_home_team']) else "away"; stats[k]["shots"] = r['tot']
        # Shots on Target (Using View)
        for r in conn.execute('SELECT team_id, COUNT(*) as tar FROM shots_on_target WHERE match_id=? AND own_goal=0 GROUP BY team_id', (str(match_id),)).fetchall():
            k = "home" if str(r['team_id']) == str(match['id_home_team']) else "away"; stats[k]["target"] = r['tar']
            
        stats["home"]["cards"] = conn.execute("SELECT COALESCE(SUM(CASE WHEN card_type = 'Red' THEN 2 WHEN card_id IS NOT NULL THEN 1 ELSE 0 END), 0) FROM cards WHERE match_id=? AND team_id=?", (str(match_id), str(match['id_home_team']))).fetchone()[0]
        stats["away"]["cards"] = conn.execute("SELECT COALESCE(SUM(CASE WHEN card_type = 'Red' THEN 2 WHEN card_id IS NOT NULL THEN 1 ELSE 0 END), 0) FROM cards WHERE match_id=? AND team_id=?", (str(match_id), str(match['id_away_team']))).fetchone()[0]
        for r in conn.execute('SELECT team_id, SUM(fouls_committed) as f FROM player_match_details WHERE match_id=? GROUP BY team_id', (str(match_id),)).fetchall():
            k = "home" if str(r['team_id']) == str(match['id_home_team']) else "away"; stats[k]["fouls"] = r['f'] or 0

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
        rows = conn.execute('SELECT id, id_home_team, id_away_team, home_team, away_team FROM matches WHERE (id_home_team = ? OR id_away_team = ?) AND finished = 1 ORDER BY date DESC LIMIT 5', (str(tid), str(tid))).fetchall()
        res = []
        for r in rows:
            is_home = str(r['id_home_team']) == str(tid)
            rival_id = r['id_away_team'] if is_home else r['id_home_team']
            rival_name = r['away_team'] if is_home else r['home_team']
            res.append({'rival_id': rival_id, 'rival_name': rival_name, 'cond': 'L' if is_home else 'V', 'id': str(r['id'])})
        return res

    l5_home = get_last_5_context(match['id_home_team'])
    l5_away = get_last_5_context(match['id_away_team'])
    
    # Contexto del "Ultimo Partido" (para cuando el actual esta pendiente)
    def get_single_context(mid, tid):
        if not mid: return None
        r = conn.execute('SELECT id, id_home_team, id_away_team, home_team, away_team FROM matches WHERE id = ?', (str(mid),)).fetchone()
        if not r: return None
        is_home = str(r['id_home_team']) == str(tid)
        rival_id = r['id_away_team'] if is_home else r['id_home_team']
        rival_name = r['away_team'] if is_home else r['home_team']
        return {'rival_id': rival_id, 'rival_name': rival_name, 'cond': 'L' if is_home else 'V', 'id': str(mid)}

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
            SELECT g.minute, g.team_id, g.first_name, g.last_name, g.own_goal,
                   (SELECT last_name FROM player_match_details WHERE player_id = g.assist_id AND match_id = g.match_id) as assist_name 
            FROM goals g
            WHERE g.match_id = ? ORDER BY CAST(g.minute as INTEGER) ASC
        ''', (str(match_id),)).fetchall()
        for g in goals_data:
            tid = str(g['team_id'])
            scorer = g['last_name']
            
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

    conn.close()
    return render_template_string(DETAIL_HTML, match=match, home_lineup=home_lineup, away_lineup=away_lineup, home_subs=home_subs, away_subs=away_subs, home_top=get_team_rankings_logic(match['id_home_team']), away_top=get_team_rankings_logic(match['id_away_team']), stats=stats, m_note=m_note, pred_s=pred_s, pred_h=pred_h, pred_c=pred_c, pred_f=pred_f, lineup_label="Formacion" if match['finished'] else "ultimo 11", current_filter=sf, h2h_matches=h2h_matches, ref_history=ref_history, l5_home=l5_home, l5_away=l5_away, last_match_home=last_match_home, last_match_away=last_match_away, h_mid=h_mid, a_mid=a_mid, match_goals=match_goals)

@app.route('/api/team_ranking/<team_id>')
def api_team_ranking(team_id):
    limit = request.args.get('limit', type=int)
    match_id = request.args.get('match_id') # Capturamos el match_id
    return jsonify(get_team_rankings_logic(
        team_id, 
        request.args.get('type', 'tiradores'), 
        request.args.get('filter', 'all'), 
        limit,
        match_id
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
        WHERE pmd.player_id = ? 
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
    conn = get_db_connection(); match = conn.execute('SELECT id_home_team, id_away_team, referee FROM matches WHERE id = ?', (str(match_id),)).fetchone(); conn.close()
    if not match: return jsonify({"error": "N/A"}), 404
    ft = request.args.get('shot_filter', 'all')
    return jsonify({
        "shots": get_prediction_logic(match['id_home_team'], match['id_away_team'], 'shots', ft),
        "headers": get_prediction_logic(match['id_home_team'], match['id_away_team'], 'headers'),
        "cards": get_prediction_logic(match['id_home_team'], match['id_away_team'], 'cards', referee=match['referee']),
        "fouls": get_prediction_logic(match['id_home_team'], match['id_away_team'], 'fouls', referee=match['referee'])
    })

@app.route('/search_players/<team_id>')
def search_players(team_id):
    q = request.args.get('q', '')
    conn = get_db_connection()
    # Busca jugadores unicos por nombre que hayan jugado en ese equipo
    players = conn.execute('''
        SELECT player_id, first_name, last_name, position, MAX(shirt_number) as shirt_number 
        FROM player_match_details 
        WHERE team_id = ? AND (first_name || ' ' || last_name) LIKE ? 
        GROUP BY player_id
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
    
    return render_template_string(TEAM_HTML, 
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
    return render_template_string(REFEREE_HTML, ref_name=name, matches=matches, ranks=ranks, top_targets=top_targets, stats_avg=stats_avg)



# --- PLANTILLAS HTML ---

FOOTER_HTML = '''<footer class="mt-20 pt-6 border-t border-slate-700/50 text-center">
    <div class="flex flex-col items-center gap-4">
        <a href="https://github.com/MartinezGalo/ARG-STATS" target="_blank" 
        class="group flex items-center gap-2 bg-sky-500/10 px-6 py-2 rounded-full border border-sky-500/20 text-sky-400 text-xs font-black uppercase tracking-widest transition-all hover:bg-sky-600 hover:text-white hover:shadow-[0_0_20px_rgba(14,165,233,0.4)]">
            <svg width="16" height="16" fill="currentColor" viewBox="0 0 24 24">
                <path d="M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23.957-.266 1.983-.399 3.003-.404 1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576 4.765-1.589 8.199-6.086 8.199-11.386 0-6.627-5.373-12-12-12z"/>
            </svg>
            GitHub Repository
        </a>

        <p class="text-[10px] font-bold text-slate-500 uppercase tracking-[0.2em]">
            Desarrollado por 
            <a href="https://github.com/MartinezGalo" target="_blank" class="text-slate-300">MartinezGalo</a> &
            <a href="https://github.com/francoqdev" target="_blank" class="text-slate-300">francoqdev</a> 
        </p>
    </div>
</footer>'''


INDEX_HTML = '''
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8"><title>ARG STATS</title><script src="https://cdn.tailwindcss.com"></script><style>body{background-color:#0f172a;color:#f8fafc;}</style>
    <meta name="author" content="MartinezGalo & francoqdev">
    <meta name="copyright" content="ARG STATS">
    <link rel="icon" href="{{ url_for('static', filename='lpf.png') }}?v=2" type="image/png">
</head>
<body class="p-8 font-sans">
        <script>
        function stepGameweek(delta) {
            const gameweekSelect = document.getElementById('gameweek-select');
            const tournamentSelect = document.getElementById('tournament-select');
            const yearSelect = document.getElementById('year-select');
            
            const currentGameweek = parseInt(gameweekSelect.value);
            const currentTournament = tournamentSelect.value;
            const currentYear = yearSelect.value;
            
            let newGameweek = currentGameweek + delta;
            let newTournament = currentTournament;
            let newYear = currentYear;
            
            // Logica para navegar hacia atras (delta = -1)
            if (delta === -1) {
                if (newGameweek < 1) {
                    // Cambiar a torneo anterior
                    if (currentTournament === "Liga Profesional Apertura") {
                        newTournament = "Liga Profesional Clausura";
                        newYear = (parseInt(currentYear) - 1).toString();
                        newGameweek = 20;
                    } else {
                        newTournament = "Liga Profesional Apertura";
                        newGameweek = 20;
                    }
                }
            }
            // Logica para navegar hacia adelante (delta = 1)
            else if (delta === 1) {
                if (newGameweek > 20) {
                    // Cambiar a torneo siguiente
                    if (currentTournament === "Liga Profesional Apertura") {
                        newTournament = "Liga Profesional Clausura";
                        newGameweek = 1;
                    } else {
                        newTournament = "Liga Profesional Apertura";
                        newYear = (parseInt(currentYear) + 1).toString();
                        newGameweek = 1;
                    }
                }
            }
            
            // Actualizar los selects y enviar el formulario
            yearSelect.value = newYear;
            tournamentSelect.value = newTournament;
            gameweekSelect.value = newGameweek;
            
            document.getElementById('filter-form').submit();
        }
    </script>
    <div class="max-w-5xl mx-auto">
        <!-- HEADER -->
        <header class="flex flex-col md:flex-row justify-between items-center mb-12 gap-6">
            <a href="/"><h1 class="text-6xl font-black italic uppercase tracking-tighter text-white">ARG STATS</h1></a>
            <nav class="flex gap-4">
                <a href="/" class="bg-sky-600 px-6 py-2 rounded-xl text-xs font-black uppercase shadow-lg">Partidos</a>
                <a href="/stats" class="bg-slate-800 hover:bg-slate-700 px-6 py-2 rounded-xl text-xs font-black uppercase transition-all border border-slate-700">Estadisticas Liga</a>
            </nav>
        </header>

        <!-- FILTROS -->
        <div class="flex sticky top-0 z-10 justify-center mb-12">
            <form id="filter-form" class="flex flex-wrap items-stretch justify-center gap-0 bg-slate-800/40 rounded-[2rem] border border-slate-700/50 backdrop-blur-md shadow-2xl overflow-hidden">
                <div class="flex flex-col border-r border-slate-700/50 p-4 hover:bg-slate-700/20 transition-colors">
                    <label class="text-[9px] font-black uppercase text-sky-400 mb-1 tracking-[0.2em] text-center">Temporada</label>
                    <select name="year" id="year-select" onchange="this.form.submit()" class="bg-transparent text-white text-sm font-bold outline-none cursor-pointer">
                        {% for y in years %}
                        <option value="{{ y }}" {% if current_year == y %}selected{% endif %} class="bg-slate-900">{{ y }}</option>
                        {% endfor %}
                    </select>
                </div>

                <div class="flex flex-col border-r border-slate-700/50 p-4 hover:bg-slate-700/20 transition-colors">
                    <label class="text-[9px] font-black uppercase text-sky-400 mb-1 tracking-[0.2em] text-center">Torneo</label>
                    <select name="tournament" id="tournament-select" onchange="this.form.submit()" class="bg-transparent text-white text-sm font-bold outline-none cursor-pointer">
                        <option value="Liga Profesional Apertura" {% if current_tournament == 'Liga Profesional Apertura' %}selected{% endif %} class="bg-slate-900">Apertura</option>
                        <option value="Liga Profesional Clausura" {% if current_tournament == 'Liga Profesional Clausura' %}selected{% endif %} class="bg-slate-900">Clausura</option>
                    </select>
                </div>

                <div class="flex items-center gap-4 p-4 hover:bg-slate-700/20 transition-colors">
                    <button type="button" onclick="stepGameweek(-1)" class="p-2 rounded-full hover:bg-sky-500/20 text-sky-400 transition-all active:scale-90">
                        <svg width="24" height="24" fill="none" stroke="currentColor" stroke-width="3" viewBox="0 0 24 24"><path d="m15 18-6-6 6-6"/></svg>
                    </button>
                    
                    <div class="flex flex-col items-center">
                        <label class="text-[9px] font-black uppercase text-sky-400 mb-1 tracking-[0.2em]">Jornada</label>
                        <select name="gameweek" id="gameweek-select" onchange="this.form.submit()" class="bg-transparent text-white text-sm font-bold outline-none cursor-pointer">
                            {% for i in range(1, 21) %}
                            <option value="{{ i }}" {% if current_gameweek|int == i %}selected{% endif %} class="bg-slate-900">Fecha {{ i }}</option>
                            {% endfor %}
                        </select>
                    </div>

                    <button type="button" onclick="stepGameweek(1)" class="p-2 rounded-full hover:bg-sky-500/20 text-sky-400 transition-all active:scale-90">
                        <svg width="24" height="24" fill="none" stroke="currentColor" stroke-width="3" viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"/></svg>
                    </button>
                </div>
            </form>
        </div>
        
        <!-- GRILLA PARTIDOS -->
        <div class="grid grid-cols-1 gap-8">
            {% macro score_color(val) %}{% if val <= 30 %}text-red-500{% elif val <= 70 %}text-blue-500{% else %}text-green-500{% endif %}{% endmacro %}
            {% for m in matches %}
            <!-- TARJETA PARTIDO -->
            <div class="bg-slate-800 p-8 rounded-[2.5rem] border border-slate-700 shadow-lg relative overflow-hidden transition-all hover:border-slate-600">
                    
                    <div class="flex flex-wrap justify-between items-center mb-8 border-b border-slate-700/50 pb-4 gap-4">
                        <div class="flex items-center gap-4">
                            <span class="bg-sky-500/10 text-sky-400 px-4 py-1.5 rounded-xl text-[10px] font-black uppercase tracking-widest border border-sky-500/20">{{ m.tournament }}</span>
                            <span class="text-[14px] font-black text-slate-300 tracking-tighter">{{ m.date[:16] }}</span>
                            <div class="flex items-center gap-2">
                                <span class="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">arbitro:</span>
                                
                                <div class="flex items-center gap-1.5">
                                    {% if m.referee %}
                                        <a href="/referee/{{ m.referee }}" 
                                        class="text-sm font-bold text-slate-200 italic hover:text-sky-400 transition-colors">
                                            {{ m.referee }}
                                        </a>
                                    {% else %}
                                        <span class="text-sm font-medium text-slate-500 italic tracking-tight">
                                            Por designar
                                        </span>
                                    {% endif %}
                                </div>
                            </div>
                        </div>
                        <span class="text-[10px] font-black uppercase tracking-widest {% if m.finished %}text-slate-500{% else %}text-emerald-400 animate-pulse{% endif %}">
                            {% if m.finished %}FINALIZADO{% else %}PENDIENTE{% endif %}
                        </span>
                    </div>
                    
                    <div class="flex items-center justify-between gap-2">

                        <div class="flex-1 flex flex-col items-center text-center">
                            <a href="/team/{{ m.id_home_team }}" class="text-3xl font-black uppercase tracking-tighter hover:text-sky-400 transition-colors mb-6 block flex flex-col items-center gap-4">
                                <img src="{{ url_for('static', filename=m.id_home_team ~ '.png') }}" onerror="this.onerror=null; this.src='{{ url_for('static', filename='none.png') }}'" class="w-20 h-20 object-contain drop-shadow-lg" alt="{{ m.home_team }}">
                                {{ get_short_name(m.home_team) }}
                            </a>
                            <div class="grid grid-cols-2 gap-x-8 gap-y-4">
                                <div class="flex flex-col">
                                    <span class="text-[9px] font-black text-slate-500 uppercase">Tiros</span>
                                    <span class="text-xl font-black {{ score_color(m.preds.s_home) }}">{{ m.preds.s_home }}</span>
                                </div>
                                <div class="flex flex-col">
                                    <span class="text-[9px] font-black text-slate-500 uppercase">Cabezazos</span>
                                    <span class="text-xl font-black {{ score_color(m.preds.h_home) }}">{{ m.preds.h_home }}</span>
                                </div>
                                <div class="flex flex-col">
                                    <span class="text-[9px] font-black text-slate-500 uppercase">Faltas</span>
                                    <span class="text-xl font-black {{ score_color(m.preds.f_home) }}">{{ m.preds.f_home }}</span>
                                </div>
                                <div class="flex flex-col">
                                    <span class="text-[9px] font-black text-slate-500 uppercase">Tarjetas</span>
                                    <span class="text-xl font-black {{ score_color(m.preds.c_home) }}">{{ m.preds.c_home }}</span>
                                </div>
                            </div>
                        </div>

                        <div class="flex flex-col items-center gap-4 px-4">
                            <div class="px-8 py-4 bg-slate-900 rounded-3xl font-mono text-3xl border-2 border-slate-700 text-white shadow-2xl">
                                {{ m.score or 'VS' }}
                            </div>
                            <a href="{{ url_for('match_detail', match_id=m.id) }}" class="text-[11px] font-black text-sky-500 uppercase tracking-widest hover:text-white transition-colors bg-sky-500/10 px-4 py-2 rounded-xl border border-sky-500/20">
                                Analizar Detalle →
                            </a>
                        </div>

                        <div class="flex-1 flex flex-col items-center text-center">
                            <a href="/team/{{ m.id_away_team }}" class="text-3xl font-black uppercase tracking-tighter hover:text-sky-400 transition-colors mb-6 block flex flex-col items-center gap-4">
                                <img src="{{ url_for('static', filename=m.id_away_team ~ '.png') }}" onerror="this.onerror=null; this.src='{{ url_for('static', filename='none.png') }}'" class="w-20 h-20 object-contain drop-shadow-lg" alt="{{ m.away_team }}">
                                {{ get_short_name(m.away_team) }}
                            </a>
                            <div class="grid grid-cols-2 gap-x-8 gap-y-4">
                                <div class="flex flex-col">
                                    <span class="text-[9px] font-black text-slate-500 uppercase">Tiros</span>
                                    <span class="text-xl font-black {{ score_color(m.preds.s_away) }}">{{ m.preds.s_away }}</span>
                                </div>
                                <div class="flex flex-col">
                                    <span class="text-[9px] font-black text-slate-500 uppercase">Cabezazos</span>
                                    <span class="text-xl font-black {{ score_color(m.preds.h_away) }}">{{ m.preds.h_away }}</span>
                                </div>
                                <div class="flex flex-col">
                                    <span class="text-[9px] font-black text-slate-500 uppercase">Faltas</span>
                                    <span class="text-xl font-black {{ score_color(m.preds.f_away) }}">{{ m.preds.f_away }}</span>
                                </div>
                                <div class="flex flex-col">
                                    <span class="text-[9px] font-black text-slate-500 uppercase">Tarjetas</span>
                                    <span class="text-xl font-black {{ score_color(m.preds.c_away) }}">{{ m.preds.c_away }}</span>
                                </div>
                            </div>
                        </div>

                    </div>
                </div>
            {% endfor %}
            {% if not matches %}
            <div class="bg-slate-800/30 border-2 border-dashed border-slate-700 p-20 rounded-[3rem] text-center">
                <p class="text-slate-500 font-black uppercase tracking-widest">No hay partidos programados para esta fecha</p>
            </div>
            {% endif %}
        </div>
    </div>
    ''' + FOOTER_HTML + '''</body></html>'''

STATS_HTML = '''
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8"><title>ARG STATS</title>
    <meta name="author" content="MartinezGalo & francoqdev">
    <meta name="copyright" content="ARG STATS">
    <link rel="icon" href="{{ url_for('static', filename='lpf.png') }}?v=2" type="image/png">
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { background-color: #0f172a; color: #f8fafc; }
        .custom-scroll::-webkit-scrollbar { width: 6px; }
        .custom-scroll::-webkit-scrollbar-track { background: #1e293b; border-radius: 10px; }
        .custom-scroll::-webkit-scrollbar-thumb { background: #334155; border-radius: 10px; }
        .custom-scroll::-webkit-scrollbar-thumb:hover { background: #0ea5e9; }
        .btn-active { background-color: #0ea5e9; color: white; border-color: transparent; box-shadow: 0 10px 15px -3px rgba(14, 165, 233, 0.3); }
        .btn-inactive { background-color: #1e293b; color: #94a3b8; border-color: #334155; }
        .btn-inactive:hover { color: white; border-color: #475569; }
    </style>
</head>
<body class="p-8 pb-0 font-sans">
    <div class="max-w-[1500px] mx-auto">
        <!-- HEADER -->
        <header class="flex flex-col md:flex-row justify-between items-center mb-4 gap-6">
            <a href="/"><h1 class="text-6xl font-black italic uppercase tracking-tighter text-white">ARG STATS</h1></a>
            <nav>
                <a href="/" class="bg-slate-800 hover:bg-slate-700 px-6 py-2 rounded-xl text-xs font-black uppercase transition-all border border-slate-700">← Volver</a>
            </nav>
        </header>

        <!-- CONTROLES -->
        <div class="flex flex-col items-center gap-4 mt-2 mb-4 bg-slate-900/80 p-4 rounded-b-[2rem] border-t border-slate-200 backdrop-blur-xl shadow-2xl">
            
            <!-- 1. MODO (Equipos | Jugadores | Arbitros) -->
            <div class="flex bg-slate-800 p-1.5 rounded-2xl border border-slate-700 shadow-lg">
                <button onclick="setMode('teams')" id="btn-mode-teams" class="px-8 py-1 rounded-xl text-[11px] font-black uppercase transition-all">Equipos</button>
                <button onclick="setMode('players')" id="btn-mode-players" class="px-8 py-1 rounded-xl text-[11px] font-black uppercase transition-all">Jugadores</button>
                <button onclick="setMode('referees')" id="btn-mode-referees" class="px-8 py-1 rounded-xl text-[11px] font-black uppercase transition-all">Arbitros</button>
            </div>

            <!-- 2. CATEGORIA -->
            <div class="flex flex-wrap justify-center gap-2">
                <button onclick="setCategory('shots')" id="btn-cat-shots" class="cat-btn px-6 py-1.5 rounded-lg text-[10px] font-black uppercase border transition-all">Tiros</button>
                <button onclick="setCategory('goals')" id="btn-cat-goals" class="cat-btn px-6 py-1.5 rounded-lg text-[10px] font-black uppercase border transition-all">Goles</button>
                <button onclick="setCategory('headers')" id="btn-cat-headers" class="cat-btn px-6 py-1.5 rounded-lg text-[10px] font-black uppercase border transition-all">Cabezazos</button>
                <button onclick="setCategory('cards')" id="btn-cat-cards" class="cat-btn px-6 py-1.5 rounded-lg text-[10px] font-black uppercase border transition-all">Tarjetas</button>
                <button onclick="setCategory('fouls')" id="btn-cat-fouls" class="cat-btn px-6 py-1.5 rounded-lg text-[10px] font-black uppercase border transition-all">Faltas</button>
                <button onclick="setCategory('fouls_received')" id="btn-cat-fouls_received" class="cat-btn px-6 py-1.5 rounded-lg text-[10px] font-black uppercase border transition-all">Faltas Rec.</button>
            </div>

            <!-- 3. SUB-FILTRO (Solo Tiros) -->
            <div id="subfilter-container" class="hidden flex gap-2 animate-fade-in-down">
                <button onclick="setSubFilter('all')" id="btn-sub-all" class="sub-btn px-4 py-1 rounded-md text-[9px] font-bold uppercase border transition-all">Todos</button>
                <button onclick="setSubFilter('target')" id="btn-sub-target" class="sub-btn px-4 py-1 rounded-md text-[9px] font-bold uppercase border transition-all">Al Arco</button>
                <button onclick="setSubFilter('long')" id="btn-sub-long" class="sub-btn px-4 py-1 rounded-md text-[9px] font-bold uppercase border transition-all">Lejos</button>
            </div>
        </div>

        <!-- RESULTADOS -->
        <div id="grid-container" class="grid gap-2">
            <!-- El contenido se inyecta via JS -->
        </div>
    </div>

    <script>
    const TEAM_MAP = {{ team_map|safe }};
    function getShortName(name) { return TEAM_MAP[name] || name; }

    // Estado Global
    const state = {
        mode: 'teams', // teams, players, referees
        category: 'shots', // shots, headers, cards, fouls
        subFilter: 'all', // all, target, long
    };

    // Estado Individual por Grid
    const gridState = {
        made: { sort: 'total', l5: false },
        against: { sort: 'total', l5: false },
        single: { sort: 'total', l5: false }
    };

    const pages = { made: 1, against: 1, single: 1 };
    const dataCache = { made: [], against: [], single: [] };
    const perPage = 10;

    // Diccionarios de etiquetas
    const LABELS = {
        teams: {
            shots: { made: 'Tiros Realizados', against: 'Tiros Recibidos' },
            goals: { made: 'Goles a Favor', against: 'Goles en Contra' },
            headers: { made: 'Cabezazos Realizados', against: 'Cabezazos Recibidos' },
            cards: { made: 'Tarjetas Recibidas', against: 'Tarjetas Generadas (Rival)' },
            fouls: { made: 'Faltas Cometidas', against: 'Faltas Recibidas)' }
        },
        players: {
            shots: 'Rematadores',
            goals: 'Goleadores',
            headers: 'Cabezazos',
            cards: 'Tarjetas Recibidas',
            fouls: 'Faltas Cometidas',
            fouls_received: 'Faltas Recibidas'
        },
        referees: {
            cards: 'Arbitros con mas Tarjetas',
            fouls: 'Arbitros con mas Faltas'
        }
    };

    function init() {
        updateUI();
        fetchData();
    }

    function resetGridState() {
        ['made', 'against', 'single'].forEach(k => {
            gridState[k] = { sort: 'total', l5: false };
        });
    }

    function setMode(m) { 
        state.mode = m; 
        resetGridState(); 
        
        if (m === 'referees') {
            state.category = 'cards';
        } else {
            state.category = 'shots';
            state.subFilter = 'all';
        }
        
        updateUI(); 
        fetchData(); 
    }
    function setCategory(c) { state.category = c; resetGridState(); updateUI(); fetchData(); }
    function setSubFilter(s) { state.subFilter = s; updateUI(); fetchData(); }
    
    function toggleSort(id) { 
        gridState[id].sort = (gridState[id].sort === 'total' ? 'avg' : 'total'); 
        fetchData(id); 
    }
    
    function toggleL5(id) { 
        gridState[id].l5 = !gridState[id].l5; 
        fetchData(id); 
    }

    function updateUI() {
        // Modo
        ['teams', 'players', 'referees'].forEach(m => {
            const btn = document.getElementById(`btn-mode-${m}`);
            if (state.mode === m) {
                btn.className = 'px-8 py-1 rounded-xl text-[11px] font-black uppercase transition-all btn-active';
            } else {
                btn.className = 'px-8 py-1 rounded-xl text-[11px] font-black uppercase transition-all btn-inactive';
            }
        });

        // Categorias
        const cats = ['shots','goals', 'headers', 'cards', 'fouls', 'fouls_received'];
        cats.forEach(c => {
            const btn = document.getElementById(`btn-cat-${c}`);
            let show = true;
            
            // Logic for visibility
            if (state.mode === 'referees' && (c === 'shots' || c === 'goals' || c === 'headers' || c === 'fouls_received')) show = false;
            if (state.mode === 'teams' && c === 'fouls_received') show = false;

            if (!show) {
                btn.style.display = 'none';
            } else {
                btn.style.display = 'inline-block';
                if (state.category === c) {
                    btn.className = 'cat-btn px-6 py-1.5 rounded-lg text-[10px] font-black uppercase border transition-all btn-active';
                } else {
                    btn.className = 'cat-btn px-6 py-1.5 rounded-lg text-[10px] font-black uppercase border transition-all btn-inactive';
                }
            }
        });
        
        if (state.mode === 'referees' && (state.category === 'shots' || state.category === 'headers' || state.category === 'fouls_received')) {
            state.category = 'cards';
            updateUI();
            return;
        }

        const subDiv = document.getElementById('subfilter-container');
        if (state.category === 'shots') {
            subDiv.classList.remove('hidden');
            ['all', 'target', 'long'].forEach(s => {
                const btn = document.getElementById(`btn-sub-${s}`);
                if (state.subFilter === s) {
                    btn.className = 'sub-btn px-4 py-1 rounded-md text-[9px] font-bold uppercase border transition-all bg-sky-500 text-white border-transparent shadow-md';
                } else {
                    btn.className = 'sub-btn px-4 py-1 rounded-md text-[9px] font-bold uppercase border transition-all bg-slate-800 text-slate-400 border-slate-700 hover:text-white';
                }
            });
        } else {
            subDiv.classList.add('hidden');
        }
    }

    let currentFetchId = 0;

    async function fetchData(targetId = null) {
        const fetchId = ++currentFetchId;
        const grid = document.getElementById('grid-container');
        
        // Ensure correct grid structure
        if (state.mode === 'teams') {
            if (!document.getElementById('box-container-made')) renderDoubleGrid();
        } else {
            if (!document.getElementById('box-container-single')) renderSingleGrid();
        }
        
        // Loading State (Targeted)
        const targets = targetId ? [targetId] : (state.mode === 'teams' ? ['made', 'against'] : ['single']);
        targets.forEach(id => {
            const el = document.getElementById(`box-container-${id}`);
            if(el) el.style.opacity = '0.5';
        });

        try {
            if (state.mode === 'teams') {
                const reqs = [];
                // Si targetId es null, traemos todo. Si no, solo el que cambio.
                if (!targetId || targetId === 'made') {
                    const s = gridState.made;
                    pages.made = 1;
                    reqs.push(fetch(`/api/team_stats?category=${state.category}&filter=${state.subFilter}&side=made&limit=${s.l5?5:''}&order_by=${s.sort}`).then(r=>r.json()).then(d => { dataCache.made = d; }));
                }
                if (!targetId || targetId === 'against') {
                    const s = gridState.against;
                    pages.against = 1;
                    reqs.push(fetch(`/api/team_stats?category=${state.category}&filter=${state.subFilter}&side=against&limit=${s.l5?5:''}&order_by=${s.sort}`).then(r=>r.json()).then(d => { dataCache.against = d; }));
                }
                
                await Promise.all(reqs);
                if (currentFetchId !== fetchId) return;

                if (state.mode === 'teams') {
                   // Re-render boxes
                   if (!targetId || targetId === 'made') renderBox(document.getElementById('box-container-made'), 'made', LABELS.teams[state.category].made, true);
                   if (!targetId || targetId === 'against') renderBox(document.getElementById('box-container-against'), 'against', LABELS.teams[state.category].against, true);
                }

            } else {
                const s = gridState.single;
                pages.single = 1;
                let url = '';
                if (state.mode === 'players') {
                    // Limite a 100 (10 paginas de 10)
                    url = `/api/player_stats?rank_type=${state.category}&filter=${state.subFilter}&order_by=${s.sort}&limit=50`;
                    if (s.l5) url += '&limit_matches=5';
                } else {
                    url = `/api/referee_stats?category=${state.category}&order_by=${s.sort}`;
                    if (s.l5) url += '&limit=5';
                }
                
                const data = await fetch(url).then(r => r.json());
                if (currentFetchId !== fetchId) return;
                dataCache.single = data;
                
                if (state.mode !== 'teams') {
                   renderBox(document.getElementById('box-container-single'), 'single', LABELS[state.mode][state.category], false);
                }
            }
        } catch (e) {
            if (currentFetchId !== fetchId) return;
            console.error(e);
            grid.innerHTML = '<div class="text-center text-red-500 py-20 font-bold col-span-2">Error al cargar datos.</div>';
        } finally {
            if (currentFetchId === fetchId) {
                targets.forEach(id => {
                    const el = document.getElementById(`box-container-${id}`);
                    if(el) el.style.opacity = '1';
                });
            }
        }
    }

    function renderDoubleGrid() {
        const grid = document.getElementById('grid-container');
        grid.className = 'grid grid-cols-1 lg:grid-cols-2 gap-8';
        grid.innerHTML = '<div id="box-container-made"></div><div id="box-container-against"></div>';
        // Los datos pueden no estar listos, renderBox maneja array vacio
        renderBox(document.getElementById('box-container-made'), 'made', LABELS.teams[state.category].made, true);
        renderBox(document.getElementById('box-container-against'), 'against', LABELS.teams[state.category].against, true);
    }

    function renderSingleGrid() {
        const grid = document.getElementById('grid-container');
        grid.className = 'grid grid-cols-1 gap-8 max-w-4xl mx-auto w-full';
        grid.innerHTML = '<div id="box-container-single"></div>';
        renderBox(document.getElementById('box-container-single'), 'single', LABELS[state.mode][state.category], false);
    }

    function changeLocalPage(id, delta) {
        const total = Math.ceil(dataCache[id].length / perPage) || 1;
        let next = pages[id] + delta;
        if (next >= 1 && next <= total) {
            pages[id] = next;
            const container = document.getElementById(`box-container-${id}`);
            renderBox(container, id, container.dataset.title, container.dataset.isteam === 'true');
        }
    }

    function renderBox(container, id, title, isTeam) {
        if (!container) return;
        container.dataset.title = title;
        container.dataset.isteam = isTeam;
        const data = dataCache[id] || [];
        const page = pages[id];
        const start = (page - 1) * perPage;
        const visible = data.slice(start, start + perPage);
        const totalPages = Math.ceil(data.length / perPage) || 1;
        const s = gridState[id];

        container.innerHTML = `
            <div class="bg-slate-800/40 rounded-[2rem] border border-slate-700/50 shadow-xl flex flex-col overflow-hidden">
                <div class="bg-slate-800/50 px-6 py-4 border-b border-slate-700/50 flex justify-between items-center">
                    <h3 class="font-black text-sky-400 uppercase text-[15px] tracking-widest">${title}</h3>
                    <div class="flex gap-2">
                        <button onclick="toggleSort('${id}')" class="text-[11px] font-black uppercase px-3 py-1 rounded-full border ${s.sort==='avg'?'bg-sky-500 text-white border-transparent':'bg-slate-900 text-slate-500 border-slate-700'}">Promedio</button>
                        <button onclick="toggleL5('${id}')" class="text-[11px] font-black uppercase px-3 py-1 rounded-full border ${s.l5?'bg-sky-500 text-white border-transparent':'bg-slate-900 text-slate-500 border-slate-700'}">Ultimos 5</button>
                    </div>
                </div>
                <div class="py-2 flex-1 space-y-0">
                    ${visible.map((item, i) => renderRow(item, start + i, isTeam)).join('') || '<div class="text-center text-slate-600 italic py-10">Sin datos</div>'}
                </div>
                <div class="p-4 bg-slate-900/30 border-t border-slate-700/30 flex justify-between items-center">
                    <button onclick="changeLocalPage('${id}', -1)" class="p-2 rounded-lg bg-slate-800 text-sky-500 hover:bg-sky-600 hover:text-white disabled:opacity-0 transition-all" ${page === 1 ? 'disabled' : ''}>
                        <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="3" viewBox="0 0 24 24"><path d="m15 18-6-6 6-6"/></svg>
                    </button>
                    <span class="text-[12px] font-black text-slate-500 uppercase tracking-widest">${page} / ${totalPages}</span>
                    <button onclick="changeLocalPage('${id}', 1)" class="p-2 rounded-lg bg-slate-800 text-sky-500 hover:bg-sky-600 hover:text-white disabled:opacity-0 transition-all" ${page === totalPages ? 'disabled' : ''}>
                        <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="3" viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"/></svg>
                    </button>
                </div>
            </div>
        `;
    }

    function renderRow(item, i, isTeam) {
        let colorClass = 'text-slate-500';
        const idx = i; // Posicion real (0-indexed)
        const rank = idx + 1;

        if (state.mode === 'teams' || isTeam) {
            if (rank <= 10) colorClass = 'text-green-400';
            else if (rank <= 20) colorClass = 'text-blue-400';
            else if (rank <= 30) colorClass = 'text-red-400';
        } else if (state.mode === 'players') {
            if (rank <= 10) colorClass = 'text-green-400';
            else colorClass = 'text-blue-400';
        } else if (state.mode === 'referees') {
            if (rank <= 5) colorClass = 'text-green-400';
            else colorClass = 'text-blue-400';
        }

        let imgHtml = ''; let mainText = ''; let subText = ''; let link = '#';

        if (state.mode === 'teams' || isTeam) {
            imgHtml = `<img src="/static/${item.id}_xsmall.png" onerror="this.src='/static/none.png'" class="w-8 h-8 object-contain">`;
            mainText = getShortName(item.name); link = `/team/${item.id}`;
        } else if (state.mode === 'referees') {
            mainText = item.name; link = `/referee/${item.name}`;
        } else {
            mainText = item.name;
            subText = `<a href="/team/${item.t_id}" class="text-[12px] text-sky-500 font-bold uppercase hover:underline flex items-center gap-1 mt-0.5"><img src="/static/${item.t_id}_xsmall.png" class="w-3 h-3 object-contain"> ${getShortName(item.t_name)}</a>`;
        }

        return `
            <div class="flex justify-between items-center bg-slate-900/40 p-2 rounded-xl border border-slate-800/50 hover:border-slate-700 transition-all group">
                <div class="flex items-center gap-4 overflow-hidden">
                    <span class="text-lg font-black ${colorClass} w-6 text-center">#${rank}</span>
                    ${imgHtml}
                    <div class="flex flex-col truncate">
                        <a href="${link}" class="text-[15px] font-bold text-slate-200 group-hover:text-sky-400 transition-colors truncate">${mainText}</a>
                        ${subText}
                    </div>
                </div>
                

                <span class="text-[13px] font-bold text-slate-500"><span class="text-[15px] font-black text-white">${item.total}</span> en
                <span class="text-[15px] font-black text-white">${item.pj}</span> PJ | <span class="text-emerald-400">${item.avg}</span> / 90 </span>

            </div>
        `;
    }

    init();
    </script>
''' + FOOTER_HTML + '''</body></html>'''

TEAM_HTML = '''
<!DOCTYPE html>
<html lang="es">
<style>
    .custom-scroll::-webkit-scrollbar { width: 6px; }
    .custom-scroll::-webkit-scrollbar-track { background: #1e293b; border-radius: 10px; }
    .custom-scroll::-webkit-scrollbar-thumb { background: #334155; border-radius: 10px; border: 1px solid #1e293b; }
    .custom-scroll::-webkit-scrollbar-thumb:hover { background: #0ea5e9; }
    .custom-scroll { scrollbar-width: thin; scrollbar-color: #334155 #1e293b; }
    .player-card { 
        background: rgba(15, 23, 42, 0.6); 
        padding: 0.75rem 1rem; /* Un poco mas de aire que en la pizarra */
        border-radius: 1rem; 
        border: 1px solid #1e293b; 
        transition: all 0.2s; 
        cursor: pointer; 
        }
    .player-card:hover { 
        border-color: #38bdf8; 
        background: rgba(56, 189, 248, 0.05); 
    }
    #player-ranking-list {  min-height: 500px; display: flex; flex-direction: column; }
    
</style>
<head>
    <meta charset="UTF-8">
    <meta name="author" content="MartinezGalo & francoqdev">
    <meta name="copyright" content="ARG STATS">
    <title>{{ team_name }} - ARG STATS</title><script src="https://cdn.tailwindcss.com"></script><style>body{background-color:#0f172a;color:#f8fafc;}</style>
    <link rel="icon" href="{{ url_for('static', filename='lpf.png') }}?v=2" type="image/png">
</head>
<body class="p-8 font-sans">
        <script>
        function showMatches(type) {
            const finished = document.getElementById('matches-finished');
            const upcoming = document.getElementById('matches-upcoming');
            const btnFinished = document.getElementById('btn-finished');
            const btnUpcoming = document.getElementById('btn-upcoming');

            if (type === 'finished') {
                finished.classList.remove('hidden');
                upcoming.classList.add('hidden');
                btnFinished.classList.add('bg-sky-500', 'text-white', 'shadow-lg');
                btnFinished.classList.remove('text-slate-500');
                btnUpcoming.classList.remove('bg-sky-500', 'text-white', 'shadow-lg');
                btnUpcoming.classList.add('text-slate-500');
            } else {
                finished.classList.add('hidden');
                upcoming.classList.remove('hidden');
                btnUpcoming.classList.add('bg-sky-500', 'text-white', 'shadow-lg');
                btnUpcoming.classList.remove('text-slate-500');
                btnFinished.classList.remove('bg-sky-500', 'text-white', 'shadow-lg');
                btnFinished.classList.add('text-slate-500');
            }
        }

        let currentType = 'tiradores';
        let isL5 = false;
        let teamPlayerData = []; 
        let playerPage = 1;
        const playersPerPage = 10; // Cuantos mostrar por pagina
        const teamId = "{{ team_id }}";

        // 1. Alternar ultimos 5 partidos
        function toggleL5() {
            isL5 = !isL5;
            const btn = document.getElementById('l5-btn');
            if (isL5) {
                btn.classList.remove('text-slate-500', 'border-slate-700');
                btn.classList.add('bg-sky-500', 'text-white', 'border-transparent');
            } else {
                btn.classList.add('text-slate-500', 'border-slate-700');
                btn.classList.remove('bg-sky-500', 'text-white', 'border-transparent');
            }
            updateTeamRanking(teamId, currentType); // Recargar
        }

        // 2. Funcion unica para dibujar la lista
        function renderPlayerPage() {
            const list = document.getElementById('player-ranking-list');
            const info = document.getElementById('player-page-info');
            
            const start = (playerPage - 1) * playersPerPage;
            const visibleData = teamPlayerData.slice(start, start + playersPerPage);
            const totalPages = Math.ceil(teamPlayerData.length / playersPerPage) || 1;

            info.innerText = `${playerPage} / ${totalPages}`;
            
            list.innerHTML = visibleData.map(r => `
                <div class="player-card ${r.is_transferred ? 'border-red-500/50' : ''}" data-pid="${r.player_id}">
                    <div class="flex justify-between items-center gap-2">
                        <span class="font-bold truncate text-[14px] ${r.is_transferred ? 'text-red-400' : 'text-slate-200'}">
                            <span class="text-sky-500 mr-1">#${r.number || '-'} </span>${r.name}<span class="text-slate-500 text-[11px] italic"> (${r.pos})</span>
                        </span>
                        <span class="text-[12px] font-bold italic whitespace-nowrap ${r.is_transferred ? 'text-red-400' : 'text-slate-400'} text-right">
                            <span class="${r.is_transferred ? 'text-red-400' : 'text-sky-400'} font-black text-[14px]">${r.val}</span> ${r.unit} en <span class="${r.is_transferred ? 'text-red-400' : 'text-sky-400'} font-black text-[13px]">${r.pj}</span> PJ | <span class="text-emerald-400">${r.avg}</span> / 90
                        </span>
                    </div>
                </div>`).join('') || '<p class="text-[10px] text-slate-600 text-center italic py-10">Sin datos.</p>';
        }

        // 3. Cambiar de pagina
        function changePage(delta) {
            const totalPages = Math.ceil(teamPlayerData.length / playersPerPage) || 1;
            let next = playerPage + delta;
            if (next >= 1 && next <= totalPages) {
                playerPage = next;
                renderPlayerPage();
            }
        }

        // 4. Cargar datos desde la API
        function updateTeamRanking(tId, rankType, shotFilter = 'all', e = null) {
            currentType = rankType;
            
            // Manejo de UI de botones
            if (e && e.currentTarget) {
                const isSubBtn = e.currentTarget.classList.contains('sub-btn');
                const buttons = document.querySelectorAll(isSubBtn ? `#sub-filters button` : `.rank-btn`);
                buttons.forEach(b => { 
                    b.classList.remove('bg-sky-500', 'text-white'); 
                    b.classList.add('bg-slate-800', 'text-slate-500'); // text-slate-400 was used in HTML but JS sets 500
                });
                e.currentTarget.classList.remove('bg-slate-800', 'text-slate-500', 'text-slate-400');
                e.currentTarget.classList.add('bg-sky-500', 'text-white');
            }

            const subMenu = document.getElementById(`sub-filters`);
            if (rankType === 'tiradores') { 
                subMenu.style.display = 'flex';
                const subBtns = subMenu.querySelectorAll('button');
                subBtns.forEach(b => {
                    const type = b.id.split('-').pop(); // all, target, long
                    if (type === shotFilter) {
                        b.classList.remove('bg-slate-800', 'text-slate-500');
                        b.classList.add('bg-sky-500', 'text-white');
                    } else {
                        b.classList.add('bg-slate-800', 'text-slate-500');
                        b.classList.remove('bg-sky-500', 'text-white');
                    }
                });
            } else { 
                subMenu.style.display = 'none'; 
            }

            
            const limit = isL5 ? 5 : '';
            fetch(`/api/team_ranking/${tId}?type=${rankType}&filter=${shotFilter}&limit=${limit}`)
                .then(r => r.json())
                .then(data => { 
                    teamPlayerData = data; 
                    playerPage = 1; 
                    renderPlayerPage(); 
                });
        }

        window.onload = () => { updateTeamRanking(teamId, 'tiradores'); };
    </script>
    <div class="max-w-[80dvw] mx-auto space-y-12">
        <!-- HEADER -->
        <header class="flex justify-between items-center">
            <div>
                <a href="/" class="text-sky-500 font-black uppercase text-xs tracking-widest hover:underline">← Volver a Partidos</a>
                <div class="flex items-center gap-6 mt-2">
                    <img src="{{ url_for('static', filename=team_id ~ '.png') }}" onerror="this.onerror=null; this.src='{{ url_for('static', filename='none.png') }}'" class="w-24 h-24 object-contain drop-shadow-2xl">
                    <h1 class="text-6xl font-black italic uppercase tracking-tighter text-white">{{ get_short_name(team_name) }}</h1>
                </div>
            </div>
            <div class="bg-slate-800 p-4 rounded-3xl border border-slate-700 text-center min-w-[200px]">
                <span class="text-[10px] font-black text-slate-500 uppercase block mb-1">ID de Equipo</span>
                <span class="text-2xl font-mono font-black text-sky-400">{{ team_id }}</span>
            </div>
        </header>

        <div class="grid grid-cols-1 lg:grid-cols-3 gap-10">

            <!-- ULTIMOS PARTIDOS -->
            <div class="space-y-6">
                <div class="flex justify-between items-center border-l-4 border-slate-500 pl-4">
                    <h2 class="text-xl font-black uppercase italic tracking-tighter">Partidos</h2>
                    <div class="flex bg-slate-800 p-1 rounded-full border border-slate-700 text-[9px]">
                        <button onclick="showMatches('finished')" id="btn-finished" class="px-3 py-1 rounded-full font-black uppercase transition-all bg-sky-500 text-white shadow-lg">Pasados</button>
                        <button onclick="showMatches('upcoming')" id="btn-upcoming" class="px-3 py-1 rounded-full font-black uppercase transition-all text-slate-500 hover:text-white">Proximos</button>
                    </div>
                </div>
                
                <!-- LISTA PARTIDOS FINALIZADOS -->
                <div id="matches-finished" class="space-y-3 max-h-[600px] overflow-y-auto pr-2 custom-scroll">
                    {% for m in matches_finished %}
                    <a href="/match/{{ m.id }}" class="block bg-slate-900/50 p-4 rounded-2xl border border-slate-800 hover:border-sky-500 transition-all">
                        <div class="flex justify-between text-[10px] font-black text-slate-500 uppercase mb-2">
                            <span>{{ m.date[:10] }}</span>
                            <span>{{ m.tournament }} Fecha {{ m.gameweek }}</span>
                        </div>
                        <div class="flex justify-between items-center text-[14px]">
                            <div class="flex items-center gap-2 w-[140px]">
                                <img src="{{ url_for('static', filename=m.id_home_team ~ '.png') }}" onerror="this.onerror=null; this.src='{{ url_for('static', filename='none.png') }}'" class="w-6 h-6 object-contain">
                                <span class="font-bold whitespace-nowrap overflow-hidden text-ellipsis {{ 'text-sky-400' if m.id_home_team|string == team_id|string else 'text-slate-400' }}">{{ get_short_name(m.home_team) }}</span>
                            </div>
                            <span class="bg-slate-800 px-3 py-1 rounded-lg font-mono font-black">{{ m.score or 'VS' }}</span>
                            <div class="flex items-center gap-2 w-[140px] justify-end">
                                <span class="text-right font-bold whitespace-nowrap overflow-hidden text-ellipsis {{ 'text-sky-400' if m.id_away_team|string == team_id|string else 'text-slate-400' }}">{{ get_short_name(m.away_team) }}</span>
                                <img src="{{ url_for('static', filename=m.id_away_team ~ '.png') }}" onerror="this.onerror=null; this.src='{{ url_for('static', filename='none.png') }}'" class="w-6 h-6 object-contain">
                            </div>
                        </div>
                    </a>
                    {% else %}
                    <p class="text-center text-xs text-slate-500 italic py-4">No hay partidos finalizados.</p>
                    {% endfor %}
                </div>

                <!-- LISTA PARTIDOS PROXIMOS -->
                <div id="matches-upcoming" class="space-y-3 max-h-[600px] overflow-y-auto pr-2 custom-scroll hidden">
                    {% for m in matches_upcoming %}
                    <a href="/match/{{ m.id }}" class="block bg-slate-900/50 p-4 rounded-2xl border border-slate-800 hover:border-sky-500 transition-all">
                        <div class="flex justify-between text-[10px] font-black text-slate-500 uppercase mb-2">
                            <span>{{ m.date[:10] }}</span>
                            <span>{{ m.tournament }} Fecha {{ m.gameweek }}</span>
                        </div>
                        <div class="flex justify-between items-center text-[14px]">
                            <div class="flex items-center gap-2 w-[140px]">
                                <img src="{{ url_for('static', filename=m.id_home_team ~ '.png') }}" onerror="this.onerror=null; this.src='{{ url_for('static', filename='none.png') }}'" class="w-6 h-6 object-contain">
                                <span class="font-bold whitespace-nowrap overflow-hidden text-ellipsis {{ 'text-sky-400' if m.id_home_team|string == team_id|string else 'text-slate-400' }}">{{ get_short_name(m.home_team) }}</span>
                            </div>
                            <span class="bg-slate-800 px-3 py-1 rounded-lg font-mono font-black text-emerald-400 animate-pulse">VS</span>
                            <div class="flex items-center gap-2 w-[140px] justify-end">
                                <span class="text-right font-bold whitespace-nowrap overflow-hidden text-ellipsis {{ 'text-sky-400' if m.id_away_team|string == team_id|string else 'text-slate-400' }}">{{ get_short_name(m.away_team) }}</span>
                                <img src="{{ url_for('static', filename=m.id_away_team ~ '.png') }}" onerror="this.onerror=null; this.src='{{ url_for('static', filename='none.png') }}'" class="w-6 h-6 object-contain">
                            </div>
                        </div>
                    </a>
                    {% else %}
                    <p class="text-center text-xs text-slate-500 italic py-4">No hay partidos proximos programados.</p>
                    {% endfor %}
                </div>
            </div>
            <!-- POSICIONES GLOBALES -->
            <div class="space-y-6">
                <h2 class="text-xl font-black uppercase italic tracking-tighter border-l-4 border-sky-500 pl-4">Posiciones en Estadisticas</h2>
                {# Macro para definir el color de la posicion basado en tu funcion #}
                {% macro get_pos_color(v) -%}
                    {% if v == 'N/A' %}text-slate-500
                    {% elif v|int > 20 %}text-red-500
                    {% elif v|int > 10 %}text-blue-500
                    {% else %}text-green-500
                    {% endif %}
                {%- endmacro %}
                <div class="bg-slate-800/20 rounded-[2rem] m-auto border border-slate-700/50 overflow-hidden">
                    {% for pair in global_ranks %}
                    <div class="grid grid-cols-2 border-b border-slate-700/30 last:border-0 hover:bg-slate-700/10 transition-colors">
                        <div class="p-4 flex justify-between items-center border-r border-slate-700/30">
                            <div class="flex flex-col">
                                <span class="text-[12px] font-black text-slate-500 uppercase tracking-widest">{{ pair.made.label }}</span>
                                <div class="flex items-baseline gap-2">
                                    <span class="text-[16px] font-black {{ get_pos_color(pair.made.pos) }}">#{{ pair.made.pos }}</span>
                                    <span class="text-[11px] text-slate-400 font-bold uppercase">Total: <b class="text-sky-400">{{ pair.made.total }}</b></span>
                                    <span class="text-[11px] text-slate-400 font-bold uppercase">PJ: <b class="text-sky-400">{{ pair.made.pj }}</b></span>
                                </div>
                            </div>
                        </div>
                        <div class="p-4 flex justify-between items-center bg-red-500/5">
                            <div class="flex flex-col">
                                <span class="text-[12px] font-black text-slate-500 uppercase tracking-widest">{{ pair.against.label }}</span>
                                <div class="flex items-baseline gap-2">
                                    <span class="text-[16px] font-black {{ get_pos_color(pair.against.pos) }}">#{{ pair.against.pos }}</span>
                                    <span class="text-[11px] text-slate-400 font-bold uppercase">Total: <b class="text-sky-400">{{ pair.against.total }}</b></span>
                                    <span class="text-[11px] text-slate-400 font-bold uppercase">PJ: <b class="text-sky-400">{{ pair.against.pj }}</b></span>
                                </div>
                            </div>
                        </div>
                    </div>
                    {% endfor %}
                </div>
            </div>

            <!-- ESTADISTICAS PLANTEL -->
            <div class="space-y-3">
                <div class="flex justify-between items-center border-l-4 border-orange-500 pl-4">
                    <h2 class="text-xl font-black uppercase italic tracking-tighter">Estadisticas Plantel</h2>
                    <button onclick="toggleL5()" id="l5-btn" class="text-[9px] px-3 py-1 rounded-full border border-slate-700 font-black uppercase text-slate-500 hover:text-white transition-all">Ultimos 5 Partidos</button>
                </div>
                <div class="flex flex-col items-center border-b border-sky-400/20 pb-2 mb-3">
                    <div class="flex flex-wrap justify-center gap-1 mb-2 text-[12px]">
                        <button onclick="updateTeamRanking('{{ team_id }}', 'tiradores', 'all', event)" class="px-1.5 py-0.5 rounded bg-sky-500 text-white font-bold rank-btn" id="btn-main">Tiros</button>
                        <button onclick="updateTeamRanking('{{ team_id }}', 'goals', 'all', event)" class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-bold rank-btn">Goles</button>
                        <button onclick="updateTeamRanking('{{ team_id }}', 'headers', 'all', event)" class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-bold rank-btn">Cabezazos</button>
                        <button onclick="updateTeamRanking('{{ team_id }}', 'yellows', 'all', event)" class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-bold rank-btn">Tarjetas</button>
                        <button onclick="updateTeamRanking('{{ team_id }}', 'fouls', 'all', event)" class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-bold rank-btn">Faltas</button>
                        <button onclick="updateTeamRanking('{{ team_id }}', 'fouls_rec', 'all', event)" class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-bold rank-btn">Faltas Rec.</button>
                    </div>
                    <div id="sub-filters" class="sub-menu flex gap-1 justify-center mt-2 text-[11px]" style="display:none;">
                        <button onclick="updateTeamRanking('{{ team_id }}', 'tiradores', 'all', event)" id="sub-all" class="px-1.5 py-0.5 rounded bg-sky-500 text-white font-black sub-btn">Todos</button>
                        <button onclick="updateTeamRanking('{{ team_id }}', 'tiradores', 'target', event)" id="sub-target" class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-500 font-black sub-btn">Arco</button>
                        <button onclick="updateTeamRanking('{{ team_id }}', 'tiradores', 'long', event)" id="sub-long" class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-500 font-black sub-btn">Lejos</button>
                    </div>
                </div>

                <div id="player-ranking-list" class="space-y-1"></div> 
                <div class="flex justify-center gap-4 mt-2">
                    <button onclick="changePage(-1)" class="text-sky-400 hover:text-white">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="m15 18-6-6 6-6"/></svg>
                    </button>

                    <span id="player-page-info" class="text-[10px] font-black text-slate-500 uppercase mt-0.5">1 / 1</span> 
                    
                    <button onclick="changePage(1)" class="text-sky-400 hover:text-white">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="m9 18 6-6-6-6"/></svg>
                    </button>
                </div>
            </div>
        </div>
    </div>
    '''+FOOTER_HTML+'''</body></html>'''

REFEREE_HTML = '''
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="author" content="MartinezGalo & francoqdev">
    <meta name="copyright" content="ARG STATS">

    <title>arbitro: {{ ref_name }} - ARG STATS</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body{background-color:#0f172a;color:#f8fafc;}
        .custom-scroll::-webkit-scrollbar { width: 6px; }
        .custom-scroll::-webkit-scrollbar-track { background: #1e293b; border-radius: 10px; }
        .custom-scroll::-webkit-scrollbar-thumb { background: #334155; border-radius: 10px; }
        .custom-scroll::-webkit-scrollbar-thumb:hover { background: #0ea5e9; }
    </style>
</head>
<body class="p-8 font-sans">
    <div class="max-w-[80dvw] mx-auto space-y-12">
        <!-- HEADER -->
        <header class="flex justify-between items-center">
            <div>
                <a href="/" class="text-sky-500 font-black uppercase text-xs tracking-widest hover:underline">← Volver a Partidos</a>
                <h1 class="text-6xl font-black italic uppercase tracking-tighter text-white mt-2">{{ ref_name }}</h1>
            </div>
            <div class="flex gap-4">
                <div class="bg-slate-800 p-4 rounded-3xl border border-slate-700 text-center min-w-[150px]">
                    <span class="text-[10px] font-black text-slate-500 uppercase block mb-1">Rank Tarjetas</span>
                    <span class="text-2xl font-mono font-black text-yellow-500">#{{ ranks.cards }}</span>
                </div>
                <div class="bg-slate-800 p-4 rounded-3xl border border-slate-700 text-center min-w-[150px]">
                    <span class="text-[10px] font-black text-slate-500 uppercase block mb-1">Rank Faltas</span>
                    <span class="text-2xl font-mono font-black text-sky-400">#{{ ranks.fouls }}</span>
                </div>
            </div>
        </header>

        <div class="grid grid-cols-1 lg:grid-cols-3 gap-10">
            <!-- LISTA PARTIDOS -->
            <div class="space-y-6">
                <h2 class="text-xl font-black uppercase italic tracking-tighter border-l-4 border-slate-500 pl-4">Partidos Dirigidos</h2>
                <div class="space-y-4 max-h-[700px] overflow-y-auto pr-2 custom-scroll">
                    {% for m in matches %}
                    <div class="bg-slate-900/50 p-4 rounded-2xl border border-slate-800 text-[15px]">
                        <a href="/match/{{ m.id }}">
                            <div class="flex justify-between text-[11px] font-black text-slate-500 uppercase mb-3">
                                <span>{{ m.date[:10] }}</span>
                                <span>{{ m.tournament }}</span>
                            </div>
                            <div class="grid grid-cols-3 items-center gap-2 mb-3 text-[14px]">
                                <div class="flex flex-col items-center gap-1 overflow-hidden">
                                    <img src="{{ url_for('static', filename=m.id_home_team ~ '_xsmall.png') }}" onerror="this.onerror=null; this.src='{{ url_for('static', filename='none.png') }}'" class="w-8 h-8 object-contain">
                                    <span class="font-bold text-center truncate w-full">{{ get_short_name(m.home_team) }}</span>
                                </div>
                                <span class="bg-slate-800 py-1 rounded-lg font-mono font-black text-center">{{ m.score or 'VS' }}</span>
                                <div class="flex flex-col items-center gap-1 overflow-hidden">
                                    <img src="{{ url_for('static', filename=m.id_away_team ~ '_xsmall.png') }}" onerror="this.onerror=null; this.src='{{ url_for('static', filename='none.png') }}'" class="w-8 h-8 object-contain">
                                    <span class="font-bold text-center truncate w-full">{{ get_short_name(m.away_team) }}</span>
                                </div>
                            </div>
                            <div class="grid grid-cols-2 gap-4 border-t border-slate-800 pt-3">
                                <div class="text-center">
                                    <p class="text-[11px] font-black text-slate-500 uppercase">Local</p>
                                    <p class="text-[12px] font-bold"><span class="text-yellow-500">{{ m.stats.h_cards }} Tarj.</span> | <span class="text-sky-400">{{ m.stats.h_fouls }} Faltas</span></p>
                                </div>
                                <div class="text-center">
                                    <p class="text-[11px] font-black text-slate-500 uppercase">Visita</p>
                                    <p class="text-[12px] font-bold"><span class="text-yellow-500">{{ m.stats.v_cards }} Tarj.</span> | <span class="text-sky-400">{{ m.stats.v_fouls }} Faltas</span></p>
                                </div>
                            </div>
                        </a>
                    </div>
                    {% endfor %}
                </div>
            </div>

            <!-- ANALISIS TENDENCIAS -->
            <div class="lg:col-span-2 space-y-8">
                <h2 class="text-xl font-black uppercase italic tracking-tighter border-l-4 border-red-500 pl-4">Analisis de Tendencias por Equipo</h2>
                
                <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <!-- TARJETAS POR EQUIPO -->
                    <div class="bg-slate-800/30 p-6 rounded-[2rem] border border-slate-700/50">
                        <h3 class="text-sm font-black text-yellow-500 uppercase mb-4 tracking-widest">Equipos con mas Tarjetas</h3>
                        <div class="space-y-2">
                            {% for t in top_targets.cards %}
                            <div class="flex justify-between items-center bg-slate-900/40 p-3 rounded-xl border border-slate-800">
                                <div class="flex items-center gap-2 overflow-hidden">
                                    <img src="{{ url_for('static', filename=t.id ~ '_xsmall.png') }}" onerror="this.onerror=null; this.src='{{ url_for('static', filename='none.png') }}'" class="w-6 h-6 object-contain">
                                    <a href="/team/{{ t.id }}" class="font-bold text-sm truncate hover:text-yellow-500">{{ get_short_name(t.name) }}</a>
                                </div>
                                <span class="text-xs font-black"><span class="text-yellow-500 text-lg">{{ t.total }}</span> T en {{ t.pj }} PJ</span>
                            </div>
                            {% endfor %}
                        </div>
                    </div>

                    <!-- FALTAS COMETIDAS -->
                    <div class="bg-slate-800/30 p-6 rounded-[2rem] border border-slate-700/50">
                        <h3 class="text-sm font-black text-red-500 uppercase mb-4 tracking-widest">Mas Faltas Cometidas (En contra)</h3>
                        <div class="space-y-2">
                            {% for t in top_targets.fouls_committed %}
                            <div class="flex justify-between items-center bg-slate-900/40 p-3 rounded-xl border border-slate-800">
                                <div class="flex items-center gap-2 overflow-hidden">
                                    <img src="{{ url_for('static', filename=t.id ~ '_xsmall.png') }}" onerror="this.onerror=null; this.src='{{ url_for('static', filename='none.png') }}'" class="w-6 h-6 object-contain">
                                    <a href="/team/{{ t.id }}" class="font-bold text-sm truncate hover:text-red-500">{{ get_short_name(t.name) }}</a>
                                </div>
                                <span class="text-xs font-black"><span class="text-red-500 text-lg">{{ t.total }}</span> F en {{ t.pj }} PJ</span>
                            </div>
                            {% endfor %}
                        </div>
                    </div>

                    <!-- FALTAS RECIBIDAS -->
                    <div class="bg-slate-800/30 p-6 rounded-[2rem] border border-slate-700/50">
                        <h3 class="text-sm font-black text-emerald-500 uppercase mb-4 tracking-widest">Mas Faltas Recibidas (A favor)</h3>
                        <div class="space-y-2">
                            {% for t in top_targets.fouls_received %}
                            <div class="flex justify-between items-center bg-slate-900/40 p-3 rounded-xl border border-slate-800">
                                <div class="flex items-center gap-2 overflow-hidden">
                                    <img src="{{ url_for('static', filename=t.id ~ '_xsmall.png') }}" onerror="this.onerror=null; this.src='{{ url_for('static', filename='none.png') }}'" class="w-6 h-6 object-contain">
                                    <a href="/team/{{ t.id }}" class="font-bold text-sm truncate hover:text-emerald-500">{{ get_short_name(t.name) }}</a>
                                </div>
                                <span class="text-xs font-black"><span class="text-emerald-500 text-lg">{{ t.total }}</span> F en {{ t.pj }} PJ</span>
                            </div>
                            {% endfor %}
                        </div>
                    </div>

                    <!-- PROMEDIOS -->
                    <div class="bg-sky-500/5 p-6 rounded-[2rem] border border-sky-500/20 flex flex-col justify-center text-center">
                        <p class="text-sky-400 font-black uppercase text-[10px] tracking-[0.3em] mb-2">Promedio General</p>
                        <div class="flex justify-around">
                            <div>
                                <p class="text-3xl font-black text-white">{{ stats_avg.cards }}</p>
                                <p class="text-[9px] text-slate-500 font-bold uppercase">Tarjetas / Part</p>
                            </div>
                            <div>
                                <p class="text-3xl font-black text-white">{{ stats_avg.fouls }}</p>
                                <p class="text-[9px] text-slate-500 font-bold uppercase">Faltas / Part</p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    '''+FOOTER_HTML+'''</body></html>'''

DETAIL_HTML = '''
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8"><title>{{ match.home_team }} vs {{ match.away_team }}</title>
    <meta charset="UTF-8">
    <meta name="author" content="MartinezGalo & francoqdev">
    <meta name="copyright" content="ARG STATS">
    <link rel="icon" href="{{ url_for('static', filename='lpf.png') }}?v=2" type="image/png">
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { background-color: #0f172a; color: #f8fafc; overflow-x: hidden; }
        .pitch { position: relative; width: 100%; max-width: 500px; margin: 0 auto; aspect-ratio: 2 / 3; background-color: #1a4d2e; border: 4px solid #ffffff1a; border-radius: 24px; overflow: hidden; background-image: linear-gradient(to bottom, transparent 49.5%, #ffffff1a 50%, #ffffff1a 50.5%, transparent 51%), radial-gradient(circle at 50% 50%, transparent 14%, #ffffff1a 14.5%, #ffffff1a 15.5%, transparent 16%); box-shadow: 0 20px 50px rgba(0,0,0,0.5); }
        .pitch::before, .pitch::after { content: ""; position: absolute; left: 20%; width: 60%; height: 15%; border: 3px solid #ffffff1a; }
        .pitch::before { top: 0; border-top: 0; } .pitch::after { bottom: 0; border-bottom: 0; }
        .player-dot { position: absolute; width: 34px; height: 34px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 13px; font-weight: 900; transform: translate(-50%, -50%); border: 2px solid white; cursor: grab; z-index: 10; transition: transform 0.2s, border-color 0.2s, box-shadow 0.2s; user-select: none; }
        .player-dot:active { cursor: grabbing; z-index: 100 !important; transition: none !important; }
        .key-player { border-color: #fbbf24 !important; box-shadow: 0 0 15px #fbbf24 !important; }
        .selected-player { z-index: 50; }
        .lasso-selected { border-color: #38bdf8 !important; box-shadow: 0 0 15px #38bdf8 !important; z-index: 50; }
        .highlight-player { border-color: #f8fafc !important; box-shadow: 0 0 25px #f8fafc !important; transform: translate(-50%, -50%) scale(1.3) !important; z-index: 100; }
        .sub-highlight-red {  box-shadow: 0 0 25px 10px #ef4444 !important; transform: translate(-50%, -50%) scale(1.3) !important; z-index: 100; }
        .sub-highlight-green { box-shadow: 0 0 10px #22c55e !important; }
        .active-hover { border-color: #38bdf8 !important; background: rgba(56, 189, 248, 0.1) !important; }
        .card-badge { position: absolute; top: -5px; right: -5px; width: 12px; height: 16px; border-radius: 2px; border: 1px solid rgba(0,0,0,0.3); }
        .card-Yellow { background-color: #fbbf24; } .card-Red { background-color: #ef4444; } .card-YellowRed { background: linear-gradient(135deg, #fbbf24 50%, #ef4444 50%); }
        .player-name { position: absolute; top: 38px; left: 50%; transform: translateX(-50%); white-space: nowrap; background: rgba(15, 23, 42, 0.95); padding: 2px 6px; border-radius: 6px; font-size: 12px; font-weight: 700; border: 1px solid #334155; }
        .no-scrollbar::-webkit-scrollbar { display: none; } .no-scrollbar { -ms-overflow-style: none; scrollbar-width: none; }
        #context-menu { display: none; position: fixed; background: #1e293b; border: 1px solid #334155; border-radius: 12px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); z-index: 2000; min-width: 160px; overflow: hidden; }
        .context-item { padding: 12px 16px; font-size: 12px; font-weight: 700; color: #cbd5e1; cursor: pointer; display: flex; align-items: center; gap: 10px; transition: all 0.2s; }
        .context-item:hover { background: #334155; color: white; }
        .context-header { padding: 10px 16px; border-bottom: 1px solid #334155; background: #0f172a; color: #0ea5e9; font-size: 10px; font-weight: 900; text-transform: uppercase; letter-spacing: 1px; }
        #selection-box { display: none; position: fixed; background: rgba(56, 189, 248, 0.2); border: 1px solid #38bdf8; z-index: 1500; pointer-events: none; }
        #modal-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.8); backdrop-filter: blur(8px); z-index: 3000; align-items: center; justify-content: center; }

        #player-modal { 
            background: #111827; 
            width: 95%; 
            max-width: 1100px; 
            height: 750px !important; /* Altura fija obligatoria */
            border-radius: 2.5rem; 
            border: 1px solid #334155; 
            padding: 2.5rem; 
            display: flex !important; /* Forzamos flexbox */
            flex-direction: column !important;
            overflow: hidden !important; /* Evita que la modal crezca */
        }

        /* Asegura que el cuerpo de la modal no se desborde */
        .modal-body-grid {
            display: grid !important;
            grid-template-columns: repeat(12, 1fr);
            gap: 2.5rem;
            flex: 1 !important; 
            min-height: 0 !important; /* Vital para que los hijos puedan scrollear */
            overflow: hidden; 
        }
        /* Asegura que el contenido interno de la modal respete el limite de 750px */
        #modal-content {
            height: 100%;
            display: flex;
            flex-direction: column;
            overflow: hidden; /* Evita que la modal entera scrollee */
        }

        /* Forzamos que las notas no se muevan nunca */
        .notes-section {
            height: 160px; /* Altura fija para notas */
            flex-shrink: 0; /* Prohibe que se encoja */
        }

        /* Ajuste para las tarjetas de ranking */
        .rank-badge {
            background: #0f172a;
            border: 1px solid #1e293b;
            border-radius: 1.25rem;
            padding: 1rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            min-height: 80px; /* Altura consistente */
        }

        .custom-blue-scroll::-webkit-scrollbar { width: 6px; }
        .custom-blue-scroll::-webkit-scrollbar-track { background: #0f172a; }
        .custom-blue-scroll::-webkit-scrollbar-thumb { background: #0ea5e9; border-radius: 10px; }
        .custom-blue-scroll::-webkit-scrollbar-thumb:hover { background: #38bdf8; }

        .player-card { background: rgba(15, 23, 42, 0.6); padding: 0.25rem 0.5rem; border-radius: 0.5rem; border: 1px solid #1e293b; transition: all 0.2s; cursor: pointer; }
        .sub-menu { display: none; animation: slideDown 0.2s ease-out; }
        #home-ranking-list, #away-ranking-list { height: 350px; min-height: 350px; overflow: hidden; display: flex; flex-direction: column; }
    </style>
</head>
<body class="p-6">

        <script>
        const homeMatchId = "{{ h_mid }}";
        const awayMatchId = "{{ a_mid }}";
        let pitch, draggables, contextMenu, selectionBox;
        document.addEventListener('DOMContentLoaded', () => {
            pitch = document.getElementById('soccer-pitch');
            draggables = document.querySelectorAll('.draggable');
            contextMenu = document.getElementById('context-menu');
            selectionBox = document.getElementById('selection-box');
        });
        let activePlayer = null, lastCtxPid = null, selectedPlayers = [], currentPlayerShots = [];
        let isLassoing = false, startX, startY, pitchIsReversed = false;
        const rankingsData = { home: [], away: [] }, currentPages = { home: 1, away: 1 }, perPage = 10;
        const locks = { home: false, away: false };
        const l5_active = { home: false, away: false };
        const match_focus = { home: false, away: false };

        function getScoreColorClass(v) { if (v <= 30) return 'text-red-500'; if (v <= 70) return 'text-blue-500'; return 'text-green-500'; }
        function getPosColorClass(v) { if (v > 20) return 'text-red-500'; if (v > 10) return 'text-blue-500'; return 'text-green-500'; }

        function highlightTarget(pid, active) {
            document.querySelectorAll(`[data-pid="${pid}"]`).forEach(el => {
                if (el.classList.contains('player-dot')) active ? el.classList.add('highlight-player') : el.classList.remove('highlight-player');
                else if (el.classList.contains('list-item-hover-only')) active ? el.classList.add('active-hover') : el.classList.remove('active-hover');
                else if (el.classList.contains('player-card')) active ? el.classList.add('active-hover') : el.classList.remove('active-hover');
            });
        }
        
        function toggleRankingL5(side, teamId) {
            // Si estaba en modo 'partido', desactivamos ese visualmente y pasamos a L5
            l5_active[side] = !l5_active[side];
            
            // Si activamos L5, desactivamos el modo Partido
            if (l5_active[side]) match_focus[side] = false;

            const btnL5 = document.getElementById(`${side === 'home' ? 'h' : 'v'}-l5-btn`);
            const btnPart = document.getElementById(`${side === 'home' ? 'h' : 'v'}-part-btn`);
            const contextDiv = document.getElementById(`${side}-l5-context`);
            const lastMatchDiv = document.getElementById(`${side}-last-match-context`);
            
            // Resetear el otro boton
            if (l5_active[side]) {
                btnPart.classList.remove('bg-sky-500', 'text-white');
                btnPart.classList.add('text-slate-500');
                if(contextDiv) { contextDiv.classList.remove('hidden'); contextDiv.classList.add('flex'); }
                if(lastMatchDiv) { lastMatchDiv.classList.add('hidden'); lastMatchDiv.classList.remove('flex'); }
            } else {
                if(contextDiv) { contextDiv.classList.add('hidden'); contextDiv.classList.remove('flex'); }
            }

            btnL5.classList.toggle('bg-sky-500', l5_active[side]);
            btnL5.classList.toggle('text-white', l5_active[side]);
            btnL5.classList.toggle('text-slate-500', !l5_active[side]);
            
            refreshCurrentRanking(side, teamId);
        }

        function toggleRankingMatch(side, teamId) {
            match_focus[side] = !match_focus[side];
            
            // Si activamos "Partido", desactivamos "L5"
            if (match_focus[side]) l5_active[side] = false;

            // Actualizar UI de botones
            const btnPart = document.getElementById(`${side === 'home' ? 'h' : 'v'}-part-btn`);
            const btnL5 = document.getElementById(`${side === 'home' ? 'h' : 'v'}-l5-btn`);
            const l5Div = document.getElementById(`${side}-l5-context`);
            const lastMatchDiv = document.getElementById(`${side}-last-match-context`);
            
            btnPart.classList.toggle('bg-sky-500', match_focus[side]);
            btnPart.classList.toggle('text-white', match_focus[side]);
            
            btnL5.classList.remove('bg-sky-500', 'text-white');
            btnL5.classList.add('text-slate-500');

            if (match_focus[side]) {
                if(l5Div) { l5Div.classList.add('hidden'); l5Div.classList.remove('flex'); }
                // Mostrar contexto ultimo partido SOLO si tiene contenido
                if(lastMatchDiv && lastMatchDiv.innerText.trim().length > 0) {
                     lastMatchDiv.classList.remove('hidden');
                     lastMatchDiv.classList.add('flex');
                }
            } else {
                if(lastMatchDiv) { lastMatchDiv.classList.add('hidden'); lastMatchDiv.classList.remove('flex'); }
            }

            refreshCurrentRanking(side, teamId);
        }

        function refreshCurrentRanking(side, teamId) {
            const sideCode = side === 'home' ? 'h' : 'v';
            const activeMain = document.querySelector(`.${sideCode}-rank-btn.bg-sky-500`);
            const type = activeMain ? activeMain.getAttribute('data-type') : 'tiradores';
            updateTeamRanking(side, teamId, type, 'all', null);
        }


        function togglePitchOrientation() {
            pitchIsReversed = !pitchIsReversed;
            draggables.forEach(p => {
                let val = (p.style.bottom && p.style.bottom !== 'auto') ? p.style.bottom : p.style.top;
                if (p.dataset.side === 'home') { p.style.top = pitchIsReversed ? val : 'auto'; p.style.bottom = pitchIsReversed ? 'auto' : val; }
                else { p.style.bottom = pitchIsReversed ? val : 'auto'; p.style.top = pitchIsReversed ? 'auto' : val; }
                let currentLeft = parseFloat(p.style.left);
                p.style.left = (100 - currentLeft) + '%';
            });
        }

        function toggleLock(side) {
            locks[side] = !locks[side];
            const btn = document.getElementById(`lock-${side}-btn`);
            btn.innerHTML = locks[side] ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>' : (side === 'home' ? 'L' : 'V');
            btn.classList.toggle('bg-sky-500', locks[side]); btn.classList.toggle('text-white', locks[side]);
        }

        function renderRankingPage(side) {
            const data = rankingsData[side], page = currentPages[side], list = document.getElementById(`${side}-ranking-list`);
            const start = (page - 1) * perPage, total = Math.ceil(data.length / perPage) || 1;
            document.getElementById(`${side}-page-info`).innerText = `${page} / ${total}`;
            list.innerHTML = data.slice(start, start + perPage).map(r => `
                <div class="player-card ${r.is_transferred ? 'border-red-500/50' : ''}" data-pid="${r.player_id}" 
                     onmouseenter="highlightTarget('${r.player_id}', true)" 
                     onmouseleave="highlightTarget('${r.player_id}', false)"
                     onclick="handlePlayerClick(event, '${r.player_id}')">
                    <div class="flex justify-between items-center gap-2">
                        <span class="font-bold truncate text-[14px] ${r.is_transferred ? 'text-red-400' : 'text-slate-200'}">
                            <span class="text-sky-500 mr-1">#${r.number || '-'} </span>${r.name}<span class="text-slate-500 text-[11px] italic"> (${r.pos})</span>
                        </span>
                        <span class="text-[12px] font-bold italic whitespace-nowrap ${r.is_transferred ? 'text-red-400' : 'text-slate-400'} ${side === 'away' ? 'text-left' : 'text-right'}">
                            <span class="${r.is_transferred ? 'text-red-400' : 'text-sky-400'} font-black text-[14px]">${r.val}</span> ${r.unit} en <span class="${r.is_transferred ? 'text-red-400' : 'text-sky-400'} font-black text-[13px]">${r.pj}</span> PJ | <span class="text-emerald-400">${r.avg}</span> / 90
                        </span>
                    </div>
                </div>`).join('') || '<p class="text-[10px] text-slate-600 text-center italic py-4">Sin datos.</p>';
        }

        function changePage(side, delta) {
            const total = Math.ceil(rankingsData[side].length / perPage) || 1;
            let next = currentPages[side] + delta;
            if (next < 1) next = 1; if (next > total) next = total;
            currentPages[side] = next;
            renderRankingPage(side);
        }
        
        function updateTeamRanking(side, teamId, rankType, shotFilter = 'all', e = null) {
            const sideCode = side === 'home' ? 'h' : 'v';
            if (e && e.currentTarget) {
                const isSubBtn = e.currentTarget.classList.contains('h-sub-btn') || e.currentTarget.classList.contains('v-sub-btn');
                const buttons = document.querySelectorAll(isSubBtn ? `#${side}-sub-filters button` : `.${sideCode}-rank-btn`);

                buttons.forEach(b => { b.classList.remove('bg-sky-500', 'text-white'); b.classList.add('bg-slate-800', 'text-slate-400', 'text-slate-500'); });
                e.currentTarget.classList.add('bg-sky-500', 'text-white');
                e.currentTarget.classList.remove('bg-slate-800', 'text-slate-400', 'text-slate-500');
            }
            const subMenu = document.getElementById(`${side}-sub-filters`);
            if (rankType === 'tiradores') { 
                subMenu.style.display = 'flex';

                // Actualizar estado visual de sub-filtros
                const subBtns = subMenu.querySelectorAll('button');
                subBtns.forEach(b => {
                    const type = b.id.split('-').pop(); // all, target, long
                    if (type === shotFilter) {
                        b.classList.remove('bg-slate-800', 'text-slate-500');
                        b.classList.add('bg-sky-500', 'text-white');
                    } else {
                        b.classList.add('bg-slate-800', 'text-slate-500');
                        b.classList.remove('bg-sky-500', 'text-white');
                    }
                });
            } else { 
                subMenu.style.display = 'none'; 
            }
            
            let url = `/api/team_ranking/${teamId}?type=${rankType}&filter=${shotFilter}`;
            
            if (match_focus[side]) {
                url += `&match_id=${side === 'home' ? homeMatchId : awayMatchId}`; 
            } else if (l5_active[side]) {
                url += `&limit=5`;
            }

            fetch(url).then(r => r.json()).then(data => { 
                rankingsData[side] = data; 
                currentPages[side] = 1; 
                renderRankingPage(side); 
            });
        }

        function updatePredictions(f) {
            fetch(`/api/match_prediction/{{ match.id }}?shot_filter=${f}`).then(r => r.json()).then(d => {
                const c = 'shots'; const sd = d[c];
                const h = document.getElementById(`val-${c}-h`), v = document.getElementById(`val-${c}-v`), g = document.getElementById(`val-${c}-gen`);
                if(h) { h.innerText = sd.h; h.className = `text-3xl font-black ${getScoreColorClass(sd.h)}`; }
                if(v) { v.innerText = sd.v; v.className = `text-3xl font-black ${getScoreColorClass(sd.v)}`; }
                if(g) { g.innerText = sd.gen; g.className = `text-5xl font-black ${getScoreColorClass(sd.gen)}`; }
                
                const rankRmh = document.getElementById(`rank-${c}-rmh`), rankRav = document.getElementById(`rank-${c}-rav`), rankRah = document.getElementById(`rank-${c}-rah`), rankRmv = document.getElementById(`rank-${c}-rmv`);
                if(rankRmh) { rankRmh.innerText = `#${sd.rm_h}`; rankRmh.className = `font-black ${getPosColorClass(sd.rm_h)}`; }
                if(rankRav) { rankRav.innerText = `#${sd.ra_v}`; rankRav.className = `font-black ${getPosColorClass(sd.ra_v)}`; }
                if(rankRah) { rankRah.innerText = `#${sd.ra_h}`; rankRah.className = `font-black ${getPosColorClass(sd.ra_h)}`; }
                if(rankRmv) { rankRmv.innerText = `#${sd.rm_v}`; rankRmv.className = `font-black ${getPosColorClass(sd.rm_v)}`; }

                document.querySelectorAll('.pred-filter-btn').forEach(b => { b.classList.remove('bg-sky-500', 'text-white'); b.classList.add('text-slate-500'); });
                const ab = document.getElementById(`pred-filter-${f}`); if(ab) { ab.classList.add('bg-sky-500', 'text-white'); }
            });
        }
        async function openPlayer(pid) {
            const overlay = document.getElementById('modal-overlay');
            const content = document.getElementById('modal-content');
            overlay.style.display = 'flex';
            content.innerHTML = '<div class="text-center p-20 animate-pulse font-black text-sky-500">CARGANDO PERFIL...</div>';

            const r = await fetch(`/player_info/${pid}/{{ match.id }}`);
            const d = await r.json();

            window.currentRankings = d.rankings_top;
            window.currentPlayerData = d.stats;
            window.playerL5Details = d.last_5_details;
            window.playerTeamId = d.team_id;

            content.innerHTML = `
                <div class="flex justify-between items-end border-b border-slate-700 pb-2 mb-3 shrink-0">
                    <div>
                        <h2 class="text-5xl font-black italic uppercase text-white leading-none">${d.name}</h2>
                        <p class="text-sky-400 font-bold uppercase tracking-widest mt-3 text-lg">
                            ${d.team} | ${d.pos} | <span class="text-white">#${d.number || 'S/N'}</span>
                        </p>
                        <div class="flex gap-2 mt-3 flex-wrap">
                            ${d.teams_history ? d.teams_history.map(tid => `<a href="/team/${tid}"><img src="/static/${tid}_xsmall.png" onerror="this.style.display='none'" class="w-8 h-8 object-contain bg-slate-800 rounded-lg p-1 border border-slate-700 hover:border-sky-500 transition-colors" title="Team ${tid}"></a>`).join('') : ''}
                        </div>
                    </div>
                    <button onclick="closeModal()" class="self-start text-slate-500 hover:text-white transition-colors text-3xl p-2">✕</button>
                </div>

                <div class="grid grid-cols-12 gap-3 flex-1 min-h-0 overflow-hidden mb-4">
                    
                    <div class="col-span-4 bg-slate-900/50 rounded-[2.5rem] border border-slate-800 p-6 flex flex-col min-h-0 shadow-inner">
                        <div class="flex gap-2 mb-4 bg-slate-950 p-1.5 rounded-2xl border border-slate-800 shrink-0">
                            <button onclick="switchPlayerTab('gen')" id="tab-btn-gen" class="flex-1 py-2 text-[11px] font-black uppercase rounded-xl transition-all bg-sky-600 text-white">General</button>
                            <button onclick="switchPlayerTab('l5')" id="tab-btn-l5" class="flex-1 py-2 text-[11px] font-black uppercase rounded-xl transition-all text-slate-500">Ultimos 5</button>
                            <button onclick="switchPlayerTab('part')" id="tab-btn-part" class="flex-1 py-2 text-[11px] font-black uppercase rounded-xl transition-all text-slate-500">Partido</button>
                        </div>
                        <div id="player-stats-content" class="space-y-2 overflow-y-auto flex-1 custom-blue-scroll no-scrollbar pr-2">
                            ${renderStatRows(d.stats.general)}
                        </div>
                        <div id="player-modal-rival-context" class="hidden justify-center items-center gap-5 mb-4 bg-slate-900 p-2 rounded-xl border border-slate-800 flex-wrap"></div>
                    </div>

                    <div class="col-span-8 flex flex-col gap-6 min-h-0">
                        
                        <div class="bg-slate-900/50 rounded-[2.5rem] border border-slate-800 p-6 flex flex-col flex-1 min-h-0 shadow-inner overflow-hidden">
                            <div class="flex justify-between items-center mb-6 shrink-0">
                                <h4 class="text-[12px] font-black text-sky-500 uppercase tracking-[0.2em]">Rankings (Top 20)</h4>
                                <div class="flex bg-slate-950 p-1 rounded-xl border border-slate-800">
                                    <button onclick="renderRankScope('liga')" id="rank-scope-liga" class="px-4 py-1 text-[10px] font-black uppercase rounded-lg bg-sky-600 text-white transition-all">Liga</button>
                                    <button onclick="renderRankScope('equipo')" id="rank-scope-equipo" class="px-4 py-1 text-[10px] font-black uppercase rounded-lg text-slate-500 transition-all">Equipo</button>
                                    <button onclick="renderRankScope('posicion')" id="rank-scope-posicion" class="px-4 py-1 text-[10px] font-black uppercase rounded-lg text-slate-500 transition-all">Posicion</button>
                                </div>
                            </div>
                            
                            <div id="rankings-list-container" class="overflow-y-auto flex-1 custom-blue-scroll pr-4 grid grid-cols-2 gap-4 content-start">
                                </div>
                        </div>

                        <div class="notes-section bg-slate-900/50 rounded-[2.5rem] border border-slate-800 p-6 pt-4 shadow-inner">
                            <div class="flex justify-between items-center mb-4 px-2">
                                <h4 class="text-[12px] font-black text-sky-500 uppercase tracking-[0.2em]">Notas de Scouting</h4>
                                <button onclick="savePlayerNote('${pid}')" class="text-[10px] bg-sky-600 hover:bg-sky-500 text-white px-4 py-1.5 rounded-xl font-black uppercase transition-all shadow-lg">Guardar</button>
                            </div>
                            <textarea id="p-note-area" class="w-full bg-slate-950 border border-slate-800 rounded-2xl p-4 text-sm text-slate-300 outline-none focus:border-sky-500 transition-all h-20 resize-none shadow-inner">${d.notes || ''}</textarea>
                        </div>
                    </div>
                </div>
            `;
        renderRankScope('liga');
        }

        function renderRankScope(scope) {
            const container = document.getElementById('rankings-list-container');
            const data = window.currentRankings[scope];
            
            // Actualizar UI de botones
            ['liga', 'equipo', 'posicion'].forEach(s => {
                const btn = document.getElementById(`rank-scope-${s}`);
                btn.className = (s === scope) 
                    ? "px-4 py-1 text-[10px] font-black uppercase rounded-lg bg-sky-600 text-white shadow-lg transition-all"
                    : "px-4 py-1 text-[10px] font-black uppercase rounded-lg text-slate-500 hover:text-white transition-all";
            });

            if (data.length === 0) {
                container.innerHTML = `<div class="col-span-2 text-center py-10 text-slate-600 italic text-sm">El jugador no figura en el Top 20 de ninguna estadistica en este ambito.</div>`;
                return;
            }

            container.innerHTML = data.map(r => `
                    <div class="rank-badge h-24 shrink-0"> <div class="flex flex-col justify-center">
                            <span class="text-[12px] text-slate-300 font-black uppercase tracking-tighter">${r.label}</span>
                            <span class="text-white font-bold text-sm">${r.total} <small class="text-slate-600 font-normal italic">acum.</small></span>
                        </div>
                        <div class="text-right">
                            <span class="text-3xl font-black ${r.pos <= 3 ? 'text-emerald-400' : 'text-sky-500'}">#${r.pos}</span>
                        </div>
                    </div>
                `).join('');
        }

        function renderStatRows(data) {
            const labels = [
                ['Tiros Totales', 'shots'], ['Al Arco', 'target'], ['De Lejos', 'long'],
                ['Cabezazos', 'headers'], ['Tarjetas', 'cards'], ['Faltas Cometidas', 'f_c'],
                ['Faltas Recibidas', 'f_r'], ['Minutos', 'mins']
            ];
            return labels.map(l => `
                <div class="flex justify-between items-center p-1 rounded-lg hover:bg-slate-800/50 transition-colors">
                    <span class="text-[11px] font-bold text-slate-400 uppercase">${l[0]}</span>
                    <span class="text-sm font-black text-white">${data[l[1]] || 0}</span>
                </div>
            `).join('');
        }

        function switchPlayerTab(tab) {
            const content = document.getElementById('player-stats-content');
            const rivalContext = document.getElementById('player-modal-rival-context');
            const mapping = { 'gen': 'general', 'l5': 'l5', 'part': 'partido' };
            content.innerHTML = renderStatRows(window.currentPlayerData[mapping[tab]]);
            
            // Context Logic
            if (tab === 'l5' || tab === 'part') {
                let logosHtml = '';
                if(tab === 'l5' && window.playerL5Details) {
                     logosHtml = window.playerL5Details.map(m => `
                        <div class="flex flex-col items-center">
                            <span class="text-[9px] font-black ${m.cond === 'L' ? 'text-green-500' : 'text-yellow-500'}">${m.cond}</span>
                            <a href="/match/${m.match_id}"><img src="/static/${m.rival_id}_xsmall.png" onerror="this.src='/static/none.png'" class="w-6 h-6 object-contain hover:scale-110 transition-transform" title="${m.rival}"></a>
                        </div>
                     `).join('');
                } else if(tab === 'part') {
                     const homeId = "{{ match.id_home_team }}";
                     const awayId = "{{ match.id_away_team }}";
                     let rivalId = (String(window.playerTeamId) === String(homeId)) ? awayId : homeId;
                     let cond = (String(window.playerTeamId) === String(homeId)) ? 'L' : 'V';
                     let matchId = (String(window.playerTeamId) === String(homeId)) ? homeMatchId : awayMatchId;
                     
                     logosHtml = `
                        <div class="flex flex-col items-center">
                            <span class="text-[9px] font-black ${cond === 'L' ? 'text-green-500' : 'text-yellow-500'}">${cond}</span>
                            <a href="/match/${matchId}"><img src="/static/${rivalId}_xsmall.png" onerror="this.src='/static/none.png'" class="w-8 h-8 object-contain hover:scale-110 transition-transform" title="Rival"></a>
                        </div>
                     `;
                }
                
                if (logosHtml && rivalContext) {
                    rivalContext.innerHTML = logosHtml;
                    rivalContext.classList.remove('hidden');
                    rivalContext.classList.add('flex');
                } else if(rivalContext) {
                    rivalContext.classList.add('hidden');
                    rivalContext.classList.remove('flex');
                }
            } else if(rivalContext) {
                rivalContext.classList.add('hidden');
                rivalContext.classList.remove('flex');
            }

            // UI de botones
            ['gen', 'l5', 'part'].forEach(t => {
                const btn = document.getElementById(`tab-btn-${t}`);
                if(btn) {
                    btn.classList.remove('bg-sky-600', 'text-white');
                    btn.classList.add('text-slate-500');
                }
            });
            const active = document.getElementById(`tab-btn-${tab}`);
            if(active) active.classList.add('bg-sky-600', 'text-white');
        }        
        function closeModal() { document.getElementById('modal-overlay').style.display = 'none'; }
        function handlePlayerClick(e) { 
            const p = e.currentTarget; 
            if (!p.dragging) openPlayer(p.dataset.pid); 
        }        
        let substituteTarget = null;
        function closeSubstModal() { 
            document.getElementById('subst-modal-overlay').classList.add('hidden'); 
            document.getElementById('subst-search').value = '';
            document.getElementById('subst-results').innerHTML = '';
        }
        
        function searchPlayers(q) {
            if(!q || q.length < 2) return;
            fetch(`/search_players/${substituteTarget.dataset.teamid}?q=${q}`).then(r => r.json()).then(data => {
                const currentPids = Array.from(document.querySelectorAll('.player-dot')).map(p => p.dataset.pid);
                const filteredData = data.filter(p => !currentPids.includes(p.id));
                document.getElementById('subst-results').innerHTML = filteredData.map(p => `<div onclick="applySubstitution('${p.player_id}', '${p.last_name}', '${p.number}')" class="bg-slate-800 p-3 rounded-xl border border-slate-700 hover:border-sky-500 cursor-pointer flex justify-between"><span class="font-bold text-white">${p.player_name}</span><span class="text-slate-500 font-black">${p.position}</span></div>`).join('');
            });
        }

        function applySubstitution(pid, name, number) { 
            substituteTarget.dataset.pid = pid; 
            substituteTarget.dataset.pname = name; 
            // Actualiza el texto de la posicion y el nombre visual
            substituteTarget.childNodes[0].nodeValue = number; 
            substituteTarget.querySelector('.player-name').innerText = name; 
            closeSubstModal(); 
        }        
        document.addEventListener('contextmenu', e => { const p = e.target.closest('.player-dot'); if(p) { e.preventDefault(); lastCtxPid = p.dataset.pid; substituteTarget = p; document.getElementById('ctx-player-name').innerText = p.dataset.pname; const keyLabel = document.getElementById('ctx-key-label'); keyLabel.innerText = p.classList.contains('key-player') ? '❌ Quitar Marca' : '⭐ Marcar como Clave'; contextMenu.style.display = 'block'; contextMenu.style.left = e.clientX + 'px'; contextMenu.style.top = e.clientY + 'px'; } else contextMenu.style.display = 'none'; });
        document.addEventListener('click', e => { if (!e.target.closest('#context-menu')) contextMenu.style.display = 'none'; });
        function handleCtxAction(act) { if(act === 'profile') openPlayer(lastCtxPid); else if(act === 'replace') document.getElementById('subst-modal-overlay').classList.remove('hidden'); else if(act === 'key') substituteTarget.classList.toggle('key-player'); contextMenu.style.display = 'none'; }
        window.onload = () => { updateTeamRanking('home', '{{ match.id_home_team }}', 'tiradores'); updateTeamRanking('away', '{{ match.id_away_team }}', 'tiradores'); };
        function handleStart(e) {
            const clientX = e.touches ? e.touches[0].clientX : e.clientX;
            const clientY = e.touches ? e.touches[0].clientY : e.clientY;
            const p = e.target.closest('.draggable'), isPitch = e.target.closest('#soccer-pitch');
            if(!p && isPitch && !e.target.closest('#context-menu')) { selectedPlayers.forEach(x=>x.classList.remove('selected-player', 'lasso-selected')); selectedPlayers=[]; isLassoing=true; startX=clientX; startY=clientY; selectionBox.style.display='none'; draggables.forEach(x=>x._rect=x.getBoundingClientRect()); }
            else if(p) {
                if ((p.dataset.side === 'home' && locks.home) || (p.dataset.side === 'away' && locks.away)) return;
                if(!e.touches) e.preventDefault();
                activePlayer=p; activePlayer.dragging = false;
                if(!selectedPlayers.includes(p)) { selectedPlayers.forEach(x=>x.classList.remove('selected-player', 'lasso-selected')); p.classList.add('selected-player'); selectedPlayers=[p]; }
                selectedPlayers.forEach(x=>{ x.style.transition='none'; x.startL=parseFloat(x.style.left); x.startB=(x.style.bottom&&x.style.bottom!=='auto')?parseFloat(x.style.bottom):null; x.startT=x.startB===null?parseFloat(x.style.top):null; });
                activePlayer.mX=clientX; activePlayer.mY=clientY;
            }
        }
        function handleMove(e) {
            const clientX = e.touches ? e.touches[0].clientX : e.clientX;
            const clientY = e.touches ? e.touches[0].clientY : e.clientY;
            if(isLassoing) { 
                if(e.cancelable) e.preventDefault();
                let w=Math.abs(clientX-startX), h=Math.abs(clientY-startY), l=Math.min(clientX,startX), t=Math.min(clientY,startY); 
                if (w > 2 || h > 2) selectionBox.style.display = 'block';
                Object.assign(selectionBox.style, {width:w+'px', height:h+'px', left:l+'px', top:t+'px'});
                const br=selectionBox.getBoundingClientRect();
                draggables.forEach(x=>{
                    if ((x.dataset.side === 'home' && locks.home) || (x.dataset.side === 'away' && locks.away)) return;
                    const r=x._rect, overlap=!(br.right<r.left||br.left>r.right||br.bottom<r.top||br.top>r.bottom);
                    if(overlap) { if(!selectedPlayers.includes(x)) { x.classList.add('selected-player', 'lasso-selected'); selectedPlayers.push(x); } }
                    else { x.classList.remove('selected-player', 'lasso-selected'); selectedPlayers=selectedPlayers.filter(y=>y!==x); }
                });
            } else if(activePlayer) {
                if(e.cancelable) e.preventDefault();
                if (Math.abs(clientX - activePlayer.mX) > 3 || Math.abs(clientY - activePlayer.mY) > 3) activePlayer.dragging=true;
                const r=pitch.getBoundingClientRect(), dx=((clientX-activePlayer.mX)/r.width)*100, dy=((clientY-activePlayer.mY)/r.height)*100;
                selectedPlayers.forEach(x=>{ x.style.left=Math.max(0,Math.min(100,x.startL+dx))+'%'; if(x.startB!==null) x.style.bottom=Math.max(0,Math.min(100,x.startB-dy))+'%'; else x.style.top=Math.max(0,Math.min(100,x.startT+dy))+'%'; });
            }
        }
        function handleEnd() { isLassoing=false; selectionBox.style.display='none'; if(activePlayer) selectedPlayers.forEach(p => p.style.transition = ''); activePlayer=null; }
        
        // Substitution Highlight Logic
        function handleSubHover(e, el) {
            const subId = el.dataset.subId;
            if (subId) {
                // Highlight sub in list
                el.classList.add('sub-highlight-green');

                // Highlight player on pitch
                const pitchPlayer = document.querySelector(`.player-dot[data-pid="${subId}"]`);
                if (pitchPlayer) {
                    pitchPlayer.classList.add('sub-highlight-red');
                }
            }
        }

        function handleSubLeave(el) {
            const subId = el.dataset.subId;
            // Remove highlights
            el.classList.remove('sub-highlight-green');
            if (subId) {
                const pitchPlayer = document.querySelector(`.player-dot[data-pid="${subId}"]`);
                if (pitchPlayer) {
                    pitchPlayer.classList.remove('sub-highlight-red');
                }
            }
        }

        document.addEventListener('mousedown', handleStart);
        document.addEventListener('touchstart', handleStart, {passive: false});
        document.addEventListener('mousemove', handleMove);
        document.addEventListener('touchmove', handleMove, {passive: false});
        document.addEventListener('mouseup', handleEnd);
        document.addEventListener('touchend', handleEnd);
    </script>

    <!-- MODAL PLAYER -->
    <div id="modal-overlay" onclick="if(event.target==this) closeModal()"><div id="player-modal"><div id="modal-content"></div></div></div>
    
    <!-- MODAL SUSTITUCION -->
    <div id="subst-modal-overlay" class="hidden fixed inset-0 bg-black/80 backdrop-blur-sm z-[4000] flex items-center justify-center" onclick="if(event.target==this) closeSubstModal()">
        <div class="bg-slate-900 border border-slate-700 w-full max-w-md p-8 rounded-[2rem] shadow-2xl">
            <h3 class="text-xl font-black uppercase text-white mb-4 italic tracking-tighter">Sustitucion Tactica</h3>
            <input type="text" id="subst-search" autocomplete="off" oninput="searchPlayers(this.value)" placeholder="Ingresa nombre o ID..." class="w-full bg-slate-950 border border-slate-800 p-4 rounded-2xl outline-none focus:border-sky-500 text-white text-sm mb-4">
            <div id="subst-results" class="space-y-2 max-h-60 overflow-y-auto"></div>
        </div>
    </div>

    <div id="selection-box"></div>
    <div id="substitution-tooltip"></div>
    <!-- MENU CONTEXTUAL -->
    <div id="context-menu">
        <div class="context-header" id="ctx-player-name">Jugador</div>
        <div class="context-item" onclick="handleCtxAction('profile')">📊 Ver Perfil</div>
        <div class="context-item" onclick="handleCtxAction('replace')">🔄 Reemplazar Jugador</div>
        <div class="context-item" onclick="handleCtxAction('key')" id="ctx-key-label">⭐ Marcar como Clave</div>
    </div>

    <div class="max-w-[1600px] space-y-8 mx-auto">
        <!-- HEADER -->
        <header class="flex justify-between items-center"><a href="/" class="bg-slate-800 px-6 py-2 rounded-xl font-bold border border-slate-700 hover:bg-slate-700 transition flex items-center gap-2"><span>←</span> INICIO</a><div class="text-right"><h2 class="text-sky-500 font-black italic uppercase text-sm tracking-widest">{{ match.tournament or 'LIGA PROFESIONAL' }}</h2><p class="text-slate-500 text-[10px] font-bold uppercase tracking-tighter">{{ match.date }}</p></div></header>
        
        <!-- MARCADOR -->
        <div class="flex h-auto ">
            <div class="bg-slate-950/80 w-[70%] max-w-6xl p-8 pb-0 rounded-[3rem] border border-slate-700/50  shadow-inner  mx-auto text-center">
                <div class="flex justify-around items-center gap-8 mb-4 text-center">
                    <h1 class="text-3xl font-black uppercase flex-1 tracking-tighter hover:text-sky-500 transition-colors">
                        <a href="/team/{{ match.id_home_team }}" class="flex flex-col items-center gap-4">
                            <img src="{{ url_for('static', filename=match.id_home_team ~ '.png') }}" onerror="this.onerror=null; this.src='{{ url_for('static', filename='none.png') }}'" class="w-24 h-24 object-contain drop-shadow-2xl" alt="{{ match.home_team }}">
                            <span>{{ get_short_name(match.home_team) }}</span>
                        </a>
                    </h1>                    
                    <div class="px-8 py-3 bg-slate-900 rounded-3xl border-2 border-slate-800 text-4xl font-mono font-black text-white shadow-2xl">{{ match.score or 'VS' }}</div>
                    <h1 class="text-3xl font-black uppercase flex-1 tracking-tighter hover:text-sky-500 transition-colors ">
                        <a href="/team/{{ match.id_away_team }}" class="flex flex-col items-center gap-4">
                            <img src="{{ url_for('static', filename=match.id_away_team ~ '.png') }}" onerror="this.onerror=null; this.src='{{ url_for('static', filename='none.png') }}'" class="w-24 h-24 object-contain drop-shadow-2xl" alt="{{ match.away_team }}">
                            <span>{{ get_short_name(match.away_team) }}</span>
                        </a>
                    </h1>
                </div>
                <div class="border-t border-slate-800 py-4 "><span class="text-[12px] font-bold text-slate-300 uppercase tracking-widest italic">arbitro: {%if match.referee %} <a href="/referee/{{ match.referee }}" class="hover:text-sky-500">{{ match.referee}}</a> {% else %} Por designar {% endif %}</span></div>
                
                <!-- GOLES -->
                {% if match_goals %}
                <div class="grid grid-cols-[1fr_1px_1fr] gap-8 mt-0 border-t border-slate-800 justify-between">
                    <!-- GOLES LOCAL -->
                    <div class="space-y-2 p-4  text-right">
                        {% for g in match_goals if g.team_id|string == match.id_home_team|string %}
                        <div class="text-[14px] text-slate-300 grid grid-cols-[auto_auto] gap-x-2 justify-end items-center">
                             <span class="font-black text-white">{{ g.scorer }}</span> 
                             <span class="text-sky-500 font-bold">'{{ g.minute }}</span>
                             {% if g.assist %}
                             <div class="text-[11px] text-slate-500 font-bold italic mr-1 col-start-0">({{ g.assist }})</div>
                             {% endif %}
                        </div>
                        {% endfor %}
                    </div>
                    <div class="h-full w-[0px] border-x border-slate-800"></div>
                    <!-- GOLES VISITA -->
                    <div class="space-y-2 p-4  text-left">
                        {% for g in match_goals if g.team_id|string == match.id_away_team|string %}
                        <div class="text-[14px] text-slate-300 grid grid-cols-[auto_auto] gap-x-2 justify-start items-center">
                             <span class="text-sky-500 font-bold">'{{ g.minute }}</span>
                             <span class="font-black text-white">{{ g.scorer }}</span> 
                             {% if g.assist %}
                             <div class="text-[11px] text-slate-500 font-bold italic ml-1 col-start-2">({{ g.assist }})</div>
                             {% endif %}
                        </div>
                        {% endfor %}
                    </div>
                </div>
                {% endif %}
            </div>
            <!-- NOTAS -->
            <div class="ml-4 mx-auto w-[35%]">
                <form action="/save_match_note/{{ match.id }}" method="POST" class="bg-slate-900/40 p-6 rounded-[2.5rem] border border-slate-800/50 backdrop-blur-sm">
                    <div class="flex justify-between items-center mb-3 px-2">
                        <label class="text-[10px] font-black text-sky-500 uppercase tracking-[0.2em]">Notas Tacticas del Encuentro</label>
                        <button type="submit" class="text-[9px] bg-sky-600/20 hover:bg-sky-600 text-sky-400 hover:text-white px-4 py-1 rounded-full font-black uppercase transition-all border border-sky-500/30">Actualizar Nota</button>
                    </div>
                    <textarea name="notes" placeholder="Escribe aqui el analisis post-partido o instrucciones previas..." 
                        class="w-full bg-slate-950/50 border border-slate-800 rounded-2xl p-4 text-sm text-slate-300 outline-none focus:border-sky-500 h-28 resize-none shadow-inner transition-all">{{ m_note.notes if m_note else '' }}</textarea>
                </form>
            </div>
        </div>

        <div class="bg-slate-800/40 p-8 md:p-4 rounded-[4rem] border border-slate-700/50 grid md:grid-cols-4 gap-10 shadow-2xl items-start">
            <div class="space-y-8">
                <!-- BANCO LOCAL -->
                <div class="space-y-3">
                    <h4 class="text-[15px] font-black text-sky-400 uppercase italic mb-4 text-center tracking-widest border-b border-sky-400/20 pb-2">Banco Local</h4>
                    <div class="grid grid-cols-2 gap-1.5">
                        {% for p in home_subs %}
                        <div class="bg-slate-900/50 p-1.5 rounded-lg text-[12px] cursor-pointer hover:bg-slate-800 transition-all list-item-hover-only" 
                             data-pid="{{ p.player_id }}" 
                             {%if p.substitution %}data-sub-id="{{ p.substitution }}"{% endif %}
                             onmouseenter="handleSubHover(event, this)" 
                             onmouseleave="handleSubLeave(this)" 
                             onclick="handlePlayerClick(event, '{{p.player_id}}')">
                            <div class="flex justify-between items-center gap-1 w-full">
                                <span class="font-bold truncate flex-1 text-[14px] text-slate-200"><span class="text-slate-500 mr-1">{{ p.shirt_number or '-' }}</span> {{ p.player_name }} <span class="text-slate-500 font-medium text-[11px]">({{ p.position }})</span></span>
                                {% if p.sub_minute %}<span class="text-emerald-500 font-black text-[12px] whitespace-nowrap">{{ p.sub_minute}}'</span>{% endif %} 
                            </div>
                        </div>
                        {% endfor %}
                    </div>
                </div>
                <!-- RANKING LOCAL -->
                <div class="space-y-3">
                    <div class="flex flex-col items-center border-b border-sky-400/20 pb-2 mb-3">
                        <h4 class="text-[14px] font-black text-sky-400 uppercase italic tracking-widest mb-3">Rankings Local</h4>
                        <div class="flex w-full justify-between mb-3 text-[9px]">
                            <button onclick="toggleRankingL5('home', '{{ match.id_home_team }}')" id="h-l5-btn" class="px-2 py-0.5 rounded-full border border-slate-700 font-black uppercase text-slate-500 hover:text-white transition-all">Ultimos 5 Partidos</button>
                            <button onclick="toggleRankingMatch('home', '{{ match.id_home_team }}')" id="h-part-btn" class="px-2 py-0.5 rounded-full border border-slate-700 font-black uppercase text-slate-500 hover:text-white transition-all">Partido</button>
                        </div>
                        <div class="flex flex-wrap justify-center gap-1 mb-2 text-[12px]">
                            <button data-type="tiradores" onclick="updateTeamRanking('home', '{{ match.id_home_team }}', 'tiradores', 'all', event)" class="px-1.5 py-0.5 rounded bg-sky-500 text-white font-bold h-rank-btn">Tiros</button>
                            <button data-type="headers" onclick="updateTeamRanking('home', '{{ match.id_home_team }}', 'headers', 'all', event)" class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-bold h-rank-btn">Cabezazos</button>
                            <button data-type="yellows" onclick="updateTeamRanking('home', '{{ match.id_home_team }}', 'yellows', 'all', event)" class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-bold h-rank-btn">Tarjetas</button>
                            <button data-type="fouls" onclick="updateTeamRanking('home', '{{ match.id_home_team }}', 'fouls', 'all', event)" class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-bold h-rank-btn">Faltas</button>
                            <button data-type="fouls_rec" onclick="updateTeamRanking('home', '{{ match.id_home_team }}', 'fouls_rec', 'all', event)" class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-bold h-rank-btn">Faltas Rec.</button>
                        </div>
                        <div id="home-sub-filters" class="sub-menu flex gap-1 justify-center text-[11px]" style="display:none;">
                            <button onclick="updateTeamRanking('home', '{{ match.id_home_team }}', 'tiradores', 'all', event)" id="home-sub-all" class="px-1.5 py-0.5 rounded bg-sky-500 text-white font-black h-sub-btn">Todos</button>
                            <button onclick="updateTeamRanking('home', '{{ match.id_home_team }}', 'tiradores', 'target', event)" id="home-sub-target" class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-500 font-black h-sub-btn">Arco</button>
                            <button onclick="updateTeamRanking('home', '{{ match.id_home_team }}', 'tiradores', 'long', event)" id="home-sub-long" class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-500 font-black h-sub-btn">Lejos</button>
                        </div>
                    </div>
                    <div id="home-l5-context" class="hidden gap-5 mb-3 justify-center bg-slate-900/50 p-2 rounded-xl">
                        {% for m in l5_home %}
                        <div class="flex flex-col items-center">
                            <span class="text-[9px] font-black {{ 'text-green-500' if m.cond == 'L' else 'text-yellow-500' }}">{{ m.cond }}</span>
                            <a href="/match/{{ m.id }}"><img src="{{ url_for('static', filename=m.rival_id ~ '_xsmall.png') }}" onerror="this.onerror=null; this.src='{{ url_for('static', filename='none.png') }}'" class="w-6 h-6 object-contain hover:scale-110 transition-transform" title="{{ get_short_name(m.rival_name) }}"></a>
                        </div>
                        {% endfor %}
                    </div>                        <div id="home-last-match-context" class="hidden gap-2 mb-3 justify-center bg-slate-900/50 p-2 rounded-xl">
                            {% if last_match_home %}
                            <div class="flex flex-col items-center">
                                <span class="text-[9px] font-black {{ 'text-green-500' if last_match_home.cond == 'L' else 'text-yellow-500' }}">{{ last_match_home.cond }}</span>
                                <a href="/match/{{ last_match_home.id }}"><img src="{{ url_for('static', filename=last_match_home.rival_id ~ '_xsmall.png') }}" onerror="this.onerror=null; this.src='{{ url_for('static', filename='none.png') }}'" class="w-6 h-6 object-contain hover:scale-110 transition-transform" title="{{ get_short_name(last_match_home.rival_name) }}"></a>
                            </div>
                            {% endif %}
                        </div>                    <div id="home-ranking-list" class="space-y-1"></div>
                    <div class="flex justify-center gap-4 mt-2">
                        <button onclick="changePage('home', -1)" class="text-sky-400 hover:text-white transition-colors"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="m15 18-6-6 6-6"/></svg></button>
                        <span id="home-page-info" class="text-[10px] font-black text-slate-500 uppercase mt-0.5">1 / 1</span>
                        <button onclick="changePage('home', 1)" class="text-sky-400 hover:text-white transition-colors"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="m9 18 6-6-6-6"/></svg></button>
                    </div>
                </div>
            </div>
            <!-- PITCH -->
            <div class="md:col-span-2 relative flex flex-col items-center">
                <h2 class="text-[15px] font-black text-slate-300 uppercase italic mb-4 text-center tracking-widest border-b border-slate-500/20 pb-2">{{ lineup_label }}</h2>
                <div class="pitch" id="soccer-pitch">
                    {% for p in home_lineup %}<div class="player-dot bg-blue-500 draggable shadow-lg" style="bottom:{{ (p.role_x * 50) }}%; left:{{(1-p.role_y)*100}}%;" data-pid="{{p.player_id}}" data-pname="{{p.player_name}}" data-side="home" data-teamid="{{match.id_home_team}}" onclick="handlePlayerClick(event)">{{ p.shirt_number or p.position }}{% if p.card %}<div class="card-badge card-{{p.card}}"></div>{% endif %}<div class="player-name">{{p.player_name}}</div></div>{% endfor %}
                    {% for p in away_lineup %}<div class="player-dot bg-red-500 draggable shadow-lg" style="top:{{ (p.role_x * 50) }}%; left:{{p.role_y*100}}%;" data-pid="{{p.player_id}}" data-pname="{{p.player_name}}" data-side="away" data-teamid="{{match.id_away_team}}" onclick="handlePlayerClick(event)">{{ p.shirt_number or p.position }}{% if p.card %}<div class="card-badge card-{{p.card}}"></div>{% endif %}<div class="player-name">{{p.player_name}}</div></div>{% endfor %}
                </div>
                <div class="flex items-center gap-4 mt-4">
                    <button id="lock-home-btn" onclick="toggleLock('home')" class="bg-slate-800 hover:bg-slate-700 text-white w-10 h-10 rounded-xl flex items-center justify-center border border-slate-700 shadow-lg font-black">L</button>
                    <button onclick="togglePitchOrientation()" class="bg-slate-800 hover:bg-slate-700 text-white w-10 h-10 rounded-xl transition-all flex items-center justify-center border border-slate-700 shadow-lg"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="m3 16 4 4 4-4"/><path d="M7 20V4"/><path d="m21 8-4-4-4 4"/><path d="M17 4v16"/></svg></button>
                    <button id="lock-away-btn" onclick="toggleLock('away')" class="bg-slate-800 hover:bg-slate-700 text-white w-10 h-10 rounded-xl flex items-center justify-center border border-slate-700 shadow-lg font-black">V</button>
                </div>
            </div>

            <div class="space-y-8 text-right">
                <!-- BANCO VISITA -->
                <div class="space-y-3">
                    <h4 class="text-[15px] font-black text-red-500 uppercase italic mb-4 text-center tracking-widest border-b border-red-500/20 pb-2">Banco Visita</h4>
                    <div class="grid grid-cols-2 gap-1.5">
                        {% for p in away_subs %}
                        <div class="bg-slate-900/50 p-1.5 rounded-lg text-[12px] cursor-pointer hover:bg-slate-800 transition-all list-item-hover-only" 
                             data-pid="{{ p.player_id }}" 
                             {%if p.substitution %}data-sub-id="{{ p.substitution }}"{% endif %}
                             onmouseenter="handleSubHover(event, this)" 
                             onmouseleave="handleSubLeave(this)" 
                             onclick="handlePlayerClick(event, '{{p.player_id}}')">
                            <div class="flex justify-between items-center gap-1 w-full flex-row-reverse">
                                <span class="font-bold truncate flex-1 text-[14px] text-slate-200 text-right"><span class="text-slate-500 font-medium text-[11px]">({{ p.position }})</span> {{ p.player_name }} <span class="text-slate-500 ml-1">{{ p.shirt_number or '-' }}</span></span>
                                {% if p.sub_minute %}<span class="text-emerald-500 font-black text-[12px] whitespace-nowrap">{{ p.sub_minute}}'</span>{% endif %}
                            </div>
                        </div>
                        {% endfor %}
                    </div>
                </div>
                <!-- RANKING VISITA -->
                <div class="space-y-3">
                    <div class="flex flex-col items-center border-b border-red-500/20 pb-2 mb-3">
                        <h4 class="text-[14px] font-black text-red-500 uppercase italic tracking-widest mb-3">Rankings Visita</h4>
                        <div class="flex w-full justify-between mb-3 text-[9px]">
                            <button onclick="toggleRankingL5('away', '{{ match.id_away_team }}')" id="v-l5-btn" class="px-2 py-0.5 rounded-full border border-slate-700 font-black uppercase text-slate-500 hover:text-white transition-all">Ultimos 5 Partidos</button>
                            <button onclick="toggleRankingMatch('away', '{{ match.id_away_team }}')" id="v-part-btn" class="px-2 py-0.5 rounded-full border border-slate-700 font-black uppercase text-slate-500 hover:text-white transition-all">Partido</button>
                        </div>
                        <div class="flex flex-wrap justify-center gap-1 mb-2 text-[12px]">
                            <button data-type="tiradores" onclick="updateTeamRanking('away', '{{ match.id_away_team }}', 'tiradores', 'all', event)" class="px-1.5 py-0.5 rounded bg-sky-500 text-white font-bold v-rank-btn">Tiros</button>
                            <button data-type="headers" onclick="updateTeamRanking('away', '{{ match.id_away_team }}', 'headers', 'all', event)" class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-bold v-rank-btn">Cabezazos</button>
                            <button data-type="yellows" onclick="updateTeamRanking('away', '{{ match.id_away_team }}', 'yellows', 'all', event)" class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-bold v-rank-btn">Tarjetas</button>
                            <button data-type="fouls" onclick="updateTeamRanking('away', '{{ match.id_away_team }}', 'fouls', 'all', event)" class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-bold v-rank-btn">Faltas</button>
                            <button data-type="fouls_rec" onclick="updateTeamRanking('away', '{{ match.id_away_team }}', 'fouls_rec', 'all', event)" class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-bold v-rank-btn">Faltas Rec.</button>
                        </div>
                        <div id="away-sub-filters" class="sub-menu flex gap-1 justify-center text-[12px]">
                            <button onclick="updateTeamRanking('away', '{{ match.id_away_team }}', 'tiradores', 'all', event)" id="away-sub-all" class="px-1.5 py-0.5 rounded bg-sky-500 text-white font-black v-sub-btn">Todos</button>
                            <button onclick="updateTeamRanking('away', '{{ match.id_away_team }}', 'tiradores', 'target', event)" id="away-sub-target" class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-500 font-black v-sub-btn">Arco</button>
                            <button onclick="updateTeamRanking('away', '{{ match.id_away_team }}', 'tiradores', 'long', event)" id="away-sub-long" class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-500 font-black v-sub-btn">Lejos</button>
                        </div>
                    </div>
                                                                    <div id="away-l5-context" class="hidden gap-2 mb-3 justify-center bg-slate-900/50 p-2 rounded-xl">
                                                                        {% for m in l5_away %}
                                                                        <div class="flex flex-col items-center">
                                                                            <span class="text-[9px] font-black {{ 'text-green-500' if m.cond == 'L' else 'text-yellow-500' }}">{{ m.cond }}</span>
                                                                            <a href="/match/{{ m.id }}"><img src="{{ url_for('static', filename=m.rival_id ~ '_xsmall.png') }}" onerror="this.onerror=null; this.src='{{ url_for('static', filename='none.png') }}'" class="w-6 h-6 object-contain hover:scale-110 transition-transform" title="{{ get_short_name(m.rival_name) }}"></a>
                                                                        </div>
                                                                        {% endfor %}
                                                                    </div>                                            <div id="away-last-match-context" class="hidden gap-2 mb-3 justify-center bg-slate-900/50 p-2 rounded-xl">
                                                {% if last_match_away %}
                                                <div class="flex flex-col items-center">
                                                    <span class="text-[9px] font-black {{ 'text-green-500' if last_match_away.cond == 'L' else 'text-yellow-500' }}">{{ last_match_away.cond }}</span>
                                                    <a href="/match/{{ last_match_away.id }}"><img src="{{ url_for('static', filename=last_match_away.rival_id ~ '_xsmall.png') }}" onerror="this.onerror=null; this.src='{{ url_for('static', filename='none.png') }}'" class="w-6 h-6 object-contain hover:scale-110 transition-transform" title="{{ get_short_name(last_match_away.rival_name) }}"></a>
                                                </div>
                                                {% endif %}
                                            </div>                    <div id="away-ranking-list" class="space-y-1"></div>
                    <div class="flex justify-center gap-4 mt-2">
                        <button onclick="changePage('away', -1)" class="text-sky-400 hover:text-white transition-colors"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="m15 18-6-6 6-6"/></svg></button>
                        <span id="away-page-info" class="text-[10px] font-black text-slate-500 uppercase tracking-widest mt-0.5">1 / 1</span>
                        <button onclick="changePage('away', 1)" class="text-sky-400 hover:text-white transition-colors"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="m9 18 6-6-6-6"/></svg></button>
                    </div>
                </div>
            </div>
        </div>


        <!-- PANEL DE PREDICCIONES -->
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4" id="prediction-section">
            {% macro get_score_color(val) %}{% if val <= 30 %}text-red-500{% elif val <= 70 %}text-blue-500{% else %}text-green-500{% endif %}{% endmacro %}
            {% macro get_pos_color(val) %}{% if val > 20 %}text-red-500{% elif val > 10 %}text-blue-500{% else %}text-green-500{% endif %}{% endmacro %}
            {% for cat, data, label in [
                ('shots', pred_s, 'Puntuacion de Tiros'),
                ('headers', pred_h, 'Puntuacion de Cabezazos'),
                ('cards', pred_c, 'Puntuacion de Tarjetas'),
                ('fouls', pred_f, 'Puntuacion de Faltas')
            ] %}
            <div class="bg-slate-800/60 p-5 rounded-[2.5rem] border border-slate-700 shadow-xl">
                <!--PREDICCION -->
                <div class="flex justify-between items-center mb-6">
                    <h3 class="font-black text-sky-400 uppercase tracking-tighter text-[16px] italic leading-tight">{{ label }}</h3>
                    {% if cat == 'shots' %}
                    <div class="flex gap-1 shrink-0">
                        <button onclick="updatePredictions('all')" id="pred-filter-all" class="pred-filter-btn text-[9px] px-2 py-1 rounded-md uppercase font-bold border border-slate-700 bg-sky-500 text-white">Todos</button>
                        <button onclick="updatePredictions('target')" id="pred-filter-target" class="pred-filter-btn text-[9px] px-2 py-1 rounded-md uppercase font-bold border border-slate-700 text-slate-500 hover:text-white">Arco</button>
                        <button onclick="updatePredictions('long')" id="pred-filter-long" class="pred-filter-btn text-[9px] px-2 py-1 rounded-md uppercase font-bold border border-slate-700 text-slate-500 hover:text-white">Lejos</button>
                    </div>
                    {% endif %}
                </div>
                <div class="space-y-6">
                    <div class="grid grid-cols-2 gap-3">
                        <div class="bg-slate-950 p-4 rounded-2xl border border-slate-800 text-center">
                            <div class="text-[12px] font-bold text-slate-500 uppercase">Local</div>
                            <div class="text-3xl font-black {{ get_score_color(data.h) }}" id="val-{{ cat }}-h">{{ data.h }}</div>
                        </div>
                        <div class="bg-slate-950 p-4 rounded-2xl border border-slate-800 text-center">
                            <div class="text-[12px] font-bold text-slate-500 uppercase">Visita</div>
                            <div class="text-3xl font-black {{ get_score_color(data.v) }}" id="val-{{ cat }}-v">{{ data.v }}</div>
                        </div>
                    </div>
                    <div class="bg-sky-600/10 p-5 rounded-2xl border border-sky-500/30 text-center">
                        <div class="text-[12px] font-black text-sky-500 uppercase tracking-widest opacity-60">General</div>
                        <div class="text-5xl font-black {{ get_score_color(data.gen) }}" id="val-{{ cat }}-gen">{{ data.gen }}</div>
                    </div>
                    
                    <div class="space-y-2 pt-4 border-t border-slate-700/50">
                        <div class="flex justify-between items-center bg-slate-950/50 p-2 rounded-lg border border-slate-800">
                            {% set tag_h = 'Realz' if cat in ['shots', 'headers'] else 'Recib' if cat in ['cards'] else 'Comet' %}
                            {% set tag_v = 'Recib' if cat in ['shots', 'headers'] else 'Gener' if cat in ['cards'] else 'Recib' %}
                            <span class="text-[11px] text-slate-400 font-black uppercase">L {{tag_h}} <span id="rank-{{cat}}-rmh" class="{{ get_pos_color(data.rm_h) }}">#{{data.rm_h}}</span></span>
                            <span class="text-[11px] text-slate-400 font-black uppercase text-right">V {{tag_v}} <span id="rank-{{cat}}-rav" class="{{ get_pos_color(data.ra_v) }}">#{{data.ra_v}}</span></span>
                        </div>
                        <div class="flex justify-between items-center bg-slate-950/50 p-2 rounded-lg border border-slate-800">
                             <span class="text-[11px] text-slate-400 font-black uppercase" >L {{tag_v}} <span id="rank-{{cat}}-rah"class="{{ get_pos_color(data.ra_h) }}">#{{data.ra_h}}</span></span>
                             <span class="text-[11px] text-slate-400 font-black uppercase text-right">V {{tag_h}} <span id="rank-{{cat}}-rmv" class="{{ get_pos_color(data.rm_v) }}">#{{data.rm_v}}</span></span>
                        </div>
                        {% if data.ref_rank %}
                        <div class="bg-slate-900 p-2 rounded-lg border border-sky-900/20 text-center">
                            <span class="text-[11px] text-sky-500 font-black uppercase italic" id="rank-{{cat}}-refrank">arbitro <span class="{{ get_pos_color(data.ref_rank) }}">#{{data.ref_rank}}</span> en {{'Tarjetas' if cat=='cards' else 'Faltas'}}</span>
                        </div>
                        {% endif %}
                    </div>
                </div>
            </div>
            {% endfor %}
        </div>

        <!-- HISTORIAL Y ARBITRO -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-8 mt-12">
            <!-- H2H -->
            <div class="bg-slate-800/40 p-6 rounded-[2.5rem] text-[15px] border border-slate-700/50 shadow-xl">
                <h3 class="font-black text-sky-400 uppercase italic tracking-widest mb-6 border-l-4 border-sky-500 pl-4">Historial Entre Ambos</h3>
                <div class="space-y-3">
                    {% for h in h2h_matches %}
                    <a href="/match/{{ h.id }}" class="flex justify-between items-center bg-slate-900/50 p-4 rounded-2xl border border-slate-800 hover:border-sky-500 transition-all group">
                        <div class="flex flex-col w-full">
                            <span class="text-[11px] font-black text-slate-500 uppercase tracking-widest mb-2 text-center">{{ h.date[:10] }} | {{ h.tournament }}</span>
                            <div class="flex items-center justify-between gap-4">
                                <div class="flex items-center gap-2 flex-1 justify-end">
                                    <span class="font-bold text-slate-300 text-right text-[13px] {{ 'text-sky-400' if h.id_home_team|string == match.id_home_team|string else '' }}">{{ get_short_name(h.home_team) }}</span>
                                    <img src="{{ url_for('static', filename=h.id_home_team ~ '_xsmall.png') }}" onerror="this.src='/static/none.png'" class="w-6 h-6 object-contain">
                                </div>
                                <span class="bg-slate-800 px-3 py-1 rounded font-mono font-black text-white border border-slate-700">{{ h.score }}</span>
                                <div class="flex items-center gap-2 flex-1">
                                    <img src="{{ url_for('static', filename=h.id_away_team ~ '_xsmall.png') }}" onerror="this.src='/static/none.png'" class="w-6 h-6 object-contain">
                                    <span class="font-bold text-slate-300 text-left text-[13px] {{ 'text-sky-400' if h.id_away_team|string == match.id_home_team|string else '' }}">{{ get_short_name(h.away_team) }}</span>
                                </div>
                            </div>
                        </div>
                        <span class="text-sky-500 opacity-0 group-hover:opacity-100 transition-opacity ml-2">→</span>
                    </a>
                    {% else %}
                    <p class="text-slate-500 text-xs italic text-center py-4">No se encontraron enfrentamientos previos recientes.</p>
                    {% endfor %}
                </div>
            </div>

            <!-- HISTORIAL ARBITRO -->
            <div class="bg-slate-800/40 p-6 rounded-[2.5rem] text-[15px] border border-slate-700/50 shadow-xl">
                <h3 class="font-black text-yellow-500 uppercase italic tracking-widest mb-6 border-l-4 border-yellow-500 pl-4">Historial del Arbitro : <a class="text-[12px] text-yellow-500 hover:underline" href="/referee/{{ match.referee }}">{{match.referee}}</a></h3>
                <div class="space-y-3">
                    {% for r in ref_history %}
                    <div class="bg-slate-900/50 p-4 rounded-2xl border border-slate-800 hover:border-yellow-500/50 transition-all">
                        <a href="/match/{{ r.match_id }}">
                            <div class="flex justify-between font-black text-slate-500 text-[11px] uppercase mb-3">
                                <span>{{ r.date[:10] }}</span>
                                <span>{{ r.tournament }}</span>
                            </div>
                            <div class="grid grid-cols-3 items-center gap-2 mb-3 text-[13px]">
                                <div class="flex flex-col items-center gap-1">
                                    <img src="{{ url_for('static', filename=r.id_home_team ~ '_xsmall.png') }}" onerror="this.src='/static/none.png'" class="w-8 h-8 object-contain">
                                    <span class="font-bold text-center truncate w-full {{ 'text-sky-400' if r.id_home_team|string == match.id_home_team|string or r.id_home_team|string == match.id_away_team|string else 'text-slate-300' }}">{{ get_short_name(r.home_team) }}</span>
                                </div>
                                <span class="bg-slate-800 py-1.5 rounded-lg font-mono font-black text-center text-white text-[18px] border border-slate-700">{{ r.score or 'VS' }}</span>
                                <div class="flex flex-col items-center gap-1">
                                    <img src="{{ url_for('static', filename=r.id_away_team ~ '_xsmall.png') }}" onerror="this.src='/static/none.png'" class="w-8 h-8 object-contain">
                                    <span class="font-bold text-center truncate w-full {{ 'text-sky-400' if r.id_away_team|string == match.id_home_team|string or r.id_away_team|string == match.id_away_team|string else 'text-slate-300' }}">{{ get_short_name(r.away_team) }}</span>
                                </div>
                            </div>
                            <div class="grid grid-cols-2 gap-4 border-t border-slate-800 pt-3">
                                <div class="text-center">
                                    <p class="text-[10px] font-black text-slate-500 uppercase tracking-widest">Local</p>
                                    <p class="text-[12px] font-bold"><span class="text-yellow-500">{{ r.stats.h_cards }} Tarj.</span> | <span class="text-sky-400">{{ r.stats.h_fouls }} Faltas</span></p>
                                </div>
                                <div class="text-center">
                                    <p class="text-[10px] font-black text-slate-500 uppercase tracking-widest">Visita</p>
                                    <p class="text-[12px] font-bold"><span class="text-yellow-500">{{ r.stats.v_cards }} Tarj.</span> | <span class="text-sky-400">{{ r.stats.v_fouls }} Faltas</span></p>
                                </div>
                            </div>
                        </a>
                    </div>
                    {% else %}
                    <p class="text-slate-500 text-xs italic text-center py-4">No hay registros recientes de este arbitro con estos equipos.</p>
                    {% endfor %}
                </div>
            </div>
        </div>
    </div>
    '''+FOOTER_HTML+'''</body></html>'''

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