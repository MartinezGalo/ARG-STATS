import sqlite3
import os
import json
from flask import Flask, render_template_string, request, redirect, url_for, jsonify, send_from_directory

app = Flask(__name__)
DB_NAME = "LIGA_ARG_2025.db"

# --- LÓGICA DE BASE DE DATOS Y ESTADÍSTICAS ---

def get_db_connection():
    """
    Establece la conexión con la base de datos SQLite.
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
    try:
        conn.execute('ALTER TABLE matches ADD COLUMN finished INTEGER DEFAULT 0')
    except:
        pass # La columna ya existe
    conn.commit()
    conn.close()

def get_referee_rankings():
    """
    Calcula la posición de cada árbitro en un top basado en el volumen total de eventos.
    Retorna dos diccionarios: {NombreArbitro: PosicionRanking} para tarjetas y faltas.
    """
    conn = get_db_connection()
    # Ranking por Total de Tarjetas
    ref_cards = conn.execute('''
        SELECT m.referee, COUNT(c.card_id) as total 
        FROM matches m LEFT JOIN cards c ON m.id = c.match_id 
        WHERE m.finished = 1 GROUP BY m.referee ORDER BY total DESC
    ''').fetchall()
    # Ranking por Total de Faltas
    ref_fouls = conn.execute('''
        SELECT m.referee, SUM(pmd.fouls_committed) as total 
        FROM matches m LEFT JOIN player_match_details pmd ON m.id = pmd.match_id 
        WHERE m.finished = 1 GROUP BY m.referee ORDER BY total DESC
    ''').fetchall()
    conn.close()
    return {r['referee']: i+1 for i, r in enumerate(ref_cards)}, {r['referee']: i+1 for i, r in enumerate(ref_fouls)}

def get_referee_detailed_tops():
    """
    Obtiene métricas detalladas (Total, PJ, Promedio) de los árbitros para la página /stats.
    Ordena los resultados por el valor Total acumulado.
    """
    conn = get_db_connection()
    ref_c_q = conn.execute('''
        SELECT m.referee as name, COUNT(c.card_id) as total, COUNT(DISTINCT m.id) as pj,
        CAST(COUNT(c.card_id) AS FLOAT) / COUNT(DISTINCT m.id) as avg
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

def get_last_finished_match_id(team_id):
    """Busca el ID del partido finalizado más reciente de un equipo para extraer su táctica actual."""
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
        res.append(d)
    return res

def get_team_stats_core(category='shots', filter_type='all', order_by='total', limit=None):
    """
    Función unificada que obtiene estadísticas completas de equipos.
    Si `limit` está presente, calcula las métricas basadas en los últimos N partidos de cada equipo.
    Retorna dos listas de diccionarios (A favor y En contra).
    """
    conn = get_db_connection()
    # Mapeo de nombres de equipos (id -> nombre)
    teams_map = {str(r['id']): r['name'] for r in conn.execute('SELECT DISTINCT id_home_team as id, home_team as name FROM matches').fetchall()}

    sort_metric = "total" if order_by == 'total' else "avg"

    # Si no se pide limit, reutilizamos la implementación previa que usa consultas globales
    if not limit:
        if category == 'shots':
            where_f = "AND on_target = 1" if filter_type == 'target' else "AND inside_box = 0" if filter_type == 'long' else ""

            made_q = f'''
                SELECT team_id as rank_team, COUNT(*) as total, COUNT(DISTINCT match_id) as pj, CAST(COUNT(*) AS FLOAT) / COUNT(DISTINCT match_id) as avg 
                FROM shots 
                WHERE 1=1 {where_f} 
                GROUP BY rank_team 
                ORDER BY {sort_metric} DESC'''
            
            against_q = f'''
                SELECT (CASE WHEN s.team_id = m.id_home_team THEN m.id_away_team ELSE m.id_home_team END) as rank_team, COUNT(*) as total, COUNT(DISTINCT s.match_id) as pj, CAST(COUNT(*) AS FLOAT) / COUNT(DISTINCT s.match_id) as avg 
                FROM shots s JOIN matches m ON s.match_id = m.id 
                WHERE 1=1 {where_f} 
                GROUP BY rank_team 
                ORDER BY {sort_metric} DESC'''

        elif category == 'headers':
            made_q = f'''
                SELECT team_id as rank_team, COUNT(*) as total, COUNT(DISTINCT match_id) as pj, CAST(COUNT(*) AS FLOAT) / COUNT(DISTINCT match_id) as avg 
                FROM shots WHERE shot_type = 'Header' 
                GROUP BY rank_team 
                ORDER BY {sort_metric} DESC'''
            against_q = f'''
                SELECT (CASE WHEN s.team_id = m.id_home_team THEN m.id_away_team ELSE m.id_home_team END) as rank_team, COUNT(*) as total, COUNT(DISTINCT s.match_id) as pj, CAST(COUNT(*) AS FLOAT) / COUNT(DISTINCT s.match_id) as avg 
                FROM shots s JOIN matches m ON s.match_id = m.id 
                WHERE s.shot_type = 'Header' 
                GROUP BY rank_team 
                ORDER BY {sort_metric} DESC'''

        elif category == 'cards':
            made_q = f'''
                SELECT team_id as rank_team, COUNT(*) as total, COUNT(DISTINCT match_id) as pj, CAST(COUNT(*) AS FLOAT) / COUNT(DISTINCT match_id) as avg 
                FROM cards 
                GROUP BY rank_team 
                ORDER BY {sort_metric} DESC'''
            against_q = f'''
                SELECT (CASE WHEN c.team_id = m.id_home_team THEN m.id_away_team ELSE m.id_home_team END) as rank_team, COUNT(*) as total, COUNT(DISTINCT c.match_id) as pj, CAST(COUNT(*) AS FLOAT) / COUNT(DISTINCT c.match_id) as avg 
                FROM cards c JOIN matches m ON c.match_id = m.id 
                GROUP BY rank_team 
                ORDER BY {sort_metric} DESC'''

        elif category == 'fouls':
            made_q = f'''
                SELECT team_id as rank_team, SUM(fouls_committed) as total, COUNT(DISTINCT match_id) as pj, CAST(SUM(fouls_committed) AS FLOAT) / COUNT(DISTINCT match_id) as avg 
                FROM player_match_details 
                GROUP BY rank_team 
                ORDER BY {sort_metric} DESC'''
            against_q = f'''
                SELECT team_id as rank_team, SUM(fouls_received) as total, COUNT(DISTINCT match_id) as pj, CAST(SUM(fouls_received) AS FLOAT) / COUNT(DISTINCT match_id) as avg 
                FROM player_match_details 
                GROUP BY rank_team 
                ORDER BY {sort_metric} DESC'''

        res_made = conn.execute(made_q).fetchall()
        res_against = conn.execute(against_q).fetchall()
        conn.close()

        def structure(data):
            return [{"id": str(r[0]), "name": teams_map.get(str(r[0]), "N/A"), "total": int(r[1]), "pj": r[2], "avg": round(r[3], 2)} for r in data]

        return structure(res_made), structure(res_against)

    # Si se solicita limitar a últimos N partidos por equipo, hacemos cálculo por equipo
    results_made = []
    results_against = []
    team_ids = list(teams_map.keys())

    for tid in team_ids:
        # Obtener últimos `limit` partidos finalizados donde participó el equipo
        match_rows = conn.execute('SELECT id FROM matches WHERE (id_home_team = ? OR id_away_team = ?) AND finished = 1 ORDER BY date DESC LIMIT ?', (str(tid), str(tid), limit)).fetchall()
        match_ids = [r[0] for r in match_rows]
        pj = len(match_ids)
        if pj == 0:
            results_made.append({"id": tid, "name": teams_map.get(tid, "N/A"), "total": 0, "pj": 0, "avg": 0})
            results_against.append({"id": tid, "name": teams_map.get(tid, "N/A"), "total": 0, "pj": 0, "avg": 0})
            continue

        ids_str = ",".join([f"'{m}'" for m in match_ids])

        if category == 'shots':
            where_f = "AND on_target = 1" if filter_type == 'target' else "AND inside_box = 0" if filter_type == 'long' else ""
            # A favor: contar eventos del equipo en esos partidos
            q_made = f"SELECT COUNT(*) FROM shots WHERE team_id = ? AND match_id IN ({ids_str}) {where_f}"
            total_m = conn.execute(q_made, (str(tid),)).fetchone()[0]
            # En contra: contar eventos del rival en esos partidos
            q_against = f"SELECT COUNT(*) FROM shots s JOIN matches m ON s.match_id = m.id WHERE (CASE WHEN s.team_id = m.id_home_team THEN m.id_away_team ELSE m.id_home_team END) = ? AND s.match_id IN ({ids_str}) {where_f}"
            total_a = conn.execute(q_against, (str(tid),)).fetchone()[0]

        elif category == 'headers':
            q_made = f"SELECT COUNT(*) FROM shots WHERE team_id = ? AND shot_type = 'Header' AND match_id IN ({ids_str})"
            total_m = conn.execute(q_made, (str(tid),)).fetchone()[0]
            q_against = f"SELECT COUNT(*) FROM shots s JOIN matches m ON s.match_id = m.id WHERE (CASE WHEN s.team_id = m.id_home_team THEN m.id_away_team ELSE m.id_home_team END) = ? AND s.shot_type = 'Header' AND s.match_id IN ({ids_str})"
            total_a = conn.execute(q_against, (str(tid),)).fetchone()[0]

        elif category == 'cards':
            q_made = f"SELECT COUNT(*) FROM cards WHERE team_id = ? AND match_id IN ({ids_str})"
            total_m = conn.execute(q_made, (str(tid),)).fetchone()[0]
            q_against = f"SELECT COUNT(*) FROM cards c JOIN matches m ON c.match_id = m.id WHERE (CASE WHEN c.team_id = m.id_home_team THEN m.id_away_team ELSE m.id_home_team END) = ? AND c.match_id IN ({ids_str})"
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

    # Ordenamos por la métrica solicitada
    key = (lambda x: x['total']) if order_by == 'total' else (lambda x: x['avg'])
    results_made.sort(key=key, reverse=True)
    results_against.sort(key=key, reverse=True)

    return results_made, results_against

def get_rankings_from_stats(category='shots', filter_type='all', order_by='total'):
    """Helper para el predictor: convierte las listas de stats en dicts de ranking {ID: Posicion}"""
    made_list, against_list = get_team_stats_core(category, filter_type, order_by)
    # enumerate genera la posición basándose en el orden de la consulta SQL
    rank_made = {item['id']: i+1 for i, item in enumerate(made_list)}
    rank_against = {item['id']: i+1 for i, item in enumerate(against_list)}
    return rank_made, rank_against

def get_team_rankings_logic(team_id, rank_type='tiradores', filter_type='all', limit=None):
    """
    Ranking de jugadores individuales. 
    Si limit tiene valor (ej: 5), busca solo los últimos N partidos finalizados del equipo.
    """
    conn = get_db_connection()
    lt_sub = "(SELECT team_id FROM player_match_details pmd2 JOIN matches m2 ON pmd2.match_id = m2.id WHERE pmd2.player_id = pmd.player_id ORDER BY m2.date DESC LIMIT 1)"
    match_filter = ""
    if limit:
        match_ids = [r[0] for r in conn.execute("SELECT id FROM matches WHERE (id_home_team = ? OR id_away_team = ?) AND finished = 1 ORDER BY date DESC LIMIT ?", (str(team_id), str(team_id), limit)).fetchall()]
        if match_ids:
            ids_str = ",".join([f"'{mid}'" for mid in match_ids])
            match_filter = f"AND pmd.match_id IN ({ids_str})"
        else: return []

    if rank_type == 'tiradores':
        jf = "AND s.on_target = 1" if filter_type == 'target' else "AND s.inside_box = 0" if filter_type == 'long' else ""
        query = f'SELECT pmd.player_id, pmd.player_name, pmd.position, COUNT(s.shot_id) as val, COUNT(DISTINCT pmd.match_id) as pj, {lt_sub} as ct FROM player_match_details pmd LEFT JOIN shots s ON pmd.player_id = s.player_id AND pmd.match_id = s.match_id {jf} WHERE pmd.team_id = ? AND pmd.minutes_played > 0 {match_filter} GROUP BY pmd.player_id HAVING val > 0 ORDER BY val DESC'
    elif rank_type == 'headers':
        query = f'SELECT pmd.player_id, pmd.player_name, pmd.position, COUNT(s.shot_id) as val, COUNT(DISTINCT pmd.match_id) as pj, {lt_sub} as ct FROM player_match_details pmd LEFT JOIN shots s ON pmd.player_id = s.player_id AND pmd.match_id = s.match_id AND s.shot_type = "Header" WHERE pmd.team_id = ? AND pmd.minutes_played > 0 {match_filter} GROUP BY pmd.player_id HAVING val > 0 ORDER BY val DESC'
    elif rank_type == 'yellows':
        query = f'SELECT pmd.player_id, pmd.player_name, pmd.position, COUNT(c.card_id) as val, COUNT(DISTINCT pmd.match_id) as pj, {lt_sub} as ct FROM player_match_details pmd LEFT JOIN cards c ON pmd.player_id = c.player_id AND pmd.match_id = c.match_id WHERE pmd.team_id = ? AND pmd.minutes_played > 0 {match_filter} GROUP BY pmd.player_id HAVING val > 0 ORDER BY val DESC'
    elif rank_type == 'fouls':
        query = f'SELECT pmd.player_id, pmd.player_name, pmd.position, SUM(pmd.fouls_committed) as val, COUNT(DISTINCT pmd.match_id) as pj, {lt_sub} as ct FROM player_match_details pmd WHERE pmd.team_id = ? AND pmd.minutes_played > 0 {match_filter} GROUP BY pmd.player_id HAVING val > 0 ORDER BY val DESC'
    elif rank_type == 'fouls_rec':
        query = f'SELECT pmd.player_id, pmd.player_name, pmd.position, SUM(pmd.fouls_received) as val, COUNT(DISTINCT pmd.match_id) as pj, {lt_sub} as ct FROM player_match_details pmd WHERE pmd.team_id = ? AND pmd.minutes_played > 0 {match_filter} GROUP BY pmd.player_id HAVING val > 0 ORDER BY val DESC'
    
    res = conn.execute(query, (str(team_id),)).fetchall()
    u_map = {"tiradores": "tiros", "headers": "cabezazos", "yellows": "tarjetas", "fouls": "faltas", "fouls_rec": "recibidas"}
    conn.close()
    return [{"player_id": r["player_id"], "name": r["player_name"], "pos": r["position"], "val": r["val"], "pj": r["pj"], "unit": u_map.get(rank_type), "is_transferred": str(r["ct"]) != str(team_id)} for r in res]



def get_prediction_logic(home_id, away_id, category='shots', filter_type='all', referee=None, precalc_ranks=None):
    """
    Motor de predicción probabilística. 
    Cruza los rankings de ataque/defensa y aplica la rigurosidad del árbitro en Tarjetas y Faltas.
    Retorna los rankings individuales de cada parte para su visualización en la UI.
    """
    if precalc_ranks: m_ranks, a_ranks, ref_ranks = precalc_ranks
    else: m_ranks, a_ranks = get_rankings_from_stats(category, filter_type, order_by= 'total'); ref_ranks = None
    rm_h = m_ranks.get(str(home_id), 15); ra_h = a_ranks.get(str(home_id), 15)
    rm_v = m_ranks.get(str(away_id), 15); ra_v = a_ranks.get(str(away_id), 15)
    ref_val = None
    if referee and category in ['cards', 'fouls']:
        if not ref_ranks:
            rc, rf = get_referee_rankings()
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
    """Calcula rankings detallados (Posición, Total, PJ) en pares de ataque vs defensa."""
    categories = [
        ('shots', 'all', 'Tiros', 'Tiros Recibidos'),
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
    # Subconsulta para obtener el nombre del equipo más reciente del jugador
    team_sub = "(SELECT CASE WHEN pmd2.team_id = m2.id_home_team THEN m2.home_team ELSE m2.away_team END FROM player_match_details pmd2 JOIN matches m2 ON pmd2.match_id = m2.id WHERE pmd2.player_id = pmd.player_id ORDER BY m2.date DESC LIMIT 1)"
    pj = "SELECT player_id, COUNT(DISTINCT pmd.match_id) as pj, SUM(pmd.minutes_played) as minutes_played FROM player_match_details pmd WHERE pmd.minutes_played > 0 GROUP BY player_id"
    order_by = '(pj_table.minutes_played >= 300)' if order_by == 'avg' else 'total'
    if rank_type == 'shots':
        jf = "AND s.on_target = 1" if filter_type == 'target' else "AND s.inside_box = 0" if filter_type == 'long' else ""
        query = f'''
        SELECT pmd.player_id as id, pmd.player_name as name, pmd.team_id, {team_sub} as team_name, COUNT(s.shot_id) as total, pj_table.pj as pj, pj_table.minutes_played as minutes_played, (CAST(COUNT(s.shot_id) AS FLOAT) / pj_table.minutes_played)*90 as avg 
        FROM player_match_details pmd 
        LEFT JOIN shots s ON pmd.player_id = s.player_id AND pmd.match_id = s.match_id LEFT JOIN ({pj}) pj_table ON pmd.player_id = pj_table.player_id
        WHERE 1=1 {jf} 
        GROUP BY pmd.player_id HAVING COUNT(s.shot_id) > 0 
        ORDER BY {order_by} DESC LIMIT {limit}'''

    elif rank_type == 'headers':
        query = f'''
        SELECT pmd.player_id as id, pmd.player_name as name, pmd.team_id, {team_sub} as team_name, COUNT(s.shot_id) as total, pj_table.pj as pj, pj_table.minutes_played as minutes_played, (CAST(COUNT(s.shot_id) AS FLOAT) / pj_table.minutes_played)*90 as avg
        FROM player_match_details pmd LEFT JOIN shots s ON pmd.player_id = s.player_id AND pmd.match_id = s.match_id AND s.shot_type = "Header" 
        LEFT JOIN ({pj}) pj_table ON pmd.player_id = pj_table.player_id 
        GROUP BY pmd.player_id HAVING COUNT(s.shot_id) > 0 
        ORDER BY {order_by} DESC LIMIT {limit}'''

    elif rank_type == 'cards':
        query = f'''
        SELECT pmd.player_id as id, pmd.player_name as name, pmd.team_id, {team_sub} as team_name, COUNT(c.card_id) as total, pj_table.pj as pj, pj_table.minutes_played as minutes_played, (CAST(COUNT(c.card_id) AS FLOAT) / pj_table.minutes_played)*90 as avg
        FROM player_match_details pmd 
        LEFT JOIN cards c ON pmd.player_id = c.player_id AND pmd.match_id = c.match_id LEFT JOIN ({pj}) pj_table ON pmd.player_id = pj_table.player_id
        GROUP BY pmd.player_id 
        HAVING COUNT(c.card_id) > 0 
        ORDER BY {order_by} DESC LIMIT {limit}'''
    elif rank_type == 'fouls':
        query = f'''    
        SELECT pmd.player_id as id, pmd.player_name as name, pmd.team_id, {team_sub} as team_name, SUM(pmd.fouls_committed) as total, pj_table.pj as pj, pj_table.minutes_played as minutes_played, (CAST(SUM(pmd.fouls_committed) AS FLOAT) / pj_table.minutes_played)*90 as avg
        FROM player_match_details pmd 
        LEFT JOIN ({pj}) pj_table ON pmd.player_id = pj_table.player_id
        GROUP BY pmd.player_id      
        HAVING SUM(pmd.fouls_committed) > 0 
        ORDER BY {order_by} DESC LIMIT {limit}'''
    elif rank_type == 'fouls_rec':
        query = f'''    
        SELECT pmd.player_id as id, pmd.player_name as name, pmd.team_id, {team_sub} as team_name, SUM(pmd.fouls_received) as total, pj_table.pj as pj, pj_table.minutes_played as minutes_played, (CAST(SUM(pmd.fouls_received) AS FLOAT) / pj_table.minutes_played)*90 as avg
        FROM player_match_details pmd 
        LEFT JOIN ({pj}) pj_table ON pmd.player_id = pj_table.player_id
        GROUP BY pmd.player_id      
        HAVING SUM(pmd.fouls_received) > 0 
        ORDER BY {order_by} DESC LIMIT {limit}'''
    
    res = conn.execute(query).fetchall()
    conn.close()
    return [{"id": r["id"], "name": r["name"], "t_id": r["team_id"], "t_name": r["team_name"], "total": int(r["total"]), "pj": r["pj"], "minutes_played": int(r["minutes_played"]) ,"avg": round(r["avg"], 2)} for r in res]


def get_league_player_stats_last_matches(rank_type='shots', filter_type='all', order_by='total', match_limit=5):
    """Calcula estadísticas de jugadores usando los últimos `match_limit` partidos de cada equipo.

    Para cada equipo, obtenemos sus últimos `match_limit` partidos finalizados y contamos
    los eventos de los jugadores de ese equipo únicamente en esos partidos. Luego agregamos
    por jugador a nivel de liga para construir el top.
    """
    conn = get_db_connection()
    # Obtener lista de equipos (ids)
    team_rows = conn.execute("SELECT DISTINCT id_home_team as id FROM matches UNION SELECT DISTINCT id_away_team as id FROM matches").fetchall()
    team_ids = [str(r['id']) for r in team_rows if r['id'] is not None]

    player_totals = {}  # pid -> {id, name, t_id, total, pj, minutes_played}

    for tid in team_ids:
        # últimos match_limit partidos del equipo
        mrows = conn.execute('SELECT id FROM matches WHERE (id_home_team = ? OR id_away_team = ?) AND finished = 1 ORDER BY date DESC LIMIT ?', (str(tid), str(tid), match_limit)).fetchall()
        match_ids = [r[0] for r in mrows]
        if not match_ids:
            continue
        ids_str = ','.join([f"'{m}'" for m in match_ids])

        if rank_type == 'shots':
            where_f = "AND on_target = 1" if filter_type == 'target' else "AND inside_box = 0" if filter_type == 'long' else ""
            q = f"SELECT s.player_id as pid, s.player_name as pname, s.team_id as t_id, COUNT(*) as total FROM shots s WHERE s.team_id = ? AND s.match_id IN ({ids_str}) {where_f} GROUP BY s.player_id"
            rows = conn.execute(q, (str(tid),)).fetchall()
        elif rank_type == 'headers':
            q = f"SELECT s.player_id as pid, s.player_name as pname, s.team_id as t_id, COUNT(*) as total FROM shots s WHERE s.team_id = ? AND s.shot_type = 'Header' AND s.match_id IN ({ids_str}) GROUP BY s.player_id"
            rows = conn.execute(q, (str(tid),)).fetchall()
        elif rank_type == 'cards':
            q = f"SELECT c.player_id as pid, c.player_name as pname, c.team_id as t_id, COUNT(*) as total FROM cards c WHERE c.team_id = ? AND c.match_id IN ({ids_str}) GROUP BY c.player_id"
            rows = conn.execute(q, (str(tid),)).fetchall()
        elif rank_type == 'fouls':
            q = f"SELECT pmd.player_id as pid, pmd.player_name as pname, pmd.team_id as t_id, SUM(pmd.fouls_committed) as total FROM player_match_details pmd WHERE pmd.team_id = ? AND pmd.match_id IN ({ids_str}) GROUP BY pmd.player_id"
            rows = conn.execute(q, (str(tid),)).fetchall()
        elif rank_type == 'fouls_rec':
            q = f"SELECT pmd.player_id as pid, pmd.player_name as pname, pmd.team_id as t_id, SUM(pmd.fouls_received) as total FROM player_match_details pmd WHERE pmd.team_id = ? AND pmd.match_id IN ({ids_str}) GROUP BY pmd.player_id"
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
        team_name_row = conn.execute('SELECT CASE WHEN pmd2.team_id = m2.id_home_team THEN m2.home_team ELSE m2.away_team END as team_name FROM player_match_details pmd2 JOIN matches m2 ON pmd2.match_id = m2.id WHERE pmd2.player_id = ? ORDER BY m2.date DESC LIMIT 1', (pid,)).fetchone()
        team_name = team_name_row['team_name'] if team_name_row else ''
        avg = round((v['total'] / v['minutes_played'])*90, 2) if v['minutes_played'] > 0 else 0
        out.append({'id': v['id'], 'name': v['name'], 't_id': v['t_id'], 't_name': team_name, 'total': v['total'], 'pj': v['pj'], 'minutes_played': v['minutes_played'], 'avg': avg})

    conn.close()
    if order_by == 'total':
        out.sort(key=lambda x: x[order_by], reverse=True)
    else:
        out.sort(key=lambda x: (x['minutes_played'] >= 150, x[order_by]), reverse=True)
    return out

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
    matches_raw = conn.execute("SELECT * FROM matches WHERE strftime('%Y', date) = ? AND gameweek = ? AND tournament LIKE ? ORDER BY date DESC", (str(year), str(gameweek), f'%{tournament}%')).fetchall()
    # Rankings rápidos para las tarjetas del index
    rs_m, rs_a = get_rankings_from_stats('shots')
    rh_m, rh_a = get_rankings_from_stats('headers')
    rc_m, rc_a = get_rankings_from_stats('cards')
    rf_m, rf_a = get_rankings_from_stats('fouls')    
    ref_c, ref_f = get_referee_rankings()
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
    s_m_all, s_a_all = get_team_stats_core('shots', 'all')
    s_m_tar, s_a_tar = get_team_stats_core('shots', 'target')
    s_m_lng, s_a_lng = get_team_stats_core('shots', 'long')
    h_m, h_a = get_team_stats_core('headers')
    c_m, c_a = get_team_stats_core('cards')
    f_m, f_a = get_team_stats_core('fouls')
    ref_c, ref_f = get_referee_detailed_tops()

    p_shots_all = get_league_player_stats('shots', 'all')
    p_shots_tar = get_league_player_stats('shots', 'target')
    p_shots_lng = get_league_player_stats('shots', 'long')
    p_headers = get_league_player_stats('headers')
    p_cards = get_league_player_stats('cards')
    p_fouls = get_league_player_stats('fouls')
    p_fouls_rec = get_league_player_stats('fouls_rec')

    return render_template_string(STATS_HTML, 
        s_m_all=s_m_all, s_a_all=s_a_all, s_m_tar=s_m_tar, s_a_tar=s_a_tar, s_m_lng=s_m_lng, s_a_lng=s_a_lng, 
        h_m=h_m, h_a=h_a, c_m=c_m, c_a=c_a, f_m=f_m, f_a=f_a, ref_c=ref_c, ref_f=ref_f,
        p_shots_all=p_shots_all, p_shots_tar=p_shots_tar, p_shots_lng=p_shots_lng, 
        p_headers=p_headers, p_cards=p_cards, p_fouls=p_fouls, p_fouls_rec=p_fouls_rec
    )

@app.route('/match/<match_id>')
def match_detail(match_id):
    """Análisis profundo con pizarra y predicciones"""
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

    home_lineup = get_lineup_data(h_mid, match['id_home_team'], cards_dict) if h_mid else []
    away_lineup = get_lineup_data(a_mid, match['id_away_team'], cards_dict) if a_mid else []

    home_subs = sorted([dict(p) for p in conn.execute('SELECT * FROM player_match_details WHERE match_id=? AND team_id=? AND is_starter=0', (str(h_mid or match_id), str(match['id_home_team']))).fetchall()], key=lambda x: {"ARQ":0,"DF":1,"M":2,"DL":3}.get(x['position'],99))
    away_subs = sorted([dict(p) for p in conn.execute('SELECT * FROM player_match_details WHERE match_id=? AND team_id=? AND is_starter=0', (str(a_mid or match_id), str(match['id_away_team']))).fetchall()], key=lambda x: {"ARQ":0,"DF":1,"M":2,"DL":3}.get(x['position'],99))

    stats = {"home": {"shots": 0, "target": 0, "fouls": 0, "cards": 0}, "away": {"shots": 0, "target": 0, "fouls": 0, "cards": 0}}
    if match['finished'] == 1:
        for r in conn.execute('SELECT team_id, COUNT(*) as tot, SUM(on_target) as tar FROM shots WHERE match_id=? GROUP BY team_id', (str(match_id),)).fetchall():
            k = "home" if str(r['team_id']) == str(match['id_home_team']) else "away"; stats[k]["shots"], stats[k]["target"] = r['tot'], r['tar'] or 0
        stats["home"]["cards"] = conn.execute('SELECT COUNT(*) FROM cards WHERE match_id=? AND team_id=?', (str(match_id), str(match['id_home_team']))).fetchone()[0]
        stats["away"]["cards"] = conn.execute('SELECT COUNT(*) FROM cards WHERE match_id=? AND team_id=?', (str(match_id), str(match['id_away_team']))).fetchone()[0]
        for r in conn.execute('SELECT team_id, SUM(fouls_committed) as f FROM player_match_details WHERE match_id=? GROUP BY team_id', (str(match_id),)).fetchall():
            k = "home" if str(r['team_id']) == str(match['id_home_team']) else "away"; stats[k]["fouls"] = r['f'] or 0
    conn.close()
    return render_template_string(DETAIL_HTML, match=match, home_lineup=home_lineup, away_lineup=away_lineup, home_subs=home_subs, away_subs=away_subs, home_top=get_team_rankings_logic(match['id_home_team']), away_top=get_team_rankings_logic(match['id_away_team']), stats=stats, m_note=m_note, pred_s=pred_s, pred_h=pred_h, pred_c=pred_c, pred_f=pred_f, lineup_label="Formación" if match['finished'] else "Último 11", current_filter=sf)

@app.route('/api/team_ranking/<team_id>')
def api_team_ranking(team_id):
    limit = request.args.get('limit', type=int)
    return jsonify(get_team_rankings_logic(team_id, request.args.get('type', 'tiradores'), request.args.get('filter', 'all'), limit))


@app.route('/api/team_stats')
def api_team_stats():
    """Devuelve estadísticas de equipos por categoría/side. Parámetros: category, filter, side (made|against), limit (opcional)."""
    category = request.args.get('category', 'shots')
    filter_type = request.args.get('filter', 'all')
    side = request.args.get('side', 'made')
    limit = request.args.get('limit', type=int)
    made, against = get_team_stats_core(category, filter_type, order_by='total', limit=limit)
    data = made if side == 'made' else against
    return jsonify(data)


@app.route('/api/player_stats')
def api_player_stats():
    """Devuelve estadísticas de jugadores. Parámetros: rank_type, filter, limit_matches (opcional).
       Si se pasa `limit_matches`, calcula métricas usando solo los últimos N partidos por jugador.
    """
    rank_type = request.args.get('rank_type', 'shots')
    filter_type = request.args.get('filter', 'all')
    limit_matches = request.args.get('limit_matches', type=int)
    if limit_matches:
        data = get_league_player_stats_last_matches(rank_type, filter_type, match_limit=limit_matches)
    else:
        limit = request.args.get('limit', type=int) or 100
        data = get_league_player_stats(rank_type, filter_type, limit=limit)
    return jsonify(data)

@app.route('/player_info/<player_id>/<match_id>')
def player_info(player_id, match_id):
    conn = get_db_connection()
    
    # 1. Info básica
    info = conn.execute('''
        SELECT pmd.*, m.home_team, m.away_team, m.id_home_team, m.id_away_team 
        FROM player_match_details pmd 
        JOIN matches m ON pmd.match_id = m.id 
        WHERE pmd.player_id = ? 
        ORDER BY m.date DESC LIMIT 1
    ''', (player_id,)).fetchone()

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
                (SELECT COUNT(*) FROM shots WHERE player_id = ? AND match_id IN ({ids_str})) as shots,
                (SELECT COUNT(*) FROM shots WHERE player_id = ? AND match_id IN ({ids_str}) AND on_target=1) as target,
                (SELECT COUNT(*) FROM shots WHERE player_id = ? AND match_id IN ({ids_str}) AND inside_box=0) as long,
                (SELECT COUNT(*) FROM shots WHERE player_id = ? AND match_id IN ({ids_str}) AND shot_type='Header') as headers,
                (SELECT COUNT(*) FROM cards WHERE player_id = ? AND match_id IN ({ids_str})) as cards
            FROM player_match_details WHERE player_id = ? AND match_id IN ({ids_str})
        ''', (player_id, player_id, player_id, player_id, player_id, player_id)).fetchone()
        return dict(s)

    # Lógica de Rankings (Top 20)
    def get_top_rankings():
        # Definimos los componentes de cada métrica
        # Estructura: (Etiqueta, Tabla/Join, Función Agregada, Filtro extra)
        metrics = [
            ("Tiros Totales", "shots s JOIN player_match_details p ON s.player_id = p.player_id AND s.match_id = p.match_id", "COUNT(*)", []),
            ("Tiros al Arco", "shots s JOIN player_match_details p ON s.player_id = p.player_id AND s.match_id = p.match_id", "COUNT(*)", ["s.on_target=1"]),
            ("Tiros Lejanos", "shots s JOIN player_match_details p ON s.player_id = p.player_id AND s.match_id = p.match_id", "COUNT(*)", ["s.inside_box=0"]),
            ("Faltas Cometidas", "player_match_details p", "SUM(p.fouls_committed)", []),
            ("Faltas Recibidas", "player_match_details p", "SUM(p.fouls_received)", []),
            ("Tarjetas", "cards c JOIN player_match_details p ON c.player_id = p.player_id AND c.match_id = p.match_id", "COUNT(*)", []),
            ("Cabezazos", "shots s JOIN player_match_details p ON s.player_id = p.player_id AND s.match_id = p.match_id", "COUNT(*)", ["s.shot_type='Header'"])
        ]
        
        scopes = {
            "liga": "1=1",
            "equipo": f"p.team_id = '{info['team_id']}'",
            "posicion": f"p.position = '{info['position']}'"
        }

        results = {"liga": [], "equipo": [], "posicion": []}

        for scope_name, scope_filter in scopes.items():
            for label, table_clause, agg_func, metric_filters in metrics:
                # Construcción limpia de la cláusula WHERE
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
    
    last_5_ids = [r[0] for r in conn.execute('SELECT match_id FROM player_match_details WHERE player_id=? ORDER BY match_id DESC LIMIT 5', (player_id,)).fetchall()]
    l5_stats = get_stats_summary(last_5_ids)
    
    all_ids = [r[0] for r in conn.execute('SELECT match_id FROM player_match_details WHERE player_id=?', (player_id,)).fetchall()]
    gen_stats = get_stats_summary(all_ids)
    
    rankings_top = get_top_rankings()
    note = conn.execute('SELECT notes FROM player_notes WHERE player_id = ?', (player_id,)).fetchone()

    conn.close()

    return jsonify({
        "name": info["player_name"],
        "team": info["home_team"] if str(info["team_id"]) == str(info["id_home_team"]) else info["away_team"],
        "pos": "Delantero" if info["position"] == "DL" else "Mediocampista" if info["position"] == "M" else "Defensor" if info["position"] == "DF" else "Arquero" if info["position"] == "ARQ" else "Desconocido",
        "number": info["shirt_number"],
        "stats": {"partido": match_stats, "l5": l5_stats, "general": gen_stats},
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
    # Busca jugadores únicos por nombre que hayan jugado en ese equipo
    players = conn.execute('''
        SELECT DISTINCT player_id, player_name, position 
        FROM player_match_details 
        WHERE team_id = ? AND player_name LIKE ? 
        LIMIT 8
    ''', (str(team_id), f'%{q}%')).fetchall()
    conn.close()
    return jsonify([dict(p) for p in players])

@app.route('/team/<team_id>')
def team_page(team_id):
    conn = get_db_connection()
    # Obtener nombre del equipo
    team_name = conn.execute('SELECT home_team FROM matches WHERE id_home_team = ? UNION SELECT away_team FROM matches WHERE id_away_team = ? LIMIT 1', (str(team_id), str(team_id))).fetchone()
    if not team_name: return "Equipo no encontrado", 404
    
    # Historial de partidos
    matches = conn.execute('''
        SELECT * FROM matches 
        WHERE id_home_team = ? OR id_away_team = ? 
        ORDER BY date DESC
    ''', (str(team_id), str(team_id))).fetchall()
    
    global_ranks = get_team_global_positions(team_id)
    conn.close()
    
    return render_template_string(TEAM_HTML, 
                                  team_id=team_id, 
                                  team_name=team_name[0], 
                                  matches=matches, 
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
        c_h = conn.execute('SELECT COUNT(*) FROM cards WHERE match_id=? AND team_id=?', (mid, str(m['id_home_team']))).fetchone()[0] or 0
        c_v = conn.execute('SELECT COUNT(*) FROM cards WHERE match_id=? AND team_id=?', (mid, str(m['id_away_team']))).fetchone()[0] or 0
        
        row = dict(m)
        row['stats'] = {'h_fouls': f_h, 'v_fouls': f_v, 'h_cards': c_h, 'v_cards': c_v}
        matches.append(row)
        total_cards_acc += (c_h + c_v)
        total_fouls_acc += (f_h + f_v)

    # 2. Rankings Globales de Árbitros
    rc, rf = get_referee_rankings()
    ranks = {'cards': rc.get(name, "N/A"), 'fouls': rf.get(name, "N/A")}

    # 3. Equipos más castigados (Top Targets)
    # Mapping de IDs a Nombres para el arbitraje
    t_map = {str(r['id']): r['name'] for r in conn.execute('SELECT DISTINCT id_home_team as id, home_team as name FROM matches').fetchall()}

    def get_top_teams(metric_type):
        if metric_type == 'cards':
            q = 'SELECT team_id, COUNT(*) as tot, COUNT(DISTINCT match_id) as pj FROM cards WHERE match_id IN (SELECT id FROM matches WHERE referee=?) GROUP BY team_id ORDER BY tot DESC LIMIT 5'
        elif metric_type == 'fouls_committed':
            q = 'SELECT team_id, SUM(fouls_committed) as tot, COUNT(DISTINCT match_id) as pj FROM player_match_details WHERE match_id IN (SELECT id FROM matches WHERE referee=?) GROUP BY team_id ORDER BY tot DESC LIMIT 5'
        else: # fouls_received
            q = 'SELECT team_id, SUM(fouls_received) as tot, COUNT(DISTINCT match_id) as pj FROM player_match_details WHERE match_id IN (SELECT id FROM matches WHERE referee=?) GROUP BY team_id ORDER BY tot DESC LIMIT 5'
        
        res = conn.execute(q, (name,)).fetchall()
        return [{"name": t_map.get(str(r[0]), "N/A"), "total": r[1], "pj": r[2]} for r in res]

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

FOOTER_HTML = '''<footer class="mt-20 py-6 border-t border-slate-700/50 text-center">
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
    <div class="max-w-5xl mx-auto">
        <header class="flex flex-col md:flex-row justify-between items-center mb-12 gap-6">
            <a href="/"><h1 class="text-6xl font-black italic uppercase tracking-tighter text-white">ARG STATS</h1></a>
            <nav class="flex gap-4">
                <a href="/" class="bg-sky-600 px-6 py-2 rounded-xl text-xs font-black uppercase shadow-lg">Partidos</a>
                <a href="/stats" class="bg-slate-800 hover:bg-slate-700 px-6 py-2 rounded-xl text-xs font-black uppercase transition-all border border-slate-700">Estadísticas Liga</a>
            </nav>
        </header>

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
                            {% for i in range(1, 29) %}
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
        
        <div class="grid grid-cols-1 gap-8">
            {% macro score_color(val) %}{% if val <= 30 %}text-red-500{% elif val <= 70 %}text-blue-500{% else %}text-green-500{% endif %}{% endmacro %}
            {% for m in matches %}
            <div class="bg-slate-800 p-8 rounded-[2.5rem] border border-slate-700 shadow-lg relative overflow-hidden transition-all hover:border-slate-600">
                    
                    <div class="flex flex-wrap justify-between items-center mb-8 border-b border-slate-700/50 pb-4 gap-4">
                        <div class="flex items-center gap-4">
                            <span class="bg-sky-500/10 text-sky-400 px-4 py-1.5 rounded-xl text-[10px] font-black uppercase tracking-widest border border-sky-500/20">{{ m.tournament }}</span>
                            <span class="text-[14px] font-black text-slate-300 tracking-tighter">{{ m.date[:16] }}</span>
                            <div class="flex items-center gap-2">
                                <span class="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">Árbitro:</span>
                                
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
                            <a href="/team/{{ m.id_home_team }}" class="text-3xl font-black uppercase tracking-tighter hover:text-sky-400 transition-colors mb-6 block">
                                {{ m.home_team }}
                            </a>
                            <div class="grid grid-cols-2 gap-x-8 gap-y-4">
                                <div class="flex flex-col">
                                    <span class="text-[9px] font-black text-slate-500 uppercase">Tiros</span>
                                    <span class="text-xl font-black {{ score_color(m.preds.s_home) }}">{{ m.preds.s_home }}</span>
                                </div>
                                <div class="flex flex-col">
                                    <span class="text-[9px] font-black text-slate-500 uppercase">Cabeza</span>
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
                            <a href="/team/{{ m.id_away_team }}" class="text-3xl font-black uppercase tracking-tighter hover:text-sky-400 transition-colors mb-6 block">
                                {{ m.away_team }}
                            </a>
                            <div class="grid grid-cols-2 gap-x-8 gap-y-4">
                                <div class="flex flex-col">
                                    <span class="text-[9px] font-black text-slate-500 uppercase">Tiros</span>
                                    <span class="text-xl font-black {{ score_color(m.preds.s_away) }}">{{ m.preds.s_away }}</span>
                                </div>
                                <div class="flex flex-col">
                                    <span class="text-[9px] font-black text-slate-500 uppercase">Cabeza</span>
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
    ''' + FOOTER_HTML + '''
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
            
            // Lógica para navegar hacia atrás (delta = -1)
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
            // Lógica para navegar hacia adelante (delta = 1)
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
</body></html>
'''

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
    </style>
</head>
<body class="p-8 pb-0 font-sans">
    <div class="max-w-[1500px] mx-auto">
        <header class="flex flex-row justify-between items-center mb-16 gap-4">
            <a href="/"><h1 class="text-6xl font-black italic uppercase tracking-tighter text-white">ARG STATS</h1></a>

            <div class="flex bg-slate-800/50 p-1 rounded-2xl border border-slate-700 shadow-xl backdrop-blur-md">
                <button id="btn-teams" onclick="switchMode('teams')" class="px-6 py-2 rounded-xl text-[10px] font-black uppercase transition-all bg-sky-600 text-white shadow-lg">Equipos</button>
                <button id="btn-players" onclick="switchMode('players')" class="px-6 py-2 rounded-xl text-[10px] font-black uppercase transition-all text-slate-400 hover:text-white">Jugadores</button>
            </div>

            <nav>
                <a href="/" class="bg-slate-800 hover:bg-slate-700 px-6 py-2 rounded-xl text-xs font-black uppercase transition-all border border-slate-700">← Volver</a>
            </nav>
        </header>

        <div id="stats-grid-root" class="space-y-20">
            </div>
    </div>
</body>
''' + FOOTER_HTML + '''
<script>
    // Función de color solicitada
    function getPosColorClass(v) { 
        if (v === 'N/A') return 'text-slate-500';
        const n = parseInt(v);
        if (n > 20) return 'text-red-500'; 
        if (n > 10) return 'text-blue-500'; 
        return 'text-green-500'; 
    }

    const teamStatsData = [
        { section: 'Análisis de Tiros', cols: 3, stats: [
            { title: 'Tiros Realizados', data: {{ s_m_all|tojson }}, api: {category: 'shots', filter: 'all', side: 'made'} },
            { title: 'Tiros al Arco', data: {{ s_m_tar|tojson }}, api: {category: 'shots', filter: 'target', side: 'made'} },
            { title: 'Tiros de Lejos', data: {{ s_m_lng|tojson }}, api: {category: 'shots', filter: 'long', side: 'made'} },
            { title: 'Tiros Recibidos', data: {{ s_a_all|tojson }}, api: {category: 'shots', filter: 'all', side: 'against'} },
            { title: 'Recibidos al Arco', data: {{ s_a_tar|tojson }}, api: {category: 'shots', filter: 'target', side: 'against'} },
            { title: 'Recibidos de Lejos', data: {{ s_a_lng|tojson }}, api: {category: 'shots', filter: 'long', side: 'against'} }
        ]},
        { section: 'Duelos y Disciplina', cols: 2, stats: [
            { title: 'Cabezazos Propios', data: {{ h_m|tojson }}, api: {category: 'headers', filter: 'all', side: 'made'} },
            { title: 'Cabezazos Recibidos', data: {{ h_a|tojson }}, api: {category: 'headers', filter: 'all', side: 'against'} },
            { title: 'Tarjetas Recibidas (Equipo)', data: {{ c_m|tojson }}, api: {category: 'cards', filter: 'all', side: 'made'} },
            { title: 'Tarjetas Generadas', data: {{ c_a|tojson }}, api: {category: 'cards', filter: 'all', side: 'against'} },
            { title: 'Faltas Cometidas', data: {{ f_m|tojson }}, api: {category: 'fouls', filter: 'all', side: 'made'} },
            { title: 'Faltas Recibidas', data: {{ f_a|tojson }}, api: {category: 'fouls', filter: 'all', side: 'against'} }
        ]},
        { section: 'Rankings de Árbitros', cols: 2, stats: [
            { title: 'Árbitros: Cobradores (Faltas)', data: {{ ref_f|tojson }} },
            { title: 'Árbitros: Tarjeteros (Tarjetas)', data: {{ ref_c|tojson }} }
        ]}
    ];

    const playerStatsData = [
        { section: 'Top Jugadores - Ataque', cols: 3, stats: [
            { title: 'Tiros Totales', data: {{ p_shots_all|tojson }}, api: {type: 'player', rank_type: 'shots', filter: 'all'} },
            { title: 'Tiros al Arco', data: {{ p_shots_tar|tojson }}, api: {type: 'player', rank_type: 'shots', filter: 'target'} },
            { title: 'Tiros desde Lejos', data: {{ p_shots_lng|tojson }}, api: {type: 'player', rank_type: 'shots', filter: 'long'} }
        ]},
        { section: 'Top Jugadores - Juego Físico', cols: 2, stats: [
            { title: 'Cabezazos', data: {{ p_headers|tojson }}, api: {type: 'player', rank_type: 'headers', filter: 'all'} },
            { title: 'Tarjetas', data: {{ p_cards|tojson }}, api: {type: 'player', rank_type: 'cards', filter: 'all'} },
            { title: 'Faltas Cometidas', data: {{ p_fouls|tojson }}, api: {type: 'player', rank_type: 'fouls', filter: 'all'} },
            { title: 'Faltas Recibidas', data: {{ p_fouls_rec|tojson }}, api: {type: 'player', rank_type: 'fouls_received', filter: 'all'} }
        ]}
    ];

    let currentMode = 'teams';
    let pages = {};

    window._statLast5 = {};
    window._statMap = {};

    function switchMode(mode) {
        currentMode = mode;
        
        // DESACTIVAR TODOS los botones de últimos 5 al cambiar de modo
        if (window._statMap) {
            Object.keys(window._statMap).forEach(id => {
                const stat = window._statMap[id];
                if (stat._origData) stat.data = JSON.parse(JSON.stringify(stat._origData));
                pages[id] = 1;
            });
        }
        window._statLast5 = {}; // Limpia el estado de botones activos

        document.getElementById('btn-teams').className = mode === 'teams' ? 'px-6 py-2 rounded-xl text-[10px] font-black uppercase transition-all bg-sky-600 text-white shadow-lg' : 'px-6 py-2 rounded-xl text-[10px] font-black uppercase transition-all text-slate-400 hover:text-white';
        document.getElementById('btn-players').className = mode === 'players' ? 'px-6 py-2 rounded-xl text-[10px] font-black uppercase transition-all bg-sky-600 text-white shadow-lg' : 'px-6 py-2 rounded-xl text-[10px] font-black uppercase transition-all text-slate-400 hover:text-white';
        
        renderAll();
    }

    function renderAll() {
        const root = document.getElementById('stats-grid-root');
        const dataGroups = currentMode === 'teams' ? teamStatsData : playerStatsData;
        root.innerHTML = '';

        dataGroups.forEach((group, gIdx) => {
            const section = document.createElement('div');
            section.innerHTML = `<h2 class="text-2xl font-black uppercase italic tracking-tighter mb-8 border-l-4 border-sky-500 pl-4 text-slate-300">${group.section}</h2>`;
            
            const grid = document.createElement('div');
            // Usamos group.cols para definir el ancho del grid
            grid.className = `grid grid-cols-1 md:grid-cols-2 lg:grid-cols-${group.cols || 3} gap-6`;
            
            group.stats.forEach((stat, sIdx) => {
                const id = `box-${gIdx}-${sIdx}`;
                if (!pages[id]) pages[id] = 1;
                const container = document.createElement('div');
                container.id = id;
                grid.appendChild(container);
                renderStatBox(container, stat, id);
            });
            section.appendChild(grid);
            root.appendChild(section);
        });
    }

    // Modificación en renderStatBox para mostrar el equipo del jugador
    function renderStatBox(container, stat, id) 
    {
        const perPage = 10;
        const page = pages[id] || 1;
        const start = (page - 1) * perPage;
        const visibleData = stat.data.slice(start, start + perPage);
        const totalPages = Math.ceil(stat.data.length / perPage) || 1;

        // Guardamos el objeto `stat` por id para no serializarlo en los atributos onclick
        window._statMap = window._statMap || {};
        window._statMap[id] = stat;
        window._statMap[id]._origData = window._statMap[id]._origData || JSON.parse(JSON.stringify(stat.data));
        window._statLast5 = window._statLast5 || {};
        const last5Active = !!window._statLast5[id];

        container.innerHTML = `
            <div class="bg-slate-800/40 rounded-[2rem] border border-slate-700/50 shadow-xl flex flex-col h-full overflow-hidden">
                <div class="bg-slate-800/50 px-6 py-4 border-b border-slate-700/50 flex justify-between items-center">
                    <h3 class="font-black text-sky-400 uppercase text-[13px] tracking-widest">${stat.title}</h3>
                    ${stat.api ? `<button id="l5-btn-${id}" onclick='toggleStatLast5("${id}")' class="text-[10px] font-black uppercase px-3 py-1 rounded-full border ${last5Active ? 'bg-sky-500 text-white' : 'bg-slate-800 text-slate-400'}">Últimos 5</button>` : ''}
                </div>

                <div class="p-4 flex-1 space-y-2">
                    ${visibleData.map((item, i) => `
                        <div class="flex justify-between items-center bg-slate-900/40 p-2  rounded-xl border border-slate-800/50 hover:border-slate-700 transition-all">
                            <div class="flex flex-col truncate">
                                <span class="text-[13px] font-bold text-slate-100 truncate">
                                    <b class="${getPosColorClass(start + i + 1)} mr-2">#${start + i + 1}</b>
                                    ${currentMode === 'players' ? item.name : item.id ? `<a href="/team/${item.id}" class=" text-[15px] hover:text-sky-400">${item.name}</a>` : `<a href="/referee/${item.name}" class=" text-[15px] hover:text-sky-400">${item.name}</a>`}
                                </span>
                                ${currentMode === 'players' ? `<a href="/team/${item.t_id}" class="text-[12px] font-black text-sky-500 uppercase mt-0.5 hover:underline decoration-sky-500/50 underline-offset-2">🛡️ ${item.t_name}</a>` : ''}
                                <div class="flex gap-3 text-[12px] font-black text-slate-500 uppercase mt-1">
                                    <span>Total: <b class=" text-[14px] text-slate-300">${item.total}</b></span>
                                    <span>PJ: <b class="text-[14px] text-slate-300">${item.pj}</b></span>
                                    ${item.minutes_played ? `<span>Min: <b class="text-[14px] text-slate-300">${item.minutes_played}</b></span>` : ''}                                    </div>
                            </div>
                            <div class="text-right ml-4">
                                <span class="text-sm font-black text-emerald-400">${item.avg}</span>
                                <div class="text-[10px] font-bold text-slate-500 uppercase">${currentMode === 'players' ? 'Por 90' : 'Prom'}</div>
                            </div>
                        </div>
                    `).join('')}
                </div>

                <div class="p-4 bg-slate-900/30 border-t border-slate-700/30 flex justify-between items-center">
                    <button onclick='changeLocalPage("${id}", -1)' 
                            class="p-2 rounded-lg bg-slate-800 text-sky-500 hover:bg-sky-600 hover:text-white disabled:opacity-0 transition-all" 
                            ${page === 1 ? 'disabled' : ''}>
                        <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="3" viewBox="0 0 24 24"><path d="m15 18-6-6 6-6"/></svg>
                    </button>
                    
                    <span class="text-[10px] font-black text-slate-500 uppercase tracking-widest">${page} / ${totalPages}</span>
                    
                    <button onclick='changeLocalPage("${id}", 1)' 
                            class="p-2 rounded-lg bg-slate-800 text-sky-500 hover:bg-sky-600 hover:text-white disabled:opacity-0 transition-all" 
                            ${page === totalPages ? 'disabled' : ''}>
                        <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="3" viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"/></svg>
                    </button>
                </div>
            </div>
        `;
    }

    // Alterna el modo 'Últimos 5' para un box y recupera datos si es necesario
    function toggleStatLast5(id) {
        window._statLast5 = window._statLast5 || {};
        window._statLast5[id] = !window._statLast5[id];
        const container = document.getElementById(id);
        const stat = (window._statMap && window._statMap[id]) || null;
        if (!stat) return;
        const btn = document.getElementById(`l5-btn-${id}`);
        if (window._statLast5[id]) {
            // fetchar datos con limit=5 (equipos) o limit_matches=5 (jugadores)
            if (stat.api && stat.api.type === 'player') {
                const url = `/api/player_stats?rank_type=${stat.api.rank_type}&filter=${stat.api.filter}&limit_matches=5`;
                if (btn) { btn.disabled = true; }
                console.log('Fetching player stats last5 ->', url, stat);
                fetch(url)
                    .then(r => {
                        if (!r.ok) throw new Error('HTTP ' + r.status);
                        return r.json();
                    })
                    .then(data => {
                        // limitar a 10 páginas de jugadores (10 por página -> 100 items)
                        const MAX_ITEMS = 10 * 10;
                        stat.data = Array.isArray(data) ? data.slice(0, MAX_ITEMS) : data;
                        // reiniciamos la paginación para este box
                        pages[id] = 1;
                        // actualizar estado visual del botón
                        if (btn) { btn.classList.add('bg-sky-500'); btn.classList.remove('bg-slate-800'); btn.classList.add('text-white'); btn.classList.remove('text-slate-400'); }
                        renderStatBox(container, stat, id);
                    })
                    .catch(e => { console.error('Error fetching player_stats', e); alert('Error cargando últimos 5 (ver consola)'); })
                    .finally(() => { if (btn) { btn.disabled = false; } });
            } else {
                const url = `/api/team_stats?category=${stat.api.category}&filter=${stat.api.filter}&side=${stat.api.side}&limit=5`;
                if (btn) { btn.disabled = true; }
                console.log('Fetching team stats last5 ->', url, stat);
                fetch(url)
                    .then(r => {
                        if (!r.ok) throw new Error('HTTP ' + r.status);
                        return r.json();
                    })
                    .then(data => {
                        stat.data = data;
                        if (btn) { btn.classList.add('bg-sky-500'); btn.classList.remove('bg-slate-800'); btn.classList.add('text-white'); btn.classList.remove('text-slate-400'); }
                        renderStatBox(container, stat, id);
                    })
                    .catch(e => { console.error('Error fetching team_stats', e); alert('Error cargando últimos 5 (ver consola)'); })
                    .finally(() => { if (btn) { btn.disabled = false; } });
            }
            } else {
                // restaurar datos originales
                stat.data = stat._origData ? JSON.parse(JSON.stringify(stat._origData)) : stat.data;
                // reiniciamos paginación al restaurar
                pages[id] = 1;
                if (btn) { btn.classList.remove('bg-sky-500'); btn.classList.add('bg-slate-800'); btn.classList.remove('text-white'); btn.classList.add('text-slate-400'); }
                renderStatBox(container, stat, id);
            }
    }

    function changeLocalPage(id, delta) {
        if (!pages[id]) pages[id] = 1;
        pages[id] += delta;
        const container = document.getElementById(id);
        const stat = (window._statMap && window._statMap[id]) || null;
        if (!stat) return; // seguridad
        renderStatBox(container, stat, id);
    }

    renderAll();
</script>
</body></html>'''

TEAM_HTML = '''
<!DOCTYPE html>
<html lang="es">
<style>
    .custom-scroll::-webkit-scrollbar { width: 6px; }
    .custom-scroll::-webkit-scrollbar-track { background: #1e293b; border-radius: 10px; }
    .custom-scroll::-webkit-scrollbar-thumb { background: #334155; border-radius: 10px; border: 1px solid #1e293b; }
    .custom-scroll::-webkit-scrollbar-thumb:hover { background: #0ea5e9; }
    .custom-scroll { scrollbar-width: thin; scrollbar-color: #334155 #1e293b; }
    .shooter-card { 
        background: rgba(15, 23, 42, 0.6); 
        padding: 0.75rem 1rem; /* Un poco más de aire que en la pizarra */
        border-radius: 1rem; 
        border: 1px solid #1e293b; 
        transition: all 0.2s; 
        cursor: pointer; 
        }
    .shooter-card:hover { 
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
    <div class="max-w-7xl mx-auto space-y-12">
        <header class="flex justify-between items-center">
            <div>
                <a href="/" class="text-sky-500 font-black uppercase text-xs tracking-widest hover:underline">← Volver a Partidos</a>
                <h1 class="text-6xl font-black italic uppercase tracking-tighter text-white mt-2">{{ team_name }}</h1>
            </div>
            <div class="bg-slate-800 p-4 rounded-3xl border border-slate-700 text-center min-w-[200px]">
                <span class="text-[10px] font-black text-slate-500 uppercase block mb-1">ID de Equipo</span>
                <span class="text-2xl font-mono font-black text-sky-400">{{ team_id }}</span>
            </div>
        </header>

        <div class="grid grid-cols-1 lg:grid-cols-3 gap-10">

            <!-- ULTIMOS PARTIDOS -->
            <div class="space-y-6">
                <h2 class="text-xl font-black uppercase italic tracking-tighter border-l-4 border-slate-500 pl-4">Últimos Partidos</h2>
                <div class="space-y-3 max-h-[600px] overflow-y-auto pr-2 custom-scroll">
                    {% for m in matches %}
                    <a href="/match/{{ m.id }}" class="block bg-slate-900/50 p-4 rounded-2xl border border-slate-800 hover:border-sky-500 transition-all">
                        <div class="flex justify-between text-[9px] font-black text-slate-500 uppercase mb-2">
                            <span>{{ m.date[:10] }}</span>
                            <span>{{ m.tournament }} Fecha {{ m.gameweek }}</span>
                        </div>
                        <div class="flex justify-between items-center">
                            <span class="text-sm font-bold whitespace-nowrap w-[140px] overflow-hidden text-ellipsis {{ 'text-sky-400' if m.id_home_team|string == team_id|string else 'text-slate-400' }}">{{ m.home_team }}</span>
                            <span class="bg-slate-800 px-3 py-1 rounded-lg font-mono font-black text-xs">{{ m.score or 'VS' }}</span>
                            <span class="text-sm text-right font-bold whitespace-nowrap w-[140px] overflow-hidden text-ellipsis {{ 'text-sky-400' if m.id_away_team|string == team_id|string else 'text-slate-400' }}">{{ m.away_team }}</span>
                        </div>
                    </a>
                    {% endfor %}
                </div>
            </div>
            <div class="space-y-6">
                <h2 class="text-xl font-black uppercase italic tracking-tighter border-l-4 border-sky-500 pl-4">Posiciones en Estadísticas</h2>
                {# Macro para definir el color de la posición basado en tu función #}
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

            <!-- RANKING -->
            <div class="space-y-3">
                <div class="flex justify-between items-center border-l-4 border-orange-500 pl-4">
                    <h2 class="text-xl font-black uppercase italic tracking-tighter">Estadísticas Plantel</h2>
                    <button onclick="toggleL5()" id="l5-btn" class="text-[9px] px-3 py-1 rounded-full border border-slate-700 font-black uppercase text-slate-500 hover:text-white transition-all">Últimos 5</button>
                </div>
                <div class="flex flex-col items-center border-b border-sky-400/20 pb-2 mb-3">
                    <div class="flex flex-wrap justify-center gap-1 mb-2">
                        <button onclick="updateTeamRanking('{{ team_id }}', 'tiradores', 'all', event)" class="text-[11px] px-1.5 py-0.5 rounded bg-sky-500 text-white font-bold rank-btn" id="btn-main">Tiros</button>
                        <button onclick="updateTeamRanking('{{ team_id }}', 'headers', 'all', event)" class="text-[11px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-bold rank-btn">Cabeza</button>
                        <button onclick="updateTeamRanking('{{ team_id }}', 'yellows', 'all', event)" class="text-[11px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-bold rank-btn">Tarj.</button>
                        <button onclick="updateTeamRanking('{{ team_id }}', 'fouls', 'all', event)" class="text-[11px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-bold rank-btn">Faltas</button>
                        <button onclick="updateTeamRanking('{{ team_id }}', 'fouls_rec', 'all', event)" class="text-[11px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-bold rank-btn">Recib.</button>
                    </div>
                    <div id="sub-filters" class="sub-menu flex gap-1 justify-center mt-2" style="display:none;">
                        <button onclick="updateTeamRanking('{{ team_id }}', 'tiradores', 'all', event)" id="sub-all" class="text-[10px] px-1.5 py-0.5 rounded bg-sky-500 text-white font-black sub-btn">Todos</button>
                        <button onclick="updateTeamRanking('{{ team_id }}', 'tiradores', 'target', event)" id="sub-target" class="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-500 font-black sub-btn">Arco</button>
                        <button onclick="updateTeamRanking('{{ team_id }}', 'tiradores', 'long', event)" id="sub-long" class="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-500 font-black sub-btn">Lejos</button>
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
    '''+FOOTER_HTML+'''
    <script>
        let currentType = 'tiradores';
        let isL5 = false;
        let teamPlayerData = []; 
        let playerPage = 1;
        const playersPerPage = 10; // Cuántos mostrar por página
        const teamId = "{{ team_id }}";

        // 1. Alternar últimos 5 partidos
        function toggleL5() {
            isL5 = !isL5;
            const btn = document.getElementById('l5-btn');
            btn.classList.toggle('bg-sky-500', isL5);
            btn.classList.toggle('text-white', isL5);
            updateTeamRanking(teamId, currentType); // Recargar
        }

        // 2. Función única para dibujar la lista
        function renderPlayerPage() {
            const list = document.getElementById('player-ranking-list');
            const info = document.getElementById('player-page-info');
            
            const start = (playerPage - 1) * playersPerPage;
            const visibleData = teamPlayerData.slice(start, start + playersPerPage);
            const totalPages = Math.ceil(teamPlayerData.length / playersPerPage) || 1;

            info.innerText = `${playerPage} / ${totalPages}`;
            
            list.innerHTML = visibleData.map(r => `
                <div class="shooter-card ${r.is_transferred ? 'border-red-500/50' : 'border-slate-800'}" 
                    data-pid="${r.player_id}">
                    <div class="flex justify-between items-center gap-2">
                        <span class="font-bold truncate text-[14px] ${r.is_transferred ? 'text-red-400' : 'text-slate-200'}">
                            ${r.name.split(' ').pop()} 
                            <span class="text-slate-500 text-[11px] italic">(${r.pos})</span>
                        </span>
                        <span class="text-[12px] font-bold italic whitespace-nowrap ${r.is_transferred ? 'text-red-400' : 'text-slate-400'}">
                            <span class="${r.is_transferred ? 'text-red-400' : 'text-sky-400'} font-black">${r.val}</span> 
                            ${r.unit} / ${r.pj} PJ
                        </span>
                    </div>
                </div>
            `).join('') || '<p class="text-[10px] text-slate-600 text-center italic py-10">Sin datos.</p>';
        }

        // 3. Cambiar de página
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
                    b.classList.add('bg-slate-800', 'text-slate-500'); 
                });
                e.currentTarget.classList.add('bg-sky-500', 'text-white');
            }

            const subMenu = document.getElementById(`sub-filters`);
            if (subMenu) subMenu.style.display = (rankType === 'tiradores') ? 'flex' : 'none';
            
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
</body></html>
'''

REFEREE_HTML = '''
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="author" content="MartinezGalo & francoqdev">
    <meta name="copyright" content="ARG STATS">

    <title>Árbitro: {{ ref_name }} - ARG STATS</title>
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
    <div class="max-w-7xl mx-auto space-y-12">
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
            <div class="space-y-6">
                <h2 class="text-xl font-black uppercase italic tracking-tighter border-l-4 border-slate-500 pl-4">Partidos Dirigidos</h2>
                <div class="space-y-4 max-h-[700px] overflow-y-auto pr-2 custom-scroll">
                    {% for m in matches %}
                    <div class="bg-slate-900/50 p-4 rounded-2xl border border-slate-800">
                    <a href="/match/{{ m.id }}">
                        <div class="flex justify-between text-[9px] font-black text-slate-500 uppercase mb-3">
                            <span>{{ m.date[:10] }}</span>
                            <span>{{ m.tournament }}</span>
                        </div>
                        <div class="grid grid-cols-3 items-center gap-2 mb-3">
                            <span class="text-xs font-bold text-center truncate">{{ m.home_team }}</span>
                            <span class="bg-slate-800 py-1 rounded-lg font-mono font-black text-center text-xs">{{ m.score or 'VS' }}</span>
                            <span class="text-xs font-bold text-center truncate">{{ m.away_team }}</span>
                        </div>
                        <div class="grid grid-cols-2 gap-4 border-t border-slate-800 pt-3">
                            <div class="text-center">
                                <p class="text-[8px] font-black text-slate-500 uppercase">Local</p>
                                <p class="text-[11px] font-bold"><span class="text-yellow-500">{{ m.stats.h_cards }}T</span> | <span class="text-sky-400">{{ m.stats.h_fouls }}F</span></p>
                            </div>
                            <div class="text-center">
                                <p class="text-[8px] font-black text-slate-500 uppercase">Visita</p>
                                <p class="text-[11px] font-bold"><span class="text-yellow-500">{{ m.stats.v_cards }}T</span> | <span class="text-sky-400">{{ m.stats.v_fouls }}F</span></p>
                            </div>
                        </div>
                    </a>
                    </div>
                    {% endfor %}
                </div>
            </div>

            <div class="lg:col-span-2 space-y-8">
                <h2 class="text-xl font-black uppercase italic tracking-tighter border-l-4 border-red-500 pl-4">Análisis de Tendencias por Equipo</h2>
                
                <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <div class="bg-slate-800/30 p-6 rounded-[2rem] border border-slate-700/50">
                        <h3 class="text-sm font-black text-yellow-500 uppercase mb-4 tracking-widest">Equipos con más Tarjetas</h3>
                        <div class="space-y-2">
                            {% for t in top_targets.cards %}
                            <div class="flex justify-between items-center bg-slate-900/40 p-3 rounded-xl border border-slate-800">
                                <span class="font-bold text-sm">{{ t.name }}</span>
                                <span class="text-xs font-black"><span class="text-yellow-500 text-lg">{{ t.total }}</span> T en {{ t.pj }} PJ</span>
                            </div>
                            {% endfor %}
                        </div>
                    </div>

                    <div class="bg-slate-800/30 p-6 rounded-[2rem] border border-slate-700/50">
                        <h3 class="text-sm font-black text-red-500 uppercase mb-4 tracking-widest">Más Faltas Cometidas (En contra)</h3>
                        <div class="space-y-2">
                            {% for t in top_targets.fouls_committed %}
                            <div class="flex justify-between items-center bg-slate-900/40 p-3 rounded-xl border border-slate-800">
                                <span class="font-bold text-sm">{{ t.name }}</span>
                                <span class="text-xs font-black"><span class="text-red-500 text-lg">{{ t.total }}</span> F en {{ t.pj }} PJ</span>
                            </div>
                            {% endfor %}
                        </div>
                    </div>

                    <div class="bg-slate-800/30 p-6 rounded-[2rem] border border-slate-700/50">
                        <h3 class="text-sm font-black text-emerald-500 uppercase mb-4 tracking-widest">Más Faltas Recibidas (A favor)</h3>
                        <div class="space-y-2">
                            {% for t in top_targets.fouls_received %}
                            <div class="flex justify-between items-center bg-slate-900/40 p-3 rounded-xl border border-slate-800">
                                <span class="font-bold text-sm">{{ t.name }}</span>
                                <span class="text-xs font-black"><span class="text-emerald-500 text-lg">{{ t.total }}</span> F en {{ t.pj }} PJ</span>
                            </div>
                            {% endfor %}
                        </div>
                    </div>

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
    '''+FOOTER_HTML+'''
</body>
</html>
'''

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
        .player-dot { position: absolute; width: 34px; height: 34px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 900; transform: translate(-50%, -50%); border: 2px solid white; cursor: grab; z-index: 10; transition: transform 0.2s, border-color 0.2s, box-shadow 0.2s; user-select: none; }
        .player-dot:active { cursor: grabbing; z-index: 100 !important; transition: none !important; }
        .key-player { border-color: #fbbf24 !important; box-shadow: 0 0 15px #fbbf24 !important; }
        .selected-player { border-color: #38bdf8 !important; box-shadow: 0 0 15px #38bdf8 !important; z-index: 50; }
        .highlight-player { border-color: #f8fafc !important; box-shadow: 0 0 25px #f8fafc !important; transform: translate(-50%, -50%) scale(1.3) !important; z-index: 100; }
        .active-hover { border-color: #38bdf8 !important; background: rgba(56, 189, 248, 0.1) !important; }
        .card-badge { position: absolute; top: -5px; right: -5px; width: 12px; height: 16px; border-radius: 2px; border: 1px solid rgba(0,0,0,0.3); }
        .card-Yellow { background-color: #fbbf24; } .card-Red { background-color: #ef4444; } .card-YellowRed { background: linear-gradient(135deg, #fbbf24 50%, #ef4444 50%); }
        .player-name { position: absolute; top: 38px; left: 50%; transform: translateX(-50%); white-space: nowrap; background: rgba(15, 23, 42, 0.95); padding: 2px 6px; border-radius: 6px; font-size: 10px; pointer-events: none; border: 1px solid #334155; }
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
        /* Asegura que el contenido interno de la modal respete el límite de 750px */
        #modal-content {
            height: 100%;
            display: flex;
            flex-direction: column;
            overflow: hidden; /* Evita que la modal entera scrollee */
        }

        /* Forzamos que las notas no se muevan nunca */
        .notes-section {
            height: 160px; /* Altura fija para notas */
            flex-shrink: 0; /* Prohíbe que se encoja */
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

        .shooter-card { background: rgba(15, 23, 42, 0.6); padding: 0.25rem 0.5rem; border-radius: 0.5rem; border: 1px solid #1e293b; transition: all 0.2s; cursor: pointer; }
        .sub-menu { display: none; margin-top: 8px; animation: slideDown 0.2s ease-out; }
        #home-ranking-list, #away-ranking-list { height: 350px; min-height: 350px; overflow: hidden; display: flex; flex-direction: column; }
    </style>
</head>
<body class="p-6">
    <div id="modal-overlay" onclick="if(event.target==this) closeModal()"><div id="player-modal"><div id="modal-content"></div></div></div>
    
    <div id="subst-modal-overlay" class="hidden fixed inset-0 bg-black/80 backdrop-blur-sm z-[4000] flex items-center justify-center" onclick="if(event.target==this) closeSubstModal()">
        <div class="bg-slate-900 border border-slate-700 w-full max-w-md p-8 rounded-[2rem] shadow-2xl">
            <h3 class="text-xl font-black uppercase text-white mb-4 italic tracking-tighter">Sustitución Táctica</h3>
            <input type="text" id="subst-search" autocomplete="off" oninput="searchPlayers(this.value)" placeholder="Ingresa nombre o ID..." class="w-full bg-slate-950 border border-slate-800 p-4 rounded-2xl outline-none focus:border-sky-500 text-white text-sm mb-4">
            <div id="subst-results" class="space-y-2 max-h-60 overflow-y-auto"></div>
        </div>
    </div>

    <div id="selection-box"></div>
    <div id="context-menu">
        <div class="context-header" id="ctx-player-name">Jugador</div>
        <div class="context-item" onclick="handleCtxAction('profile')">📊 Ver Perfil</div>
        <div class="context-item" onclick="handleCtxAction('replace')">🔄 Reemplazar Jugador</div>
        <div class="context-item" onclick="handleCtxAction('key')" id="ctx-key-label">⭐ Marcar como Clave</div>
    </div>

    <div class="max-w-[1600px] space-y-8 mx-auto">
        <header class="flex justify-between items-center"><a href="/" class="bg-slate-800 px-6 py-2 rounded-xl font-bold border border-slate-700 hover:bg-slate-700 transition flex items-center gap-2"><span>←</span> INICIO</a><div class="text-right"><h2 class="text-sky-500 font-black italic uppercase text-sm tracking-widest">{{ match.tournament or 'LIGA PROFESIONAL' }}</h2><p class="text-slate-500 text-[10px] font-bold uppercase tracking-tighter">{{ match.date }}</p></div></header>
        
        <div class="flex h-auto ">
            <div class="bg-slate-950/80 w-[70%] max-w-6xl p-8 rounded-[3rem] border border-slate-700/50  shadow-inner  mx-auto text-center">
                <div class="flex justify-around items-center gap-8 mb-4 text-center">
                    <h1 class="text-3xl font-black uppercase flex-1 tracking-tighter hover:text-sky-500 transition-colors">
                        <a href="/team/{{ match.id_home_team }}">{{ match.home_team }}</a>
                    </h1>                    
                    <div class="px-8 py-3 bg-slate-900 rounded-3xl border-2 border-slate-800 text-4xl font-mono font-black text-white shadow-2xl">{{ match.score or 'VS' }}</div>
                    <h1 class="text-3xl font-black uppercase flex-1 tracking-tighter hover:text-sky-500 transition-colors">
                        <a href="/team/{{ match.id_away_team }}">{{ match.away_team }}</a>
                    </h1>
                </div>
                <div class="border-t border-slate-800 pt-4 mt-2"><span class="text-[12px] font-bold text-slate-300 uppercase tracking-widest italic">Árbitro: {%if match.referee %} <a href="/referee/{{ match.referee }}" class="hover:text-sky-500">{{ match.referee}}</a> {% else %} Por designar {% endif %}</span></div>
            </div>
            <!-- NOTAS -->
            <div class="ml-4 mx-auto w-[35%]">
                <form action="/save_match_note/{{ match.id }}" method="POST" class="bg-slate-900/40 p-6 rounded-[2.5rem] border border-slate-800/50 backdrop-blur-sm">
                    <div class="flex justify-between items-center mb-3 px-2">
                        <label class="text-[10px] font-black text-sky-500 uppercase tracking-[0.2em]">Notas Tácticas del Encuentro</label>
                        <button type="submit" class="text-[9px] bg-sky-600/20 hover:bg-sky-600 text-sky-400 hover:text-white px-4 py-1 rounded-full font-black uppercase transition-all border border-sky-500/30">Actualizar Nota</button>
                    </div>
                    <textarea name="notes" placeholder="Escribe aquí el análisis post-partido o instrucciones previas..." 
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
                        <div class="bg-slate-900/50 p-1.5 rounded-lg text-[12px] cursor-pointer hover:bg-slate-800 transition-all list-item-hover-only" data-pid="{{ p.player_id }}" onmouseenter="highlightTarget('{{ p.player_id }}', true)" onmouseleave="highlightTarget('{{ p.player_id }}', false)" onclick="handlePlayerClick(event, '{{p.player_id}}')">
                            <div class="flex justify-between items-center gap-1 w-full">
                                <span class="font-bold truncate flex-1 text-[14px] text-slate-200">{{ p.player_name.split(' ').pop() }} <span class="text-slate-500 font-medium text-[11px]">({{ p.position }})</span></span>
                                <span class="{% if p.minutes_played > 0 %}text-emerald-500{% else %}text-slate-700{% endif %} font-black text-[12px] whitespace-nowrap">{{ p.minutes_played }}'</span>
                            </div>
                        </div>
                        {% endfor %}
                    </div>
                </div>
                <!-- RANKING LOCAL -->
                <div class="space-y-3">
                    <div class="flex flex-col items-center border-b border-sky-400/20 pb-2 mb-3">
                        <div class="flex justify-between items-center w-full mb-2">
                            <h4 class="text-[14px] font-black text-sky-400 uppercase italic tracking-widest">Rankings Local</h4>
                            <button onclick="toggleL5('home', '{{ match.id_home_team }}')" id="h-l5-btn" class="text-[9px] px-2 py-0.5 rounded-full border border-slate-700 font-black uppercase text-slate-500 hover:text-white transition-all">Últimos 5</button>
                        </div>
                        <div class="flex flex-wrap justify-center gap-1 mb-2">
                            <button onclick="updateTeamRanking('home', '{{ match.id_home_team }}', 'tiradores', 'all', event)" class="text-[11px] px-1.5 py-0.5 rounded bg-sky-500 text-white font-bold h-rank-btn" id="h-btn-main">Tiros</button>
                            <button onclick="updateTeamRanking('home', '{{ match.id_home_team }}', 'headers', 'all', event)" class="text-[11px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-bold h-rank-btn">Cabeza</button>
                            <button onclick="updateTeamRanking('home', '{{ match.id_home_team }}', 'yellows', 'all', event)" class="text-[11px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-bold h-rank-btn">Tarj.</button>
                            <button onclick="updateTeamRanking('home', '{{ match.id_home_team }}', 'fouls', 'all', event)" class="text-[11px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-bold h-rank-btn">Faltas</button>
                            <button onclick="updateTeamRanking('home', '{{ match.id_home_team }}', 'fouls_rec', 'all', event)" class="text-[11px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-bold h-rank-btn">Recib.</button>
                        </div>
                        <div id="home-sub-filters" class="sub-menu flex gap-1 justify-center mt-2" style="display:none;">
                            <button onclick="updateTeamRanking('home', '{{ match.id_home_team }}', 'tiradores', 'all', event)" id="home-sub-all" class="text-[10px] px-1.5 py-0.5 rounded bg-sky-500 text-white font-black h-sub-btn">Todos</button>
                            <button onclick="updateTeamRanking('home', '{{ match.id_home_team }}', 'tiradores', 'target', event)" id="home-sub-target" class="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-500 font-black h-sub-btn">Arco</button>
                            <button onclick="updateTeamRanking('home', '{{ match.id_home_team }}', 'tiradores', 'long', event)" id="home-sub-long" class="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-500 font-black h-sub-btn">Lejos</button>
                        </div>
                    </div>
                    <div id="home-ranking-list" class="space-y-1"></div>
                    <div class="flex justify-center gap-4 mt-2">
                        <button onclick="changePage('home', -1)" class="text-sky-400 hover:text-white transition-colors"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="m15 18-6-6 6-6"/></svg></button>
                        <span id="home-page-info" class="text-[10px] font-black text-slate-500 uppercase mt-0.5">1 / 1</span>
                        <button onclick="changePage('home', 1)" class="text-sky-400 hover:text-white transition-colors"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="m9 18 6-6-6-6"/></svg></button>
                    </div>
                </div>
            </div>
            <!-- PITCH -->
            <div class="md:col-span-2 relative flex flex-col items-center">
                <div class="pitch" id="soccer-pitch">
                    {% for p in home_lineup %}<div class="player-dot bg-blue-500 draggable shadow-lg" style="bottom:{{ (p.role_x * 50) }}%; left:{{(1-p.role_y)*100}}%;" data-pid="{{p.player_id}}" data-pname="{{p.player_name}}" data-side="home" data-teamid="{{match.id_home_team}}" onclick="handlePlayerClick(event)">{{p.position}}{% if p.card %}<div class="card-badge card-{{p.card}}"></div>{% endif %}<div class="player-name">{{p.player_name.split(' ').pop()}}</div></div>{% endfor %}
                    {% for p in away_lineup %}<div class="player-dot bg-red-500 draggable shadow-lg" style="top:{{ (p.role_x * 50) }}%; left:{{p.role_y*100}}%;" data-pid="{{p.player_id}}" data-pname="{{p.player_name}}" data-side="away" data-teamid="{{match.id_away_team}}" onclick="handlePlayerClick(event)">{{p.position}}{% if p.card %}<div class="card-badge card-{{p.card}}"></div>{% endif %}<div class="player-name">{{p.player_name.split(' ').pop()}}</div></div>{% endfor %}
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
                        <div class="bg-slate-900/50 p-1.5 rounded-lg text-[12px] cursor-pointer hover:bg-slate-800 transition-all list-item-hover-only" data-pid="{{ p.player_id }}" onmouseenter="highlightTarget('{{ p.player_id }}', true)" onmouseleave="highlightTarget('{{ p.player_id }}', false)" onclick="handlePlayerClick(event, '{{p.player_id}}')">
                            <div class="flex justify-between items-center gap-1 w-full flex-row-reverse">
                                <span class="font-bold truncate flex-1 text-[14px] text-slate-200">{{ p.player_name.split(' ').pop() }} <span class="text-slate-500 font-medium text-[11px]">({{ p.position }})</span></span>
                                <span class="{% if p.minutes_played > 0 %}text-emerald-500{% else %}text-slate-700{% endif %} font-black text-[12px] whitespace-nowrap">{{ p.minutes_played }}'</span>
                            </div>
                        </div>
                        {% endfor %}
                    </div>
                </div>
                <!-- RANKING VISITA -->
                <div class="space-y-3">
                    <div class="flex flex-col items-center border-b border-red-500/20 pb-2 mb-3">
                        <div class="flex justify-between items-center w-full mb-2 flex-row-reverse">
                            <h4 class="text-[14px] font-black text-red-500 uppercase italic tracking-widest">Rankings Visita</h4>
                            <button onclick="toggleL5('away', '{{ match.id_away_team }}')" id="v-l5-btn" class="text-[9px] px-2 py-0.5 rounded-full border border-slate-700 font-black uppercase text-slate-500 hover:text-white transition-all">Últimos 5</button>
                        </div>
                        <div class="flex flex-wrap justify-center gap-1 mb-2">
                            <button onclick="updateTeamRanking('away', '{{ match.id_away_team }}', 'tiradores', 'all', event)" class="text-[11px] px-1.5 py-0.5 rounded bg-sky-500 text-white font-bold v-rank-btn">Tiros</button>
                            <button onclick="updateTeamRanking('away', '{{ match.id_away_team }}', 'headers', 'all', event)" class="text-[11px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-bold v-rank-btn">Cabeza</button>
                            <button onclick="updateTeamRanking('away', '{{ match.id_away_team }}', 'yellows', 'all', event)" class="text-[11px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-bold v-rank-btn">Tarj.</button>
                            <button onclick="updateTeamRanking('away', '{{ match.id_away_team }}', 'fouls', 'all', event)" class="text-[11px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-bold v-rank-btn">Faltas</button>
                            <button onclick="updateTeamRanking('away', '{{ match.id_away_team }}', 'fouls_rec', 'all', event)" class="text-[11px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-bold v-rank-btn">Recib.</button>
                        </div>
                        <div id="away-sub-filters" class="sub-menu flex gap-1 justify-center mt-2">
                            <button onclick="updateTeamRanking('away', '{{ match.id_away_team }}', 'tiradores', 'all', event)" id="away-sub-all" class="text-[10px] px-1.5 py-0.5 rounded bg-sky-500 text-white font-black v-sub-btn">Todos</button>
                            <button onclick="updateTeamRanking('away', '{{ match.id_away_team }}', 'tiradores', 'target', event)" id="away-sub-target" class="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-500 font-black v-sub-btn">Arco</button>
                            <button onclick="updateTeamRanking('away', '{{ match.id_away_team }}', 'tiradores', 'long', event)" id="away-sub-long" class="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-500 font-black v-sub-btn">Lejos</button>
                        </div>
                    </div>
                    <div id="away-ranking-list" class="space-y-1"></div>
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
                            <span class="text-[11px] text-sky-500 font-black uppercase italic" id="rank-{{cat}}-refrank">Árbitro <span class="{{ get_pos_color(data.ref_rank) }}">#{{data.ref_rank}}</span> en {{'Tarjetas' if cat=='cards' else 'Faltas'}}</span>
                        </div>
                        {% endif %}
                    </div>
                </div>
            </div>
            {% endfor %}
        </div>
    </div>
    '''+FOOTER_HTML+'''

    <script>
        const pitch = document.getElementById('soccer-pitch');
        const draggables = document.querySelectorAll('.draggable');
        const contextMenu = document.getElementById('context-menu');
        const selectionBox = document.getElementById('selection-box');
        let activePlayer = null, lastCtxPid = null, selectedPlayers = [], currentPlayerShots = [];
        let isLassoing = false, startX, startY, pitchIsReversed = false;
        const rankingsData = { home: [], away: [] }, currentPages = { home: 1, away: 1 }, perPage = 10;
        const locks = { home: false, away: false };
        const l5_active = { home: false, away: false };

        function getScoreColorClass(v) { if (v <= 30) return 'text-red-500'; if (v <= 70) return 'text-blue-500'; return 'text-green-500'; }
        function getPosColorClass(v) { if (v > 20) return 'text-red-500'; if (v > 10) return 'text-blue-500'; return 'text-green-500'; }

        function highlightTarget(pid, active) {
            document.querySelectorAll(`[data-pid="${pid}"]`).forEach(el => {
                if (el.classList.contains('player-dot')) active ? el.classList.add('highlight-player') : el.classList.remove('highlight-player');
                else if (el.classList.contains('list-item-hover-only')) active ? el.classList.add('active-hover') : el.classList.remove('active-hover');
                else if (el.classList.contains('shooter-card')) active ? el.classList.add('active-hover') : el.classList.remove('active-hover');
            });
        }
        
        function toggleL5(side, teamId) {
            l5_active[side] = !l5_active[side];
            const btn = document.getElementById(`${side === 'home' ? 'h' : 'v'}-l5-btn`);
            btn.classList.toggle('bg-sky-500', l5_active[side]);
            btn.classList.toggle('text-white', l5_active[side]);
            btn.classList.toggle('text-slate-500', !l5_active[side]);
            
            // Re-cargar ranking actual con el nuevo filtro
            const activeMain = document.querySelector(`.${side === 'home' ? 'h' : 'v'}-rank-btn.bg-sky-500`);
            const type = activeMain ? activeMain.innerText.toLowerCase().replace('.', '') : 'tiradores';
            const finalType = type.includes('tiro') ? 'tiradores' : type.includes('cabeza') ? 'headers' : type.includes('tarj') ? 'yellows' : type.includes('faltas') ? 'fouls' : 'fouls_rec';
            updateTeamRanking(side, teamId, finalType, 'all', null);
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
                <div class="shooter-card ${r.is_transferred ? 'border-red-500/50' : ''}" data-pid="${r.player_id}" 
                     onmouseenter="highlightTarget('${r.player_id}', true)" 
                     onmouseleave="highlightTarget('${r.player_id}', false)"
                     onclick="handlePlayerClick(event, '${r.player_id}')">
                    <div class="flex justify-between items-center gap-2 ${side === 'away' ? 'flex-row-reverse' : ''}">
                        <span class="font-bold truncate text-[14px] ${r.is_transferred ? 'text-red-400' : 'text-slate-200'}">${r.name.split(' ').pop()} <span class="text-slate-500 text-[11px] italic">(${r.pos})</span></span>
                        <span class="text-[12px] font-bold italic whitespace-nowrap ${r.is_transferred ? 'text-red-400' : 'text-slate-400'}"><span class="${r.is_transferred ? 'text-red-400' : 'text-sky-400'} font-black">${r.val}</span> ${r.unit} / ${r.pj} PJ</span>
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
            if (rankType === 'tiradores') { subMenu.style.display = 'flex'; } else { subMenu.style.display = 'none'; }
            
            const limit = l5_active[side] ? 5 : null;
            fetch(`/api/team_ranking/${teamId}?type=${rankType}&filter=${shotFilter}&limit=${limit || ''}`).then(r => r.json()).then(data => { 
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

            content.innerHTML = `
                <div class="flex justify-between items-end border-b border-slate-700 pb-6 mb-8 shrink-0">
                    <div>
                        <h2 class="text-5xl font-black italic uppercase text-white leading-none">${d.name}</h2>
                        <p class="text-sky-400 font-bold uppercase tracking-widest mt-3 text-lg">
                            ${d.team} | ${d.pos} | <span class="text-white">#${d.number || 'S/N'}</span>
                        </p>
                    </div>
                    <button onclick="closeModal()" class="text-slate-500 hover:text-white transition-colors text-3xl p-2">✕</button>
                </div>

                <div class="grid grid-cols-12 gap-10 flex-1 min-h-0 overflow-hidden mb-2">
                    
                    <div class="col-span-4 bg-slate-900/50 rounded-[2.5rem] border border-slate-800 p-6 flex flex-col min-h-0 shadow-inner">
                        <div class="flex gap-2 mb-6 bg-slate-950 p-1.5 rounded-2xl border border-slate-800 shrink-0">
                            <button onclick="switchPlayerTab('gen')" id="tab-btn-gen" class="flex-1 py-2 text-[11px] font-black uppercase rounded-xl transition-all bg-sky-600 text-white">General</button>
                            <button onclick="switchPlayerTab('l5')" id="tab-btn-l5" class="flex-1 py-2 text-[11px] font-black uppercase rounded-xl transition-all text-slate-500">Ultimos 5</button>
                            <button onclick="switchPlayerTab('part')" id="tab-btn-part" class="flex-1 py-2 text-[11px] font-black uppercase rounded-xl transition-all text-slate-500">Partido</button>
                        </div>
                        <div id="player-stats-content" class="space-y-2 overflow-y-auto flex-1 custom-blue-scroll pr-2">
                            ${renderStatRows(d.stats.general)}
                        </div>
                    </div>

                    <div class="col-span-8 flex flex-col gap-6 min-h-0">
                        
                        <div class="bg-slate-900/50 rounded-[2.5rem] border border-slate-800 p-6 flex flex-col flex-1 min-h-0 shadow-inner overflow-hidden">
                            <div class="flex justify-between items-center mb-6 shrink-0">
                                <h4 class="text-[12px] font-black text-sky-500 uppercase tracking-[0.2em]">Rankings (Top 20)</h4>
                                <div class="flex bg-slate-950 p-1 rounded-xl border border-slate-800">
                                    <button onclick="renderRankScope('liga')" id="rank-scope-liga" class="px-4 py-1 text-[10px] font-black uppercase rounded-lg bg-sky-600 text-white transition-all">Liga</button>
                                    <button onclick="renderRankScope('equipo')" id="rank-scope-equipo" class="px-4 py-1 text-[10px] font-black uppercase rounded-lg text-slate-500 transition-all">Equipo</button>
                                    <button onclick="renderRankScope('posicion')" id="rank-scope-posicion" class="px-4 py-1 text-[10px] font-black uppercase rounded-lg text-slate-500 transition-all">Posición</button>
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
                container.innerHTML = `<div class="col-span-2 text-center py-10 text-slate-600 italic text-sm">El jugador no figura en el Top 20 de ninguna estadística en este ámbito.</div>`;
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
                <div class="flex justify-between items-center p-2 rounded-lg hover:bg-slate-800/50 transition-colors">
                    <span class="text-[11px] font-bold text-slate-400 uppercase">${l[0]}</span>
                    <span class="text-sm font-black text-white">${data[l[1]] || 0}</span>
                </div>
            `).join('');
        }

        function switchPlayerTab(tab) {
            const content = document.getElementById('player-stats-content');
            const mapping = { 'gen': 'general', 'l5': 'l5', 'part': 'partido' };
            content.innerHTML = renderStatRows(window.currentPlayerData[mapping[tab]]);
            
            // UI de botones
            ['gen', 'l5', 'part'].forEach(t => {
                const btn = document.getElementById(`tab-btn-${t}`);
                btn.classList.remove('bg-sky-600', 'text-white');
                btn.classList.add('text-slate-500');
            });
            const active = document.getElementById(`tab-btn-${tab}`);
            active.classList.add('bg-sky-600', 'text-white');
        }        
        function closeModal() { document.getElementById('modal-overlay').style.display = 'none'; }
        function handlePlayerClick(e) { 
            const p = e.currentTarget; 
            if (!p.dragging) openPlayer(p.dataset.pid); 
        }        
        let substituteTarget = null;
        function closeSubstModal() { document.getElementById('subst-modal-overlay').classList.add('hidden'); }
        
        function searchPlayers(q) {
            if(!q || q.length < 2) return;
            fetch(`/search_players/${substituteTarget.dataset.teamid}?q=${q}`).then(r => r.json()).then(data => {
                document.getElementById('subst-results').innerHTML = data.map(p => `<div onclick="applySubstitution('${p.player_id}', '${p.player_name}', '${p.position}')" class="bg-slate-800 p-3 rounded-xl border border-slate-700 hover:border-sky-500 cursor-pointer flex justify-between"><span class="font-bold text-white">${p.player_name}</span><span class="text-slate-500 font-black">${p.position}</span></div>`).join('');
            });
        }

        function applySubstitution(pid, name, pos) { 
            substituteTarget.dataset.pid = pid; 
            substituteTarget.dataset.pname = name; 
            // Actualiza el texto de la posición y el nombre visual
            substituteTarget.childNodes[0].nodeValue = pos; 
            substituteTarget.querySelector('.player-name').innerText = name.split(' ').pop(); 
            closeSubstModal(); 
        }        
        document.addEventListener('contextmenu', e => { const p = e.target.closest('.player-dot'); if(p) { e.preventDefault(); lastCtxPid = p.dataset.pid; substituteTarget = p; document.getElementById('ctx-player-name').innerText = p.dataset.pname; const keyLabel = document.getElementById('ctx-key-label'); keyLabel.innerText = p.classList.contains('key-player') ? '❌ Quitar Marca' : '⭐ Marcar como Clave'; contextMenu.style.display = 'block'; contextMenu.style.left = e.clientX + 'px'; contextMenu.style.top = e.clientY + 'px'; } else contextMenu.style.display = 'none'; });
        document.addEventListener('click', e => { if (!e.target.closest('#context-menu')) contextMenu.style.display = 'none'; });
        function handleCtxAction(act) { if(act === 'profile') openPlayer(lastCtxPid); else if(act === 'replace') document.getElementById('subst-modal-overlay').classList.remove('hidden'); else if(act === 'key') substituteTarget.classList.toggle('key-player'); contextMenu.style.display = 'none'; }
        window.onload = () => { updateTeamRanking('home', '{{ match.id_home_team }}', 'tiradores'); updateTeamRanking('away', '{{ match.id_away_team }}', 'tiradores'); };
        document.addEventListener('mousedown', e => {
            const p = e.target.closest('.draggable'), isPitch = e.target.closest('#soccer-pitch');
            if(!p && isPitch && !e.target.closest('#context-menu')) { selectedPlayers.forEach(x=>x.classList.remove('selected-player')); selectedPlayers=[]; isLassoing=true; startX=e.clientX; startY=e.clientY; selectionBox.style.display='none'; draggables.forEach(x=>x._rect=x.getBoundingClientRect()); }
            else if(p) {
                if ((p.dataset.side === 'home' && locks.home) || (p.dataset.side === 'away' && locks.away)) return;
                activePlayer=p; activePlayer.dragging = false;
                if(!selectedPlayers.includes(p)) { selectedPlayers.forEach(x=>x.classList.remove('selected-player')); p.classList.add('selected-player'); selectedPlayers=[p]; }
                selectedPlayers.forEach(x=>{ x.style.transition='none'; x.startL=parseFloat(x.style.left); x.startB=(x.style.bottom&&x.style.bottom!=='auto')?parseFloat(x.style.bottom):null; x.startT=x.startB===null?parseFloat(x.style.top):null; });
                activePlayer.mX=e.clientX; activePlayer.mY=e.clientY;
            }
        });
        document.addEventListener('mousemove', e => {
            if(isLassoing) { 
                let w=Math.abs(e.clientX-startX), h=Math.abs(e.clientY-startY), l=Math.min(e.clientX,startX), t=Math.min(e.clientY,startY); 
                if (w > 2 || h > 2) selectionBox.style.display = 'block';
                Object.assign(selectionBox.style, {width:w+'px', height:h+'px', left:l+'px', top:t+'px'});
                const br=selectionBox.getBoundingClientRect();
                draggables.forEach(x=>{
                    if ((x.dataset.side === 'home' && locks.home) || (x.dataset.side === 'away' && locks.away)) return;
                    const r=x._rect, overlap=!(br.right<r.left||br.left>r.right||br.bottom<r.top||br.top>r.bottom);
                    if(overlap) { if(!selectedPlayers.includes(x)) { x.classList.add('selected-player'); selectedPlayers.push(x); } }
                    else { x.classList.remove('selected-player'); selectedPlayers=selectedPlayers.filter(y=>y!==x); }
                });
            } else if(activePlayer) {
                if (Math.abs(e.clientX - activePlayer.mX) > 3 || Math.abs(e.clientY - activePlayer.mY) > 3) activePlayer.dragging=true;
                const r=pitch.getBoundingClientRect(), dx=((e.clientX-activePlayer.mX)/r.width)*100, dy=((e.clientY-activePlayer.mY)/r.height)*100;
                selectedPlayers.forEach(x=>{ x.style.left=Math.max(0,Math.min(100,x.startL+dx))+'%'; if(x.startB!==null) x.style.bottom=Math.max(0,Math.min(100,x.startB-dy))+'%'; else x.style.top=Math.max(0,Math.min(100,x.startT+dy))+'%'; });
            }
        });
        document.addEventListener('mouseup', () => { isLassoing=false; selectionBox.style.display='none'; if(activePlayer) selectedPlayers.forEach(p => p.style.transition = ''); activePlayer=null; });
    </script>
</body></html>
'''

if __name__ == '__main__':
    init_notes_table()
    
    is_render = os.environ.get("RENDER", False)
    
    if is_render:
        # Configuración para Render
        port = int(os.environ.get("PORT", 5000))
        app.run(host='0.0.0.0', port=port)
    else:
        # Configuración para LOCAL
        print("--- CORRIENDO EN MODO LOCAL ---")
        app.run(host='127.0.0.1', port=5001, debug=True)