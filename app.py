import sqlite3
import os
import json
import datetime
from collections import defaultdict
from flask import Flask, render_template, request, redirect, url_for, jsonify, send_from_directory
import pandas as pd

app = Flask(__name__)
DB_NAME = "ARGSTATS.db"

# Diccionario de reemplazo de nombres
TEAM_NAME_MAP = {
    "Argentinos Juniors": "Argentinos",
    "Argentinos Jrs.": "Argentinos",
    "Atletico Tucuman": "Atl. Tucuman",
    "Barracas Central": "Barracas",
    "Central Cordoba de Santiago": "Central Cordoba",
    "Club Atletico Platense": "Platense",
    "Defensa y Justicia": "Def. y Justicia",
    "Dep. Riestra": "Riestra",
    "Deportivo Riestra": "Riestra",
    "Independiente Rivadavia": "Ind. Rivadavia",
    "Newell's Old Boys": "Newell's",
    "Estudiantes de Rio Cuarto" : "Est. Rio Cuarto",
    "Racing Club": "Racing",
    "Unión de Santa Fe" : "Union",
    "Velez Sarsfield": "Velez",
    "Vélez Sarsfield": "Velez",
    "San Martin San Juan": "San Martin SJ",
}

RELEGATED_TEAMS = ['7772', '6074']  # IDs de equipos descendidos

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
    Asegura la creacion de indices en tablas clave para maximizar el rendimiento.
    """
    conn = get_db_connection()
    conn.execute('CREATE TABLE IF NOT EXISTS player_notes (player_id TEXT PRIMARY KEY, notes TEXT)')
    conn.execute('CREATE TABLE IF NOT EXISTS match_notes (match_id TEXT PRIMARY KEY, notes TEXT)')
    
    # Indexes creation for performance
    conn.execute('CREATE INDEX IF NOT EXISTS idx_goals_player ON goals (player_id, match_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_goals_assist ON goals (assist_id, match_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_cards_player ON cards (player_id, match_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_shots_player_target_box ON shots (player_id, match_id, on_target, inside_box)')

    # Views creation
    conn.execute('DROP VIEW IF EXISTS shots_on_target')
    conn.execute('DROP VIEW IF EXISTS shots_outside_box')
    conn.execute('DROP VIEW IF EXISTS headers')
    conn.execute('DROP VIEW IF EXISTS shots_goals')
    conn.execute('DROP VIEW IF EXISTS shots_corners')
    conn.execute('DROP VIEW IF EXISTS shots_received')

    conn.execute('CREATE VIEW IF NOT EXISTS shots_on_target AS SELECT * FROM shots WHERE on_target = 1')
    conn.execute('CREATE VIEW IF NOT EXISTS shots_outside_box AS SELECT * FROM shots WHERE inside_box = 0')
    conn.execute('CREATE VIEW IF NOT EXISTS headers AS SELECT * FROM shots WHERE LOWER(shot_type) = "head"')
    conn.execute('''
        CREATE VIEW IF NOT EXISTS shots_received AS 
        SELECT s.*, 
               CASE WHEN s.team_id = m.home_team_id THEN m.away_team_id ELSE m.home_team_id END as against_team_id
        FROM shots s
        JOIN matches m ON s.match_id = m.id
    ''')

    # try:
    #     conn.execute('ALTER TABLE matches ADD COLUMN finished INTEGER DEFAULT 0')
    # except:
    #     pass # La columna ya existe
    conn.commit()
    conn.close()

init_notes_table()

def get_match_context(conn):
    """Obtiene los mapeos de partidos finalizados para todos los equipos activos."""
    matches = conn.execute("""
        SELECT id, home_team_id, away_team_id, date
        FROM matches
        WHERE finished = 1
        ORDER BY date DESC
    """).fetchall()

    all_teams_query = conn.execute(
        "SELECT DISTINCT home_team_id as id FROM matches UNION SELECT DISTINCT away_team_id as id FROM matches"
    ).fetchall()
    teams = [str(r['id']) for r in all_teams_query if str(r['id']) not in RELEGATED_TEAMS]

    team_all = {t: [] for t in teams}
    team_home = {t: [] for t in teams}
    team_away = {t: [] for t in teams}
    match_teams = {}

    for m in matches:
        mid = str(m['id'])
        h_id = str(m['home_team_id'])
        a_id = str(m['away_team_id'])
        match_teams[mid] = (h_id, a_id)

        if h_id in team_all:
            team_all[h_id].append(mid)
            team_home[h_id].append(mid)
        if a_id in team_all:
            team_all[a_id].append(mid)
            team_away[a_id].append(mid)

    return teams, team_all, team_home, team_away, match_teams

def get_teams_stats(stat, last_matches=None, conn=None, context=None, cached_rows=None):
    """
    Calcula estadisticas a favor (made) y en contra (against) por equipo,
    desglosado en total, local (total_home) y visitante (total_away).
    Si last_matches esta especificado (5 o 10), filtra los partidos correspondientes.
    """
    close_conn = False
    if conn is None:
        conn = get_db_connection()
        close_conn = True

    if context is None:
        context = get_match_context(conn)

    teams, team_all, team_home, team_away, match_teams = context

    if last_matches is not None:
        target_all = {t: team_all[t][:last_matches] for t in teams}
        target_home = {t: team_home[t][:last_matches] for t in teams}
        target_away = {t: team_away[t][:last_matches] for t in teams}
    else:
        target_all = team_all
        target_home = team_home
        target_away = team_away

    if cached_rows is not None:
        rows = cached_rows
    else:
        if stat == 'shots':
            sql = "SELECT match_id, team_id, COUNT(*) as cnt FROM shots GROUP BY match_id, team_id"
        elif stat == 'shots_on_target':
            sql = "SELECT match_id, team_id, COUNT(*) as cnt FROM shots_on_target GROUP BY match_id, team_id"
        elif stat == 'shots_outside_box':
            sql = "SELECT match_id, team_id, COUNT(*) as cnt FROM shots_outside_box GROUP BY match_id, team_id"
        elif stat == 'headers':
            sql = "SELECT match_id, team_id, COUNT(*) as cnt FROM headers GROUP BY match_id, team_id"
        elif stat == 'goals':
            sql = "SELECT match_id, team_id, COUNT(*) as cnt FROM goals GROUP BY match_id, team_id"
        elif stat == 'cards':
            sql = "SELECT match_id, team_id, SUM(CASE WHEN LOWER(card_type) = 'red' THEN 2 ELSE 1 END) as cnt FROM cards GROUP BY match_id, team_id"
        elif stat == 'fouls':
            sql = "SELECT match_id, team_id, SUM(fouls_committed) as cnt FROM player_match_details GROUP BY match_id, team_id"
        elif stat == 'tackles':
            sql = "SELECT match_id, team_id, SUM(tackles) as cnt FROM player_match_details GROUP BY match_id, team_id"
        elif stat == 'offsides':
            sql = "SELECT match_id, team_id, SUM(offsides) as cnt FROM player_match_details GROUP BY match_id, team_id"
        # elif stat == 'corners':
        #     sql = "SELECT match_id, team_id, COUNT(*) as cnt FROM shots WHERE situation = 'corner' GROUP BY match_id, team_id"
        else:
            if close_conn:
                conn.close()
            raise ValueError(f"Unsupported stat: {stat}")

        rows = conn.execute(sql).fetchall()

    if close_conn:
        conn.close()

    event_map = {}
    for r in rows:
        event_map[(str(r['match_id']), str(r['team_id']))] = r['cnt']

    made_list = []
    against_list = []

    for t in teams:
        m_total = sum(event_map.get((mid, t), 0) for mid in target_all[t])
        m_home = sum(event_map.get((mid, t), 0) for mid in target_home[t])
        m_away = sum(event_map.get((mid, t), 0) for mid in target_away[t])

        ag_total = sum(event_map.get((mid, match_teams[mid][1] if match_teams[mid][0] == t else match_teams[mid][0]), 0) for mid in target_all[t])
        ag_home = sum(event_map.get((mid, match_teams[mid][1]), 0) for mid in target_home[t])
        ag_away = sum(event_map.get((mid, match_teams[mid][0]), 0) for mid in target_away[t])

        made_list.append({
            "rank_team": t,
            "total": m_total,
            "total_home": m_home,
            "total_away": m_away
        })

        against_list.append({
            "rank_team": t,
            "total": ag_total,
            "total_home": ag_home,
            "total_away": ag_away
        })

    return {
        "made": made_list,
        "against": against_list
    }

def format_team_stats_list(raw_list, venue='all', limit=None, context=None, teams_map=None):
    """Auxiliar para formatear y calcular PJ, total y promedio de una lista de estadisticas de equipos."""
    teams, team_all, team_home, team_away, match_teams = context
    out = []
    for item in raw_list:
        tid = item['rank_team']
        if venue == 'home':
            mids = team_home.get(tid, [])[:limit] if limit else team_home.get(tid, [])
            tot = item.get('total_home', 0)
        elif venue == 'away':
            mids = team_away.get(tid, [])[:limit] if limit else team_away.get(tid, [])
            tot = item.get('total_away', 0)
        else:
            mids = team_all.get(tid, [])[:limit] if limit else team_all.get(tid, [])
            tot = item.get('total', 0)

        pj = len(mids)
        avg = round(tot / pj, 2) if pj > 0 else 0.0

        out.append({
            'id': tid,
            'name': teams_map.get(tid, tid) if teams_map else tid,
            'total': tot,
            'pj': pj,
            'avg': avg
        })
    return out

def get_teams_rankings(stats_list, last_matches=None):
    """
    Obtiene las estadisticas de equipos para una lista de metricas.
    Llama a get_teams_stats con last_matches = None, 5 y 10, y junta los resultados en una tabla.
    """
    if stats_list is None:
        raise ValueError("stats_list cannot be None")

    conn = get_db_connection()
    context = get_match_context(conn)

    sql_map = {
        'shots': "SELECT match_id, team_id, COUNT(*) as cnt FROM shots GROUP BY match_id, team_id",
        'shots_on_target': "SELECT match_id, team_id, COUNT(*) as cnt FROM shots_on_target GROUP BY match_id, team_id",
        'shots_outside_box': "SELECT match_id, team_id, COUNT(*) as cnt FROM shots_outside_box GROUP BY match_id, team_id",
        'headers': "SELECT match_id, team_id, COUNT(*) as cnt FROM headers GROUP BY match_id, team_id",
        'goals': "SELECT match_id, team_id, COUNT(*) as cnt FROM goals GROUP BY match_id, team_id",
        'cards': "SELECT match_id, team_id, SUM(CASE WHEN LOWER(card_type) = 'red' THEN 2 ELSE 1 END) as cnt FROM cards GROUP BY match_id, team_id",
        'fouls': "SELECT match_id, team_id, SUM(fouls_committed) as cnt FROM player_match_details GROUP BY match_id, team_id",
        'tackles': "SELECT match_id, team_id, SUM(tackles) as cnt FROM player_match_details GROUP BY match_id, team_id",
        'offsides': "SELECT match_id, team_id, SUM(offsides) as cnt FROM player_match_details GROUP BY match_id, team_id"
    }

    rankings = {}
    for stat in stats_list:
        cached_rows = conn.execute(sql_map[stat]).fetchall() if stat in sql_map else None
        res_all = get_teams_stats(stat, last_matches=None, conn=conn, context=context, cached_rows=cached_rows)
        res_5 = get_teams_stats(stat, last_matches=5, conn=conn, context=context, cached_rows=cached_rows)
        res_10 = get_teams_stats(stat, last_matches=10, conn=conn, context=context, cached_rows=cached_rows)

        made_by_team = {}
        for item in res_all["made"]:
            t = item["rank_team"]
            made_by_team[t] = {
                "rank_team": t,
                "total": item["total"],
                "total_home": item["total_home"],
                "total_away": item["total_away"],
                "last5_total": 0, "last5_home": 0, "last5_away": 0,
                "last10_total": 0, "last10_home": 0, "last10_away": 0,
            }
        for item in res_5["made"]:
            t = item["rank_team"]
            if t in made_by_team:
                made_by_team[t]["last5_total"] = item["total"]
                made_by_team[t]["last5_home"] = item["total_home"]
                made_by_team[t]["last5_away"] = item["total_away"]
        for item in res_10["made"]:
            t = item["rank_team"]
            if t in made_by_team:
                made_by_team[t]["last10_total"] = item["total"]
                made_by_team[t]["last10_home"] = item["total_home"]
                made_by_team[t]["last10_away"] = item["total_away"]

        against_by_team = {}
        for item in res_all["against"]:
            t = item["rank_team"]
            against_by_team[t] = {
                "rank_team": t,
                "total": item["total"],
                "total_home": item["total_home"],
                "total_away": item["total_away"],
                "last5_total": 0, "last5_home": 0, "last5_away": 0,
                "last10_total": 0, "last10_home": 0, "last10_away": 0,
            }
        for item in res_5["against"]:
            t = item["rank_team"]
            if t in against_by_team:
                against_by_team[t]["last5_total"] = item["total"]
                against_by_team[t]["last5_home"] = item["total_home"]
                against_by_team[t]["last5_away"] = item["total_away"]
        for item in res_10["against"]:
            t = item["rank_team"]
            if t in against_by_team:
                against_by_team[t]["last10_total"] = item["total"]
                against_by_team[t]["last10_home"] = item["total_home"]
                against_by_team[t]["last10_away"] = item["total_away"]

        rankings[stat] = {
            "all": res_all,
            "last5": res_5,
            "last10": res_10,
            "made_by_team": made_by_team,
            "against_by_team": against_by_team,
            "made": list(made_by_team.values()),
            "against": list(against_by_team.values())
        }

    conn.close()
    return rankings

def get_team_global_positions(team_id, conn=None, context=None):
    """
    Calcula la posicion global (ranking #) del equipo en la liga en cada metrica
    tanto en estadisticas a favor (made) como en contra (against).
    Utiliza get_teams_stats para garantizar consistencia y rendimiento.
    """
    close_conn = False
    if conn is None:
        conn = get_db_connection()
        close_conn = True

    if context is None:
        context = get_match_context(conn)

    teams, team_all, team_home, team_away, match_teams = context
    pj_team = len(team_all.get(str(team_id), []))

    stat_configs = [
        ('shots', 'Tiros Realizados', 'Tiros Recibidos'),
        ('headers', 'Cabezazos Realizados', 'Cabezazos Recibidos'),
        ('fouls', 'Faltas Cometidas', 'Faltas Recibidas'),
        ('cards', 'Tarjetas Recibidas', 'Tarjetas Provocadas'),
        # ('corners', 'Corners a Favor', 'Corners en Contra'),
        ('tackles', 'Entradas Realizadas', 'Entradas Recibidas'),
        ('offsides', 'Offsides Cometidos', 'Offsides Provocados')
    ]

    global_ranks = []
    for stat_key, label_made, label_against in stat_configs:
        res = get_teams_stats(stat_key, conn=conn, context=context)

        def extract_side_info(side, label):
            items = res.get(side, [])
            sorted_items = sorted(items, key=lambda x: x['total'], reverse=True)
            for idx, item in enumerate(sorted_items, 1):
                if str(item['rank_team']) == str(team_id):
                    return {
                        'label': label,
                        'pos': idx,
                        'total': item['total'],
                        'pj': pj_team
                    }
            return {'label': label, 'pos': 'N/A', 'total': 0, 'pj': pj_team}

        global_ranks.append({
            'made': extract_side_info('made', label_made),
            'against': extract_side_info('against', label_against)
        })

    if close_conn:
        conn.close()

    return global_ranks

def get_team_rankings_logic(team_id, rank_type='tiradores', filter_type='all', limit=None, match_id=None, order_by='avg', context_match_id=None):
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
        match_rows = conn.execute("SELECT id FROM matches WHERE (home_team_id = ? OR away_team_id = ?) AND finished = 1 ORDER BY date DESC LIMIT ?", (str(team_id), str(team_id), limit)).fetchall()
        if match_rows:
            ids_str = ",".join([f"'{mid}'" for mid in [r[0] for r in match_rows]])
            match_filter = f"AND pmd.match_id IN ({ids_str})"
            include_history = True
        else: return []

    join_sql, val_sql, where_sql = _get_stat_sql_config(rank_type, filter_type)
    
    if where_sql:
        join_sql += f" {where_sql}"
        where_sql = ""

    u_map = {
        "tiradores": "tiros", 
        "shots": "tiros", 
        "shots_on_target": "al arco",
        "target": "al arco",
        "shots_outside_box": "de lejos",
        "long": "de lejos",
        "goals": "goles", 
        "headers": "cabezazos", 
        "yellows": "tarjetas", 
        "cards": "tarjetas", 
        "fouls": "faltas", 
        "fouls_rec": "faltas rec.", 
        "fouls_received": "recibidas", 
        "assists": "asistencias",
        "tackles": "entradas",
        "offsides": "offsides",
    }

    if include_history:
        # Granular Query: Group by Player AND Match
        query = f'''
            SELECT pmd.player_id, pmd.match_id, COALESCE(pmd.short_name, pmd.name) as player_name, pmd.position, {val_sql} as val, 
            pmd.minutes_played, pmd.is_starter, {lt_sub} as ct,
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
            is_sub = bool(r["is_starter"] == 0)
            
            p["val"] += val
            p["minutes"] += mins
            p["pj"] += 1
            p["history"][r["match_id"]] = {
                "val": val,
                "mins": mins,
                "is_sub": is_sub
            }
        
        # Filter out players with 0 minutes and calculate avg
        output = []
        for p in players_map.values():
            p["avg"] = round((p["val"] / p["minutes"]) * 90, 2) if p["minutes"] > 0 else 0.0
            output.append(p)

    else:
        # Standard Aggregate Query (Season)
        match_filter_sub = match_filter.replace('pmd.', 'pmd2.')
        minutes_sub = f"(SELECT SUM(pmd2.minutes_played) FROM player_match_details pmd2 WHERE pmd2.player_id = pmd.player_id AND pmd2.team_id = ? AND pmd2.minutes_played > 0 {match_filter_sub})"

        query = f'''
            SELECT pmd.player_id, COALESCE(pmd.short_name, pmd.name) as player_name, pmd.position, {val_sql} as val, COUNT(DISTINCT pmd.match_id) as pj, {lt_sub} as ct,
            (SELECT shirt_number FROM player_match_details pmd3 JOIN matches m3 ON pmd3.match_id = m3.id WHERE pmd3.player_id = pmd.player_id and minutes_played  ORDER BY m3.date DESC LIMIT 1) as shirt_number,
            {minutes_sub} as minutes_played,
            {unavail_sub} as unavail_reason
            FROM player_match_details pmd 
            {join_sql} 
            WHERE pmd.team_id = ? AND pmd.minutes_played > 0 {match_filter} {where_sql}
            GROUP BY pmd.player_id HAVING minutes_played > 0 ORDER BY val DESC
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

def get_team_players_full_matrix(team_id, context_match_id=None):
    """
    Obtiene la matriz completa de partidos y estadísticas por jugador para un equipo utilizando pandas.
    Devuelve un diccionario estructurado por jugador.
    """
    conn = get_db_connection()
    
    unavail_sub = "NULL"
    params = [str(team_id)]
    if context_match_id:
        unavail_sub = "(SELECT unavailability_reason FROM player_match_details WHERE player_id = pmd.player_id AND match_id = ? AND unavailable = 1 LIMIT 1)"
        params.insert(0, str(context_match_id))
    
    sql = f"""
        SELECT 
            pmd.player_id, 
            pmd.match_id, 
            COALESCE(pmd.short_name, pmd.name) as player_name, 
            pmd.position, 
            pmd.minutes_played, 
            pmd.is_starter,
            COALESCE(pmd.fouls_committed, 0) as fouls,
            COALESCE(pmd.fouls_received, 0) as fouls_rec,
            COALESCE(pmd.offsides, 0) as offsides,
            COALESCE(pmd.tackles, 0) as tackles,
            (SELECT COUNT(*) FROM shots s WHERE s.player_id = pmd.player_id AND s.match_id = pmd.match_id) as tiradores,
            (SELECT COUNT(*) FROM shots_on_target s WHERE s.player_id = pmd.player_id AND s.match_id = pmd.match_id) as shots_on_target,
            (SELECT COUNT(*) FROM shots_outside_box s WHERE s.player_id = pmd.player_id AND s.match_id = pmd.match_id) as shots_outside_box,
            (SELECT COUNT(*) FROM headers s WHERE s.player_id = pmd.player_id AND s.match_id = pmd.match_id) as headers,
            (SELECT COALESCE(SUM(CASE WHEN LOWER(c.card_type) = 'red' THEN 2 WHEN c.card_id IS NOT NULL THEN 1 ELSE 0 END), 0) 
             FROM cards c WHERE c.player_id = pmd.player_id AND c.match_id = pmd.match_id) as yellows,
            (SELECT COUNT(*) FROM goals g WHERE g.player_id = pmd.player_id AND g.match_id = pmd.match_id) as goals,
            (SELECT COUNT(*) FROM goals g WHERE g.assist_id = pmd.player_id AND g.match_id = pmd.match_id) as assists,
            (SELECT pmd_lt.team_id FROM player_match_details pmd_lt JOIN matches m_lt ON pmd_lt.match_id = m_lt.id WHERE pmd_lt.player_id = pmd.player_id ORDER BY m_lt.date DESC LIMIT 1) as current_team,
            (SELECT shirt_number FROM player_match_details pmd3 JOIN matches m3 ON pmd3.match_id = m3.id WHERE pmd3.player_id = pmd.player_id AND pmd3.minutes_played > 0 ORDER BY m3.date DESC LIMIT 1) as shirt_number,
            {unavail_sub} as unavail_reason
        FROM player_match_details pmd
        WHERE pmd.team_id = ? AND pmd.minutes_played > 0
    """
    
    df = pd.read_sql_query(sql, conn, params=tuple(params))
    conn.close()
    
    if df.empty:
        return {}
    
    metric_cols = ['minutes_played', 'is_starter', 'fouls', 'fouls_rec', 'offsides', 'tackles', 
                   'tiradores', 'shots_on_target', 'shots_outside_box', 'headers', 'yellows', 'goals', 'assists']
    for col in metric_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype(int)
        
    df['player_id'] = df['player_id'].astype(str)
    df['match_id'] = df['match_id'].astype(str)
    
    players_dict = {}
    
    grouped = df.groupby('player_id')
    for pid, group in grouped:
        first_row = group.iloc[0]
        matches_dict = {}
        
        for _, row in group.iterrows():
            matches_dict[row['match_id']] = {
                'mins': int(row['minutes_played']),
                'is_sub': bool(row['is_starter'] == 0),
                'tiradores': int(row['tiradores']),
                'shots': int(row['tiradores']),
                'shots_on_target': int(row['shots_on_target']),
                'target': int(row['shots_on_target']),
                'shots_outside_box': int(row['shots_outside_box']),
                'long': int(row['shots_outside_box']),
                'headers': int(row['headers']),
                'yellows': int(row['yellows']),
                'cards': int(row['yellows']),
                'fouls': int(row['fouls']),
                'fouls_rec': int(row['fouls_rec']),
                'fouls_received': int(row['fouls_rec']),
                'tackles': int(row['tackles']),
                'offsides': int(row['offsides']),
                'goals': int(row['goals']),
                'assists': int(row['assists'])
            }
            
        players_dict[pid] = {
            'player_id': pid,
            'name': str(first_row['player_name'] or 'Jugador'),
            'pos': str(first_row['position'] or '-'),
            'number': str(first_row['shirt_number'] or '-'),
            'is_transferred': str(first_row['current_team']) != str(team_id),
            'unavail_reason': first_row['unavail_reason'] if pd.notna(first_row['unavail_reason']) and first_row['unavail_reason'] is not None else None,
            'matches': matches_dict
        }
        
    return players_dict

def get_league_player_stats(rank_type='shots', filter_type='all', order_by='avg', limit=100, venue='all', conn=None, player_team_map=None):
    """Obtiene las estadísticas de jugadores para la liga completa."""
    return _get_league_player_stats_core(rank_type=rank_type, filter_type=filter_type, order_by=order_by, match_limit=None, limit=limit, venue=venue, conn=conn, player_team_map=player_team_map)

def get_league_player_stats_last_matches(rank_type='shots', filter_type='all', order_by='avg', match_limit=5, limit=100, venue='all', conn=None, player_team_map=None):
    """Calcula estadísticas de jugadores usando únicamente los últimos N partidos de cada equipo."""
    return _get_league_player_stats_core(rank_type=rank_type, filter_type=filter_type, order_by=order_by, match_limit=match_limit, limit=limit, venue=venue, conn=conn, player_team_map=player_team_map)

def _get_league_player_stats_core(rank_type='shots', filter_type='all', order_by='avg', match_limit=None, limit=100, venue='all', conn=None, player_team_map=None):
    close_conn = False
    if conn is None:
        conn = get_db_connection()
        close_conn = True
    match_where = ""
    if match_limit:
        matches = conn.execute("""
            SELECT id, home_team_id, away_team_id, date 
            FROM matches 
            WHERE finished = 1 
            ORDER BY date DESC
        """).fetchall()

        team_matches = {}
        for m in matches:
            mid = str(m['id'])
            h_id = str(m['home_team_id'])
            a_id = str(m['away_team_id'])
            
            if venue in ('all', 'home'):
                if h_id not in team_matches: team_matches[h_id] = []
                if len(team_matches[h_id]) < match_limit: team_matches[h_id].append(mid)
            if venue in ('all', 'away'):
                if a_id not in team_matches: team_matches[a_id] = []
                if len(team_matches[a_id]) < match_limit: team_matches[a_id].append(mid)

        allowed_match_ids = set()
        for mids in team_matches.values():
            allowed_match_ids.update(mids)

        if not allowed_match_ids:
            if close_conn:
                conn.close()
            return []

        m_ids_str = ",".join([f"'{m}'" for m in allowed_match_ids])
        match_where = f" AND pmd.match_id IN ({m_ids_str})"

    venue_where = ""
    if venue == 'home':
        venue_where = " AND pmd.team_id = m.home_team_id"
    elif venue == 'away':
        venue_where = " AND pmd.team_id = m.away_team_id"

    if rank_type in ('shots', 'shots_on_target', 'shots_outside_box'):
        if filter_type == 'target' or rank_type == 'shots_on_target':
            stat_expr = "(SELECT COUNT(*) FROM shots_on_target s WHERE s.player_id = pmd.player_id AND s.match_id = pmd.match_id)"
        elif filter_type == 'long' or rank_type == 'shots_outside_box':
            stat_expr = "(SELECT COUNT(*) FROM shots_outside_box s WHERE s.player_id = pmd.player_id AND s.match_id = pmd.match_id)"
        else:
            stat_expr = "(SELECT COUNT(*) FROM shots s WHERE s.player_id = pmd.player_id AND s.match_id = pmd.match_id)"
    elif rank_type == 'headers':
        stat_expr = "(SELECT COUNT(*) FROM headers s WHERE s.player_id = pmd.player_id AND s.match_id = pmd.match_id)"
    elif rank_type == 'goals':
        stat_expr = "(SELECT COUNT(*) FROM goals g WHERE g.player_id = pmd.player_id AND g.match_id = pmd.match_id)"
    elif rank_type == 'assists':
        stat_expr = "(SELECT COUNT(*) FROM goals g WHERE g.assist_id = pmd.player_id AND g.match_id = pmd.match_id)"
    elif rank_type == 'cards':
        stat_expr = "(SELECT COALESCE(SUM(CASE WHEN LOWER(c.card_type) = 'red' THEN 2 WHEN c.card_id IS NOT NULL THEN 1 ELSE 0 END), 0) FROM cards c WHERE c.player_id = pmd.player_id AND c.match_id = pmd.match_id)"
    elif rank_type == 'fouls':
        stat_expr = "COALESCE(pmd.fouls_committed, 0)"
    elif rank_type in ('fouls_rec', 'fouls_received'):
        stat_expr = "COALESCE(pmd.fouls_received, 0)"
    elif rank_type == 'tackles':
        stat_expr = "COALESCE(pmd.tackles, 0)"
    elif rank_type == 'offsides':
        stat_expr = "COALESCE(pmd.offsides, 0)"
    else:
        if close_conn:
            conn.close()
        raise ValueError(f"Unsupported player rank_type: {rank_type}")

    relegated_str = ",".join([f"'{t}'" for t in RELEGATED_TEAMS])

    if player_team_map:
        select_team_cols = "pmd.team_id as t_id, '' as t_name,"
    else:
        select_team_cols = """
            (SELECT pmd_lt.team_id FROM player_match_details pmd_lt JOIN matches m_lt ON pmd_lt.match_id = m_lt.id WHERE pmd_lt.player_id = pmd.player_id ORDER BY m_lt.date DESC LIMIT 1) as t_id,
            (SELECT CASE WHEN m2.home_team_id = pmd_lt.team_id THEN m2.home_team ELSE m2.away_team END FROM player_match_details pmd_lt JOIN matches m2 ON pmd_lt.match_id = m2.id WHERE pmd_lt.player_id = pmd.player_id ORDER BY m2.date DESC LIMIT 1) as t_name,
        """

    query = f"""
        SELECT 
            pmd.player_id as id,
            COALESCE(pmd.short_name, pmd.name) as name,
            {select_team_cols}
            SUM({stat_expr}) as total,
            COUNT(DISTINCT CASE WHEN pmd.minutes_played > 0 THEN pmd.match_id END) as pj,
            SUM(COALESCE(pmd.minutes_played, 0)) as minutes_played
        FROM player_match_details pmd
        JOIN matches m ON pmd.match_id = m.id
        WHERE m.finished = 1 AND pmd.team_id NOT IN ({relegated_str}) {venue_where} {match_where}
        GROUP BY pmd.player_id
        HAVING total > 0
    """

    rows = conn.execute(query).fetchall()
    if close_conn:
        conn.close()

    results = []
    for r in rows:
        pid = str(r['id'])
        if player_team_map and pid in player_team_map:
            t_id, t_name = player_team_map[pid]
        else:
            t_id, t_name = str(r['t_id']), r['t_name'] or ""

        total = int(r['total'] or 0)
        pj = int(r['pj'] or 0)
        mp = int(r['minutes_played'] or 0)
        avg = round((total / mp) * 90, 2) if mp > 0 else 0.0

        results.append({
            "id": pid,
            "name": r['name'] or "Jugador",
            "t_id": t_id,
            "t_name": t_name,
            "total": total,
            "pj": pj,
            "minutes_played": mp,
            "avg": avg
        })

    if order_by == 'total':
        results.sort(key=lambda x: x['total'], reverse=True)
    else:
        results.sort(key=lambda x: (x['minutes_played'] >= 150, x['avg']), reverse=True)

    return results[:limit]


def get_referee_rankings(order_by='avg', limit=None):
    """
    Calcula la posicion de cada arbitro en un top basado en el volumen total de eventos.
    Retorna dos diccionarios: {NombreArbitro: PosicionRanking} para tarjetas y faltas.
    """
    if limit:
        rc_list = get_referee_stats_logic('cards', order_by, limit)
        rf_list = get_referee_stats_logic('fouls', order_by, limit)
        return {r['name']: i+1 for i, r in enumerate(rc_list)}, {r['name']: i+1 for i, r in enumerate(rf_list)}

    conn = get_db_connection()
    
    sort_col = "total" if order_by == 'total' else "avg"
    
    # Ranking por Total de Tarjetas
    ref_cards = conn.execute(f'''
        SELECT m.referee, COALESCE(SUM(CASE WHEN c.card_type = 'Red' THEN 2 WHEN c.card_id IS NOT NULL THEN 1 ELSE 0 END), 0) as total,
        CAST(COALESCE(SUM(CASE WHEN c.card_type = 'Red' THEN 2 WHEN c.card_id IS NOT NULL THEN 1 ELSE 0 END), 0) AS FLOAT) / COUNT(DISTINCT m.id) as avg
        FROM matches m LEFT JOIN cards c ON m.id = c.match_id 
        WHERE m.finished = 1 AND m.referee IS NOT NULL AND m.referee != '' GROUP BY m.referee ORDER BY {sort_col} DESC
    ''').fetchall()
    # Ranking por Total de Faltas
    ref_fouls = conn.execute(f'''
        SELECT m.referee, SUM(pmd.fouls_committed) as total,
        CAST(SUM(pmd.fouls_committed) AS FLOAT) / COUNT(DISTINCT m.id) as avg
        FROM matches m LEFT JOIN player_match_details pmd ON m.id = pmd.match_id 
        WHERE m.finished = 1 AND m.referee IS NOT NULL AND m.referee != '' GROUP BY m.referee ORDER BY {sort_col} DESC
    ''').fetchall()
    conn.close()
    return {r['referee']: i+1 for i, r in enumerate(ref_cards) if r['referee']}, {r['referee']: i+1 for i, r in enumerate(ref_fouls) if r['referee']}

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

def get_referee_stats_logic(category='cards', order_by='avg', limit=None, conn=None):
    close_conn = False
    if conn is None:
        conn = get_db_connection()
        close_conn = True
    if limit:
        # Optimizacion: Usar Window Functions para obtener ultimos N partidos de TODOS los arbitros en una consulta
        if category == 'cards':
            val_sql = "COALESCE(SUM(CASE WHEN c.card_type = 'Red' THEN 2 ELSE 1 END), 0)"
            join_sql = "LEFT JOIN cards c ON m.id = c.match_id"
        else:
            val_sql = "COALESCE(SUM(pmd.fouls_committed), 0)"
            join_sql = "LEFT JOIN player_match_details pmd ON m.id = pmd.match_id"

        query = f"""
            WITH RefMatches AS (
                SELECT id, referee, ROW_NUMBER() OVER (PARTITION BY referee ORDER BY date DESC) as rn
                FROM matches
                WHERE finished = 1 AND referee IS NOT NULL
            )
            SELECT m.referee as name, {val_sql} as total, COUNT(DISTINCT m.id) as pj
            FROM RefMatches m
            {join_sql}
            WHERE m.rn <= ?
            GROUP BY m.referee
        """
        res = conn.execute(query, (limit,)).fetchall()
        if close_conn:
            conn.close()
        
        results = []
        for r in res:
            total = r['total']
            pj = r['pj']
            avg = round(total / pj, 2) if pj > 0 else 0
            results.append({"name": r['name'], "total": total, "pj": pj, "avg": avg})
            
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
        if close_conn:
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

def get_lineup_data(match_id, team_id, cards_dict, target_side=None):
    """
    Obtiene titulares y sus posiciones visuales para la pizarra. 
    Integra la información de sustituciones (tabla substitutions) y tarjetas del encuentro.
    Normaliza role_x y role_y si la condición (home/away) del partido de origen difiere de target_side.
    """
    conn = get_db_connection()
    match_info = conn.execute('SELECT home_team_id, away_team_id FROM matches WHERE id = ?', (str(match_id),)).fetchone()
    source_side = None
    if match_info:
        source_side = 'home' if str(team_id) == str(match_info['home_team_id']) else 'away'

    players = conn.execute('''
        SELECT p.*, 
               s.player_in_id as sub_in_id,
               s.minute as sub_minute,
               s.injury as sub_injury,
               COALESCE(pin.short_name, pin.name) as sub_in_name,
               EXISTS(SELECT 1 FROM player_notes pn WHERE pn.player_id = p.player_id AND pn.notes IS NOT NULL AND pn.notes != '') as has_note
        FROM player_match_details p 
        LEFT JOIN substitutions s ON p.match_id = s.match_id AND p.player_id = s.player_out_id
        LEFT JOIN player_match_details pin ON s.match_id = pin.match_id AND s.player_in_id = pin.player_id
        WHERE p.match_id = ? AND p.team_id = ? AND p.is_starter = 1 AND p.role_x IS NOT NULL
    ''', (str(match_id), str(team_id))).fetchall()
    conn.close()

    if target_side is None:
        target_side = source_side or 'home'

    should_invert = (source_side is not None and source_side != target_side)

    res = []
    for p in players:
        d = dict(p)
        rx = d.get('role_x')
        ry = d.get('role_y')

        if rx is None or ry is None:
            rx, ry = 50.0, 50.0
        else:
            rx = rx if rx > 1 else rx * 100.0
            ry = ry if ry > 1 else ry * 100.0

        if should_invert:
            rx = 100.0 - rx
            ry = 100.0 - ry

        d['role_x'] = round(rx, 2)
        d['role_y'] = round(ry, 2)
        d['card'] = cards_dict.get(str(d['player_id']))
        res.append(d)
    return res

def get_bench_subs(match_id, team_id, cards_dict):
    """
    Obtiene suplentes del banco e integra sustituciones desde la tabla substitutions.
    """
    conn = get_db_connection()
    rows = conn.execute('''
        SELECT p.*,
               s.player_out_id as sub_out_id,
               s.minute as sub_minute,
               s.injury as sub_injury,
               COALESCE(pout.short_name, pout.name) as sub_out_name
        FROM player_match_details p
        LEFT JOIN substitutions s ON p.match_id = s.match_id AND p.player_id = s.player_in_id
        LEFT JOIN player_match_details pout ON s.match_id = pout.match_id AND s.player_out_id = pout.player_id
        WHERE p.match_id = ? AND p.team_id = ? AND p.is_starter = 0 AND p.unavailable = 0
    ''', (str(match_id), str(team_id))).fetchall()
    conn.close()
    res = []
    for r in rows:
        d = dict(r)
        d['player_name'] = d.get('last_name', '')
        d['card'] = cards_dict.get(str(d['player_id']))
        d['substitution'] = d.get('sub_out_id')
        res.append(d)
    return sorted(res, key=lambda x: {"ARQ":0,"DF":1,"M":2,"DL":3}.get(x['position'],99))

def _get_stat_sql_config(rank_type, filter_type='all'):
    """Helper para obtener fragmentos SQL segun el tipo de estadistica."""
    base_join = ""
    val_col = ""
    extra_where = ""
    if rank_type == 'tiradores' or rank_type == 'shots':
        base_join = "LEFT JOIN shots s ON pmd.player_id = s.player_id AND pmd.match_id = s.match_id"
        val_col = "COUNT(s.shot_id)"
    elif rank_type == 'shots_on_target' or rank_type == 'target':
        base_join = "LEFT JOIN shots_on_target s ON pmd.player_id = s.player_id AND pmd.match_id = s.match_id"
        val_col = "COUNT(s.shot_id)"
    elif rank_type == 'shots_outside_box' or rank_type == 'long':
        base_join = "LEFT JOIN shots_outside_box s ON pmd.player_id = s.player_id AND pmd.match_id = s.match_id"
        val_col = "COUNT(s.shot_id)"
    elif rank_type == 'headers':
        base_join = "LEFT JOIN headers s ON pmd.player_id = s.player_id AND pmd.match_id = s.match_id"
        val_col = "COUNT(s.shot_id)"
    elif rank_type == 'goals':
        base_join = "LEFT JOIN goals g ON pmd.player_id = g.player_id AND pmd.match_id = g.match_id"
        val_col = "COUNT(g.goal_id)"
    elif rank_type == 'yellows' or rank_type == 'cards':
        base_join = "LEFT JOIN cards c ON pmd.player_id = c.player_id AND pmd.match_id = c.match_id"
        val_col = "COALESCE(SUM(CASE WHEN LOWER(c.card_type) = 'red' THEN 2 WHEN c.card_id IS NOT NULL THEN 1 ELSE 0 END), 0)"
    elif rank_type == 'assists':
        base_join = "LEFT JOIN goals g ON pmd.player_id = g.assist_id AND pmd.match_id = g.match_id"
        val_col = "COUNT(g.goal_id)"
    elif rank_type == 'fouls':
        val_col = "SUM(pmd.fouls_committed)"
    elif rank_type == 'fouls_rec' or rank_type == 'fouls_received':
        val_col = "SUM(pmd.fouls_received)"
    elif rank_type == 'tackles':
        val_col = "SUM(pmd.tackles)"
    elif rank_type == 'offsides':
        val_col = "SUM(pmd.offsides)"
    # elif rank_type == 'corners':
    #     base_join = "LEFT JOIN shots s ON pmd.player_id = s.player_id AND pmd.match_id = s.match_id AND s.situation = 'corner'"
    #     val_col = "COUNT(s.shot_id)"
        
    return base_join, val_col, extra_where

def build_stats_page_payload():
    conn = get_db_connection()
    context = get_match_context(conn)
    teams, team_all, team_home, team_away, match_teams = context

    teams_map = {str(r['id']): r['name'] for r in conn.execute('''
        SELECT DISTINCT home_team_id as id, home_team as name FROM matches
        UNION
        SELECT DISTINCT away_team_id as id, away_team as name FROM matches
    ''').fetchall()}

    payload = {'teams': {}, 'players': {}, 'referees': {}}

    # 1. Teams
    team_cats = ['shots', 'shots_on_target', 'shots_outside_box', 'goals', 'headers', 'cards', 'fouls', 'tackles', 'offsides']
    sql_map = {
        'shots': "SELECT match_id, team_id, COUNT(*) as cnt FROM shots GROUP BY match_id, team_id",
        'shots_on_target': "SELECT match_id, team_id, COUNT(*) as cnt FROM shots_on_target GROUP BY match_id, team_id",
        'shots_outside_box': "SELECT match_id, team_id, COUNT(*) as cnt FROM shots_outside_box GROUP BY match_id, team_id",
        'headers': "SELECT match_id, team_id, COUNT(*) as cnt FROM headers GROUP BY match_id, team_id",
        'goals': "SELECT match_id, team_id, COUNT(*) as cnt FROM goals GROUP BY match_id, team_id",
        'cards': "SELECT match_id, team_id, SUM(CASE WHEN LOWER(card_type) = 'red' THEN 2 ELSE 1 END) as cnt FROM cards GROUP BY match_id, team_id",
        'fouls': "SELECT match_id, team_id, SUM(fouls_committed) as cnt FROM player_match_details GROUP BY match_id, team_id",
        'tackles': "SELECT match_id, team_id, SUM(tackles) as cnt FROM player_match_details GROUP BY match_id, team_id",
        'offsides': "SELECT match_id, team_id, SUM(offsides) as cnt FROM player_match_details GROUP BY match_id, team_id"
    }
    for cat in team_cats:
        payload['teams'][cat] = {}
        cached_rows = conn.execute(sql_map[cat]).fetchall()
        for limit in [None, 10, 5]:
            lim_key = str(limit) if limit else 'all'
            stats_data = get_teams_stats(cat, last_matches=limit, conn=conn, context=context, cached_rows=cached_rows)
            for side in ['made', 'against']:
                if side not in payload['teams'][cat]: payload['teams'][cat][side] = {}
                if lim_key not in payload['teams'][cat][side]: payload['teams'][cat][side][lim_key] = {}
                raw_list = stats_data.get(side, [])
                for venue in ['all', 'home', 'away']:
                    payload['teams'][cat][side][lim_key][venue] = format_team_stats_list(raw_list, venue=venue, limit=limit, context=context, teams_map=teams_map)

    # 2. Players (Single-Pass In-Memory Aggregation for <1s load time)
    matches_rows = conn.execute("SELECT id, home_team_id, away_team_id, date FROM matches WHERE finished = 1 ORDER BY date DESC").fetchall()
    team_all_mids = defaultdict(list)
    team_home_mids = defaultdict(list)
    team_away_mids = defaultdict(list)

    for m in matches_rows:
        mid, h_id, a_id = str(m['id']), str(m['home_team_id']), str(m['away_team_id'])
        team_all_mids[h_id].append(mid)
        team_all_mids[a_id].append(mid)
        team_home_mids[h_id].append(mid)
        team_away_mids[a_id].append(mid)

    team_l10_all = {t: set(mids[:10]) for t, mids in team_all_mids.items()}
    team_l5_all = {t: set(mids[:5]) for t, mids in team_all_mids.items()}
    team_l10_home = {t: set(mids[:10]) for t, mids in team_home_mids.items()}
    team_l5_home = {t: set(mids[:5]) for t, mids in team_home_mids.items()}
    team_l10_away = {t: set(mids[:10]) for t, mids in team_away_mids.items()}
    team_l5_away = {t: set(mids[:5]) for t, mids in team_away_mids.items()}

    shots_by_pm = defaultdict(lambda: {'shots': 0, 'shots_on_target': 0, 'shots_outside_box': 0, 'headers': 0})
    for r in conn.execute("SELECT player_id, match_id, on_target, inside_box, LOWER(shot_type) as st FROM shots").fetchall():
        if not r['player_id']: continue
        key = (str(r['player_id']), str(r['match_id']))
        s = shots_by_pm[key]
        s['shots'] += 1
        if r['on_target'] == 1: s['shots_on_target'] += 1
        if r['inside_box'] == 0: s['shots_outside_box'] += 1
        if r['st'] == 'head': s['headers'] += 1

    goals_by_pm = defaultdict(int)
    assists_by_pm = defaultdict(int)
    for r in conn.execute("SELECT player_id, assist_id, match_id FROM goals").fetchall():
        mid = str(r['match_id'])
        if r['player_id']: goals_by_pm[(str(r['player_id']), mid)] += 1
        if r['assist_id']: assists_by_pm[(str(r['assist_id']), mid)] += 1

    cards_by_pm = defaultdict(int)
    for r in conn.execute("SELECT player_id, match_id, card_type, card_id FROM cards").fetchall():
        if not r['player_id']: continue
        key = (str(r['player_id']), str(r['match_id']))
        val = 2 if (r['card_type'] and r['card_type'].lower() == 'red') else 1
        cards_by_pm[key] += val

    player_team_map = {}
    player_names = {}
    for r in conn.execute('''
        SELECT pmd.player_id, pmd.team_id, COALESCE(pmd.short_name, pmd.name) as name,
               CASE WHEN m.home_team_id = pmd.team_id THEN m.home_team ELSE m.away_team END as team_name
        FROM player_match_details pmd
        JOIN matches m ON pmd.match_id = m.id
        WHERE m.finished = 1
        ORDER BY m.date DESC
    ''').fetchall():
        pid = str(r['player_id'])
        if pid not in player_team_map:
            player_team_map[pid] = (str(r['team_id']), teams_map.get(str(r['team_id']), r['team_name'] or ''))
            player_names[pid] = r['name'] or 'Jugador'

    player_cats = ['shots', 'shots_on_target', 'shots_outside_box', 'goals', 'assists', 'headers', 'cards', 'fouls', 'fouls_received', 'tackles', 'offsides']
    venues = ['all', 'home', 'away']
    limits = [None, 10, 5]

    accum = {}
    for c in player_cats:
        accum[c] = {}
        for v in venues:
            accum[c][v] = {}
            for l in limits:
                lk = str(l) if l else 'all'
                accum[c][v][lk] = defaultdict(lambda: {'total': 0, 'pj': set(), 'minutes_played': 0})

    relegated_set = set(RELEGATED_TEAMS)
    pmd_rows = conn.execute('''
        SELECT pmd.player_id, pmd.match_id, pmd.team_id,
               pmd.minutes_played, pmd.fouls_committed, pmd.fouls_received, pmd.tackles, pmd.offsides,
               m.home_team_id, m.away_team_id
        FROM player_match_details pmd
        JOIN matches m ON pmd.match_id = m.id
        WHERE m.finished = 1
    ''').fetchall()

    for r in pmd_rows:
        tid = str(r['team_id'])
        if tid in relegated_set: continue
        
        pid = str(r['player_id'])
        mid = str(r['match_id'])
        mp = r['minutes_played'] or 0
        is_home = (tid == str(r['home_team_id']))
        
        pm_key = (pid, mid)
        sh = shots_by_pm.get(pm_key, {})
        
        stat_vals = {
            'shots': sh.get('shots', 0),
            'shots_on_target': sh.get('shots_on_target', 0),
            'shots_outside_box': sh.get('shots_outside_box', 0),
            'headers': sh.get('headers', 0),
            'goals': goals_by_pm.get(pm_key, 0),
            'assists': assists_by_pm.get(pm_key, 0),
            'cards': cards_by_pm.get(pm_key, 0),
            'fouls': r['fouls_committed'] or 0,
            'fouls_received': r['fouls_received'] or 0,
            'tackles': r['tackles'] or 0,
            'offsides': r['offsides'] or 0
        }
        
        is_in_l10_all = mid in team_l10_all[tid]
        is_in_l5_all = mid in team_l5_all[tid]
        is_in_l10_venue = mid in (team_l10_home[tid] if is_home else team_l10_away[tid])
        is_in_l5_venue = mid in (team_l5_home[tid] if is_home else team_l5_away[tid])
        
        for c in player_cats:
            val = stat_vals[c]
            
            # Venue: all
            acc_all_all = accum[c]['all']['all'][pid]
            acc_all_all['total'] += val
            if mp > 0: acc_all_all['pj'].add(mid)
            acc_all_all['minutes_played'] += mp
            
            if is_in_l10_all:
                acc_all_10 = accum[c]['all']['10'][pid]
                acc_all_10['total'] += val
                if mp > 0: acc_all_10['pj'].add(mid)
                acc_all_10['minutes_played'] += mp
                
            if is_in_l5_all:
                acc_all_5 = accum[c]['all']['5'][pid]
                acc_all_5['total'] += val
                if mp > 0: acc_all_5['pj'].add(mid)
                acc_all_5['minutes_played'] += mp
                
            # Venue: home / away
            v_key = 'home' if is_home else 'away'
            acc_v_all = accum[c][v_key]['all'][pid]
            acc_v_all['total'] += val
            if mp > 0: acc_v_all['pj'].add(mid)
            acc_v_all['minutes_played'] += mp
            
            if is_in_l10_venue:
                acc_v_10 = accum[c][v_key]['10'][pid]
                acc_v_10['total'] += val
                if mp > 0: acc_v_10['pj'].add(mid)
                acc_v_10['minutes_played'] += mp
                
            if is_in_l5_venue:
                acc_v_5 = accum[c][v_key]['5'][pid]
                acc_v_5['total'] += val
                if mp > 0: acc_v_5['pj'].add(mid)
                acc_v_5['minutes_played'] += mp

    caps = {None: 50, 10: 30, 5: 10}

    for c in player_cats:
        payload['players'][c] = {}
        for v in venues:
            payload['players'][c][v] = {}
            for l in limits:
                lk = str(l) if l else 'all'
                cap = caps[l]
                
                p_dict = accum[c][v][lk]
                
                formatted = []
                for pid, data in p_dict.items():
                    tot = data['total']
                    if tot <= 0: continue
                    pj_cnt = len(data['pj'])
                    mp_cnt = data['minutes_played']
                    avg_val = round((tot / mp_cnt) * 90, 2) if mp_cnt > 0 else 0.0
                    t_id, t_name = player_team_map.get(pid, ('', ''))
                    formatted.append({
                        'id': pid,
                        'name': player_names.get(pid, 'Jugador'),
                        't_id': t_id,
                        't_name': t_name,
                        'total': tot,
                        'pj': pj_cnt,
                        'minutes_played': mp_cnt,
                        'avg': avg_val
                    })
                    
                by_total = sorted(formatted, key=lambda x: x['total'], reverse=True)[:cap]
                by_avg = sorted(formatted, key=lambda x: (x['minutes_played'] >= 150, x['avg']), reverse=True)[:cap]
                
                seen = set()
                combined = []
                for item in by_total + by_avg:
                    if item['id'] not in seen:
                        seen.add(item['id'])
                        combined.append(item)
                payload['players'][c][v][lk] = combined

    # 3. Referees
    ref_cats = ['cards', 'fouls']
    for cat in ref_cats:
        payload['referees'][cat] = {}
        for limit in [None, 10, 5]:
            lim_key = str(limit) if limit else 'all'
            payload['referees'][cat][lim_key] = get_referee_stats_logic(cat, order_by='total', limit=limit, conn=conn)

    conn.close()
    return payload

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
        next_m = conn.execute("SELECT strftime('%Y', date) as y, tournament, gameweek FROM matches WHERE finished = 0 and cancelled = 0 ORDER BY date ASC LIMIT 1").fetchone()
        if next_m:
            year, tournament, gameweek = next_m[0], next_m[1], next_m[2]
        else: 
            last_m = conn.execute("SELECT strftime('%Y', date) as y, tournament, gameweek FROM matches WHERE finished = 1 and cancelled = 0 ORDER BY date DESC LIMIT 1").fetchone()
            if last_m:
                year, tournament, gameweek = last_m[0], last_m[1], last_m[2]
            else: year = (years[0] if years else "2025"); tournament = "Apertura"; gameweek = "1"
    matches = conn.execute("SELECT * FROM matches WHERE strftime('%Y', date) = ? AND gameweek = ? AND tournament LIKE ? ORDER BY date ASC", (str(year), str(gameweek), f'%{tournament}%')).fetchall()
    conn.close()

    stats = get_teams_rankings(stats_list=["shots", "shots_on_target", "shots_outside_box", "headers", "goals", "cards", "fouls", "tackles", "offsides"])

    sort_by = request.args.get('sort_by')

    return render_template('index.html', matches=matches, stats=stats, years=years, current_year=year, current_tournament=tournament, current_gameweek=gameweek, current_sort=sort_by)


#STATS
@app.route('/stats')
def stats_page():
    payload = build_stats_page_payload()
    return render_template('stats.html', team_map=json.dumps(TEAM_NAME_MAP), initial_stats=json.dumps(payload))

@app.route('/match/<match_id>')
def match_detail(match_id):
    """Analisis profundo con pizarra y predicciones en cliente (JS)"""
    conn = get_db_connection()
    match = conn.execute('SELECT * FROM matches WHERE id = ?', (str(match_id),)).fetchone()
    m_note = conn.execute('SELECT notes FROM match_notes WHERE match_id = ?', (str(match_id),)).fetchone()
    if not match: return "No existe", 404

    # Find Previous and Next Match
    prev_match = conn.execute('''
        SELECT * FROM matches 
        WHERE (
            strftime('%Y', date),
            CASE WHEN tournament LIKE '%Apertura%' THEN 1 WHEN tournament LIKE '%Clausura%' THEN 2 ELSE 3 END,
            CAST(gameweek AS INTEGER),
            date,
            CAST(id AS INTEGER)
        ) < (
            strftime('%Y', ?),
            CASE WHEN ? LIKE '%Apertura%' THEN 1 WHEN ? LIKE '%Clausura%' THEN 2 ELSE 3 END,
            CAST(? AS INTEGER),
            ?,
            CAST(? AS INTEGER)
        )
        ORDER BY 
            strftime('%Y', date) DESC,
            CASE WHEN tournament LIKE '%Apertura%' THEN 1 WHEN tournament LIKE '%Clausura%' THEN 2 ELSE 3 END DESC,
            CAST(gameweek AS INTEGER) DESC,
            date DESC,
            CAST(id AS INTEGER) DESC
        LIMIT 1
    ''', (match['date'], match['tournament'], match['tournament'], match['gameweek'], match['date'], int(match_id))).fetchone()

    next_match = conn.execute('''
        SELECT * FROM matches 
        WHERE (
            strftime('%Y', date),
            CASE WHEN tournament LIKE '%Apertura%' THEN 1 WHEN tournament LIKE '%Clausura%' THEN 2 ELSE 3 END,
            CAST(gameweek AS INTEGER),
            date,
            CAST(id AS INTEGER)
        ) > (
            strftime('%Y', ?),
            CASE WHEN ? LIKE '%Apertura%' THEN 1 WHEN ? LIKE '%Clausura%' THEN 2 ELSE 3 END,
            CAST(? AS INTEGER),
            ?,
            CAST(? AS INTEGER)
        )
        ORDER BY 
            strftime('%Y', date) ASC,
            CASE WHEN tournament LIKE '%Apertura%' THEN 1 WHEN tournament LIKE '%Clausura%' THEN 2 ELSE 3 END ASC,
            CAST(gameweek AS INTEGER) ASC,
            date ASC,
            CAST(id AS INTEGER) ASC
        LIMIT 1
    ''', (match['date'], match['tournament'], match['tournament'], match['gameweek'], match['date'], int(match_id))).fetchone()


    sf = request.args.get('shot_filter', 'all')
    
    stats = get_teams_rankings(stats_list=["shots", "shots_on_target", "shots_outside_box", "headers", "goals", "cards", "fouls", "tackles", "offsides"])
    rc, rf = get_referee_rankings() if match['referee'] else ({}, {})

    cards_dict = {str(r['player_id']): r['card_type'] for r in conn.execute('SELECT player_id, card_type FROM cards WHERE match_id = ?', (str(match_id),)).fetchall()}

    h_mid = match_id if match['finished'] == 1 else get_last_finished_match_id(match['home_team_id'])
    a_mid = match_id if match['finished'] == 1 else get_last_finished_match_id(match['away_team_id'])

    # Fetch all players for substitution name resolution
    m_ids = {str(match_id)}
    if h_mid: m_ids.add(str(h_mid))
    if a_mid: m_ids.add(str(a_mid))
    
    home_lineup = get_lineup_data(h_mid, match['home_team_id'], cards_dict, target_side='home') if h_mid else []
    away_lineup = get_lineup_data(a_mid, match['away_team_id'], cards_dict, target_side='away') if a_mid else []

    home_subs = get_bench_subs(h_mid or match_id, match['home_team_id'], cards_dict)
    away_subs = get_bench_subs(a_mid or match_id, match['away_team_id'], cards_dict)

    match_stats = {
        "home": {"shots": 0, "target": 0, "outside": 0, "headers": 0, "fouls": 0, "corners": 0, "offsides": 0, "tackles": 0, "yellows": 0, "reds": 0},
        "away": {"shots": 0, "target": 0, "outside": 0, "headers": 0, "fouls": 0, "corners": 0, "offsides": 0, "tackles": 0, "yellows": 0, "reds": 0}
    }
    
    if match['finished'] == 1:
        # Shots summaries
        for r in conn.execute('SELECT team_id, COUNT(*) as tot FROM shots WHERE match_id=? GROUP BY team_id', (str(match_id),)).fetchall():
            k = "home" if str(r['team_id']) == str(match['home_team_id']) else "away"; match_stats[k]["shots"] = r['tot']
        
        for r in conn.execute('SELECT team_id, COUNT(*) as tar FROM shots_on_target WHERE match_id=? GROUP BY team_id', (str(match_id),)).fetchall():
            k = "home" if str(r['team_id']) == str(match['home_team_id']) else "away"; match_stats[k]["target"] = r['tar']

        for r in conn.execute('SELECT team_id, COUNT(*) as outs FROM shots_outside_box WHERE match_id=? GROUP BY team_id', (str(match_id),)).fetchall():
            k = "home" if str(r['team_id']) == str(match['home_team_id']) else "away"; match_stats[k]["outside"] = r['outs']

        for r in conn.execute('SELECT team_id, COUNT(*) as heads FROM headers WHERE match_id=? GROUP BY team_id', (str(match_id),)).fetchall():
            k = "home" if str(r['team_id']) == str(match['home_team_id']) else "away"; match_stats[k]["headers"] = r['heads']

        # Player stats summary
        for r in conn.execute('SELECT team_id, SUM(fouls_committed) as f, SUM(offsides) as o, SUM(tackles) as t FROM player_match_details WHERE match_id=? GROUP BY team_id', (str(match_id),)).fetchall():
            k = "home" if str(r['team_id']) == str(match['home_team_id']) else "away"
            match_stats[k]["fouls"] = r['f'] or 0
            match_stats[k]["offsides"] = r['o'] or 0
            match_stats[k]["tackles"] = r['t'] or 0

        # for r in conn.execute('SELECT team_id, COUNT(*) as c FROM shots WHERE match_id=? AND (situation="Corner" OR situation="corner") GROUP BY team_id', (str(match_id),)).fetchall():
        #     k = "home" if str(r['team_id']) == str(match['home_team_id']) else "away"
        #     match_stats[k]["corners"] = r['c'] or 0

        # Cards
        for r in conn.execute('SELECT team_id, card_type, COUNT(*) as tot FROM cards WHERE match_id=? GROUP BY team_id, card_type', (str(match_id),)).fetchall():
            k = "home" if str(r['team_id']) == str(match['home_team_id']) else "away"
            if r['card_type'] == 'Yellow': match_stats[k]["yellows"] += r['tot']
            else: match_stats[k]["reds"] += r['tot']

    # Unavailable players
    unavail_home = conn.execute('SELECT name, unavailability_reason as reason FROM player_match_details WHERE match_id=? AND team_id=? AND unavailable=1', (str(match_id), str(match['home_team_id']))).fetchall()
    unavail_away = conn.execute('SELECT name, unavailability_reason as reason FROM player_match_details WHERE match_id=? AND team_id=? AND unavailable=1', (str(match_id), str(match['away_team_id']))).fetchall()

    # H2H: Partidos previos entre estos dos equipos
    h2h_matches = conn.execute('''
        SELECT id, date, tournament, home_team, away_team, score, home_team_id, away_team_id
        FROM matches
        WHERE ((home_team_id = ? AND away_team_id = ?) OR (home_team_id = ? AND away_team_id = ?))
          AND finished = 1 AND id != ?
        ORDER BY date DESC LIMIT 5
    ''', (str(match['home_team_id']), str(match['away_team_id']), str(match['away_team_id']), str(match['home_team_id']), str(match_id))).fetchall()

    # Ultimos 5 y 10 partidos de cada equipo (para contexto del ranking)
    def get_context_matches(tid, limit):
        rows = conn.execute('SELECT id, gameweek, home_team_id, away_team_id, home_team, away_team, score, date FROM matches WHERE (home_team_id = ? OR away_team_id = ?) AND finished = 1 ORDER BY date DESC LIMIT ?', (str(tid), str(tid), limit)).fetchall()
        res = []
        for r in rows:
            is_home = str(r['home_team_id']) == str(tid)
            rival_id = r['away_team_id'] if is_home else r['home_team_id']
            rival_name = r['away_team'] if is_home else r['home_team']
            cond = 'N' if r['gameweek'] == '20' else 'L' if is_home else 'V'
            
            # Determinar resultado
            res_val = 'D'
            if r['score'] and '-' in r['score']:
                try:
                    h_s, a_s = map(int, r['score'].split('-'))
                    if h_s == a_s: res_val = 'D'
                    elif (is_home and h_s > a_s) or (not is_home and a_s > h_s): res_val = 'W'
                    else: res_val = 'L'
                except: pass
                
            res.append({'rival_id': rival_id, 'rival_name': rival_name, 'cond': cond, 'id': str(r['id']), 'score': r['score'], 'result': res_val, 'date': r['date']})
        return res

    l10_home = get_context_matches(match['home_team_id'], 10)
    l10_away = get_context_matches(match['away_team_id'], 10)
    l5_home = l10_home[:5]
    l5_away = l10_away[:5]
    
    # Contexto del "Ultimo Partido" (para cuando el actual esta pendiente)
    def get_single_context(mid, tid):
        if not mid: return None
        r = conn.execute('SELECT id, home_team_id, away_team_id, home_team, away_team, score FROM matches WHERE id = ?', (str(mid),)).fetchone()
        if not r: return None
        is_home = str(r['home_team_id']) == str(tid)
        rival_id = r['away_team_id'] if is_home else r['home_team_id']
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

    last_match_home = get_single_context(h_mid, match['home_team_id']) if match['finished'] == 0 else None
    last_match_away = get_single_context(a_mid, match['away_team_id']) if match['finished'] == 0 else None

    # Historial del Arbitro con estos equipos
    ref_history = []
    if match['referee']:
        # Buscar partidos de este arbitro dirigiendo a CUALQUIERA de los dos equipos
        ref_matches_raw = conn.execute('''
            SELECT m.id, m.date, m.tournament, m.home_team, m.away_team, m.home_team_id, m.away_team_id, m.score
            FROM matches m
            WHERE m.referee = ?
              AND (m.home_team_id IN (?, ?) OR m.away_team_id IN (?, ?))
              AND m.finished = 1 AND m.id != ?
            ORDER BY m.date DESC LIMIT 10
        ''', (match['referee'], str(match['home_team_id']), str(match['away_team_id']), str(match['home_team_id']), str(match['away_team_id']), str(match_id))).fetchall()

        for m in ref_matches_raw:
            mid = str(m['id'])
            # Faltas por equipo
            f_h = conn.execute('SELECT SUM(fouls_committed) FROM player_match_details WHERE match_id=? AND team_id=?', (mid, str(m['home_team_id']))).fetchone()[0] or 0
            f_v = conn.execute('SELECT SUM(fouls_committed) FROM player_match_details WHERE match_id=? AND team_id=?', (mid, str(m['away_team_id']))).fetchone()[0] or 0
            # Tarjetas por equipo
            c_h = conn.execute("SELECT COALESCE(SUM(CASE WHEN card_type = 'Red' THEN 2 WHEN card_id IS NOT NULL THEN 1 ELSE 0 END), 0) FROM cards WHERE match_id=? AND team_id=?", (mid, str(m['home_team_id']))).fetchone()[0]
            c_v = conn.execute("SELECT COALESCE(SUM(CASE WHEN card_type = 'Red' THEN 2 WHEN card_id IS NOT NULL THEN 1 ELSE 0 END), 0) FROM cards WHERE match_id=? AND team_id=?", (mid, str(m['away_team_id']))).fetchone()[0]
            
            ref_history.append({
                'date': m['date'], 'match_id': m['id'], 
                'home_team': m['home_team'], 'away_team': m['away_team'], 
                'score': m['score'], 'tournament': m['tournament'],
                'stats': {'h_cards': c_h, 'h_fouls': f_h, 'v_cards': c_v, 'v_fouls': f_v},
                'home_team_id': m['home_team_id'], 'away_team_id': m['away_team_id']
            })

    # GOLES DEL PARTIDO
    match_goals = []
    if match['finished'] == 1:
        goals_data = conn.execute('''
            SELECT g.minute, g.team_id, g.is_own_goal, COALESCE(pmd.short_name, pmd.name) as player_name,
                   (SELECT COALESCE(pmd2.short_name, pmd2.name) FROM player_match_details pmd2 WHERE pmd2.player_id = g.assist_id AND pmd2.match_id = g.match_id) as assist_name 
            FROM goals g
            LEFT JOIN player_match_details pmd ON g.player_id = pmd.player_id AND g.match_id = pmd.match_id
            WHERE g.match_id = ? ORDER BY CAST(g.minute as INTEGER) ASC
        ''', (str(match_id),)).fetchall()
        for g in goals_data:
            tid = str(g['team_id'])
            scorer = g['player_name'] if g['player_name'] else "Desconocido"
            
            if g['is_own_goal']:
                scorer += " (EC)"
                if tid == str(match['home_team_id']):
                    tid = str(match['away_team_id'])
                else:
                    tid = str(match['home_team_id'])
            
            match_goals.append({
                'minute': g['minute'], 'team_id': tid, 
                'scorer': scorer, 'assist': g['assist_name']
            })

    # SHOTMAP DATA
    match_shots = []
    if match['finished'] == 1:
        shots_rows = conn.execute('''
            SELECT s.*, COALESCE(pmd.short_name, pmd.name) as player_name 
            FROM shots s 
            LEFT JOIN player_match_details pmd ON s.player_id = pmd.player_id AND s.match_id = pmd.match_id
            WHERE s.match_id = ? 
        ''', (str(match_id),)).fetchall()
        
        for s in shots_rows:
            
            match_shots.append({
                "x": s['x'], "y": s['y'], 
                "blocked_x": s['blocked_x'],
                "blocked_y": s['blocked_y'],
                "goal_cross_x": s['goal_cross_x'], 
                "goal_cross_y": s['goal_cross_y'], 
                "is_blocked": s['is_blocked'],
                "own_goal": 1 if s['outcome'] == 'OwnGoal' else 0,
                "outcome": s['outcome'],
                "shot_type": s['shot_type'],
                "situation": s['situation'],
                "team_id": str(s['team_id']),
                "player_name": s['player_name'] or "Desconocido",
                "on_target": s['on_target'],
                "minute": s['minute']
            })

    full_home_players = get_team_players_full_matrix(match['home_team_id'], context_match_id=match_id)
    full_away_players = get_team_players_full_matrix(match['away_team_id'], context_match_id=match_id)

    conn.close()
    return render_template('match.html', match=match, prev_match=prev_match, next_match=next_match, home_lineup=home_lineup, away_lineup=away_lineup, home_subs=home_subs, away_subs=away_subs, home_top=get_team_rankings_logic(match['home_team_id']), away_top=get_team_rankings_logic(match['away_team_id']), stats=stats, match_stats=match_stats, rc=rc, rf=rf, m_note=m_note, lineup_label="Formacion" if match['finished'] else "ultimo 11", current_filter=sf, h2h_matches=h2h_matches, ref_history=ref_history, l5_home=l5_home, l5_away=l5_away, l10_home=l10_home, l10_away=l10_away, last_match_home=last_match_home, last_match_away=last_match_away, h_mid=h_mid, a_mid=a_mid, match_goals=match_goals, match_shots=match_shots, unavail_home=unavail_home, unavail_away=unavail_away, full_home_players=full_home_players, full_away_players=full_away_players)

@app.route('/api/team_players_full/<team_id>')
def api_team_players_full(team_id):
    context_match_id = request.args.get('context_match_id')
    return jsonify(get_team_players_full_matrix(team_id, context_match_id=context_match_id))

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
    """Devuelve estadisticas de equipos usando get_teams_stats. Parametros: category, filter, side (made|against), limit, order_by, venue."""
    category = request.args.get('category', 'shots')
    filter_type = request.args.get('filter', 'all')
    side = request.args.get('side', 'made')
    limit = request.args.get('limit', type=int)
    order_by = request.args.get('order_by', 'avg')
    venue = request.args.get('venue', 'all')

    conn = get_db_connection()
    context = get_match_context(conn)
    teams, team_all, team_home, team_away, match_teams = context

    teams_map = {str(r['id']): r['name'] for r in conn.execute('''
        SELECT DISTINCT home_team_id as id, home_team as name FROM matches
        UNION
        SELECT DISTINCT away_team_id as id, away_team as name FROM matches
    ''').fetchall()}

    stats_data = get_teams_stats(category, last_matches=limit, conn=conn, context=context)
    conn.close()

    raw_list = stats_data.get(side, [])
    output = format_team_stats_list(raw_list, venue=venue, limit=limit, context=context, teams_map=teams_map)

    sort_key = 'total' if order_by == 'total' else 'avg'
    output.sort(key=lambda x: x[sort_key], reverse=True)

    return jsonify(output)


@app.route('/api/player_stats')
def api_player_stats():
    """Devuelve estadisticas de jugadores usando las funciones optimizadas."""
    rank_type = request.args.get('rank_type', 'shots')
    if rank_type == 'shots' and request.args.get('category'):
        rank_type = request.args.get('category')
    filter_type = request.args.get('filter', 'all')
    limit_matches = request.args.get('limit_matches', type=int)
    order_by = request.args.get('order_by', 'avg')
    venue = request.args.get('venue', 'all')
    limit = request.args.get('limit', type=int) or 100

    if limit_matches:
        data = get_league_player_stats_last_matches(rank_type, filter_type, order_by=order_by, match_limit=limit_matches, limit=limit, venue=venue)
    else:
        data = get_league_player_stats(rank_type, filter_type, order_by=order_by, limit=limit, venue=venue)
    return jsonify(data)

@app.route('/api/referee_stats')
def api_referee_stats():
    """Devuelve estadisticas de arbitros usando la funcion optimizada."""
    category = request.args.get('category', 'cards')
    limit = request.args.get('limit', type=int)
    order_by = request.args.get('order_by', 'avg')
    return jsonify(get_referee_stats_logic(category, order_by, limit))

@app.route('/match/<match_id>/player/<player_id>/heatmap')
def match_player_heatmap(match_id, player_id):
    conn = get_db_connection()
    rows = conn.execute('''
        SELECT player_id, match_id, team_id, x, y, count
        FROM player_heatmap_points
        WHERE match_id = ? AND player_id = ?
    ''', (str(match_id), str(player_id))).fetchall()

    pmd = conn.execute('''
        SELECT minutes_played
        FROM player_match_details
        WHERE match_id = ? AND player_id = ?
    ''', (str(match_id), str(player_id))).fetchone()
    conn.close()

    minutes = pmd['minutes_played'] if (pmd and pmd['minutes_played'] is not None) else None

    return jsonify({
        "match_id": str(match_id),
        "player_id": str(player_id),
        "minutes_played": minutes,
        "points": [dict(r) for r in rows]
    })


@app.route('/player_info/<player_id>')
@app.route('/player_info/<player_id>/<match_id>')
def player_info(player_id, match_id=None):
    conn = get_db_connection()

    # 1. Info básica del jugador
    info = conn.execute('''
        SELECT pmd.*, m.home_team, m.away_team, m.home_team_id, m.away_team_id, m.score
        FROM player_match_details pmd
        JOIN matches m ON pmd.match_id = m.id
        WHERE pmd.player_id = ? AND pmd.minutes_played IS NOT NULL AND pmd.minutes_played > 0
        ORDER BY m.date DESC LIMIT 1
    ''', (player_id,)).fetchone()

    if not info:
        info = conn.execute('''
            SELECT pmd.*, m.home_team, m.away_team, m.home_team_id, m.away_team_id, m.score
            FROM player_match_details pmd
            JOIN matches m ON pmd.match_id = m.id
            WHERE pmd.player_id = ?
            ORDER BY m.date DESC LIMIT 1
        ''', (player_id,)).fetchone()

    if not info:
        conn.close()
        return jsonify({"error": "No data"}), 404

    teams_history = [str(r['team_id']) for r in conn.execute(
        'SELECT DISTINCT team_id FROM player_match_details WHERE player_id = ? AND team_id IS NOT NULL',
        (player_id,)
    ).fetchall()]

    def process_match_row(m, default_team_id=None):
        mid = str(m['match_id'])
        tid = str(m['team_id']) if m['team_id'] else str(default_team_id or info['team_id'])
        is_home = str(m['home_team_id']) == tid
        team_name = m['home_team'] if is_home else m['away_team']
        rival = m['away_team'] if is_home else m['home_team']
        rival_id = str(m['away_team_id']) if is_home else str(m['home_team_id'])
        score = m['score'] or ''

        res_val = 'D'
        if score and '-' in score:
            try:
                h_s, a_s = map(int, score.split('-'))
                if h_s == a_s:
                    res_val = 'D'
                elif (is_home and h_s > a_s) or (not is_home and a_s > h_s):
                    res_val = 'W'
                else:
                    res_val = 'L'
            except Exception:
                pass

        pm_mins = m['minutes_played'] if m['minutes_played'] is not None else 0
        is_starter = m['is_starter'] if m['is_starter'] is not None else 0
        is_sub = True if (is_starter == 0 and pm_mins > 0) else False

        return {
            "match_id": mid,
            "date": m['date'],
            "team_id": tid,
            "team_name": team_name,
            "rival": rival,
            "rival_id": rival_id,
            "cond": 'L' if is_home else 'V',
            "score": score,
            "result": res_val,
            "minutes": pm_mins,
            "is_sub": is_sub,
            "match_stats": {
                "pj": 1 if pm_mins > 0 else 0,
                "mins": pm_mins,
                "shots": m['shots'] or 0,
                "target": m['target'] or 0,
                "long": m['long'] or 0,
                "headers": m['headers'] or 0,
                "tackles": m['tackles'] or 0,
                "offsides": m['offsides'] or 0,
                "cards": m['cards'] or 0,
                "f_c": m['f_c'] or 0,
                "f_r": m['f_r'] or 0
            }
        }

    # 2. Todos los partidos donde el jugador participó (con minutos > 0 o convocatorias)
    player_matches_raw = conn.execute('''
        SELECT 
            pmd.match_id, pmd.team_id, pmd.minutes_played, pmd.is_starter,
            pmd.fouls_committed as f_c, pmd.fouls_received as f_r,
            pmd.tackles, pmd.offsides,
            m.date, m.home_team, m.away_team, m.home_team_id, m.away_team_id, m.score,
            (SELECT COUNT(*) FROM shots WHERE player_id = pmd.player_id AND match_id = pmd.match_id) as shots,
            (SELECT COUNT(*) FROM shots_on_target WHERE player_id = pmd.player_id AND match_id = pmd.match_id) as target,
            (SELECT COUNT(*) FROM shots_outside_box WHERE player_id = pmd.player_id AND match_id = pmd.match_id) as long,
            (SELECT COUNT(*) FROM headers WHERE player_id = pmd.player_id AND match_id = pmd.match_id) as headers,
            (SELECT COALESCE(SUM(CASE WHEN card_type = 'Red' THEN 2 WHEN card_id IS NOT NULL THEN 1 ELSE 0 END), 0) FROM cards WHERE player_id = pmd.player_id AND match_id = pmd.match_id) as cards
        FROM player_match_details pmd
        JOIN matches m ON pmd.match_id = m.id
        WHERE pmd.player_id = ? AND m.finished = 1
        ORDER BY m.date DESC
    ''', (player_id,)).fetchall()

    all_matches = [process_match_row(r) for r in player_matches_raw]

    # 3. Partidos de cada club del jugador (últimos 10 partidos del equipo, incluyendo si no jugó)
    team_matches = {}
    for tid in teams_history:
        team_rows = conn.execute('''
            SELECT 
                m.id as match_id, m.date, m.home_team, m.away_team, m.home_team_id, m.away_team_id, m.score,
                pmd.team_id, pmd.minutes_played, pmd.is_starter,
                pmd.fouls_committed as f_c, pmd.fouls_received as f_r,
                pmd.tackles, pmd.offsides,
                (SELECT COUNT(*) FROM shots WHERE player_id = ? AND match_id = m.id) as shots,
                (SELECT COUNT(*) FROM shots_on_target WHERE player_id = ? AND match_id = m.id) as target,
                (SELECT COUNT(*) FROM shots_outside_box WHERE player_id = ? AND match_id = m.id) as long,
                (SELECT COUNT(*) FROM headers WHERE player_id = ? AND match_id = m.id) as headers,
                (SELECT COALESCE(SUM(CASE WHEN card_type = 'Red' THEN 2 WHEN card_id IS NOT NULL THEN 1 ELSE 0 END), 0) FROM cards WHERE player_id = ? AND match_id = m.id) as cards
            FROM matches m
            LEFT JOIN player_match_details pmd ON m.id = pmd.match_id AND pmd.player_id = ?
            WHERE (m.home_team_id = ? OR m.away_team_id = ?) AND m.finished = 1
            ORDER BY m.date DESC LIMIT 10
        ''', (player_id, player_id, player_id, player_id, player_id, player_id, tid, tid)).fetchall()

        team_matches[str(tid)] = [process_match_row(r, default_team_id=tid) for r in team_rows]

    # Diccionario de nombres de equipos del jugador
    teams_names = {}
    for m in all_matches:
        if m.get('team_id') and m.get('team_name'):
            teams_names[str(m['team_id'])] = m['team_name']
    for tid, tlist in team_matches.items():
        for m in tlist:
            if m.get('team_id') and m.get('team_name'):
                teams_names[str(m['team_id'])] = m['team_name']
    if str(info['team_id']) not in teams_names:
        is_home = str(info['team_id']) == str(info['home_team_id'])
        teams_names[str(info['team_id'])] = info['home_team'] if is_home else info['away_team']

    # 4. Tiros del jugador
    shots_rows = conn.execute('''
        SELECT s.x, s.y, s.blocked_x, s.blocked_y, s.goal_cross_x, s.goal_cross_y,
               s.is_blocked, s.outcome, s.situation, s.shot_type, s.on_target, s.minute,
               m.home_team_id, m.away_team_id, s.team_id, m.date, m.id as match_id, m.home_team, m.away_team
        FROM shots s
        JOIN matches m ON s.match_id = m.id
        WHERE s.player_id = ? AND s.x IS NOT NULL AND s.y IS NOT NULL
        ORDER BY m.date DESC
    ''', (player_id,)).fetchall()

    player_shots = []
    for s in shots_rows:
        is_home = str(s['team_id']) == str(s['home_team_id'])
        rival = s['away_team'] if is_home else s['home_team']
        rival_id = str(s['away_team_id']) if is_home else str(s['home_team_id'])
        let_cx = s['blocked_x'] if s['is_blocked'] else s['goal_cross_x']
        let_cy = s['blocked_y'] if s['is_blocked'] else s['goal_cross_y']

        player_shots.append({
            "x": s['x'], "y": s['y'], "cx": let_cx, "cy": let_cy,
            "is_blocked": s['is_blocked'], "outcome": s['outcome'],
            "situation": s['situation'], "shot_type": s['shot_type'],
            "on_target": s['on_target'], "minute": s['minute'],
            "is_home": is_home, "date": s['date'], "match_id": str(s['match_id']),
            "team_id": str(s['team_id']), "rival": rival, "rival_id": rival_id
        })

    # Logica de Rankings (Top 20)
    def get_top_rankings():
        metrics = [
            ("Tiros Totales", "shots s JOIN player_match_details p ON s.player_id = p.player_id AND s.match_id = p.match_id", "COUNT(*)", []),
            ("Tiros al Arco", "shots_on_target s JOIN player_match_details p ON s.player_id = p.player_id AND s.match_id = p.match_id", "COUNT(*)", []),
            ("Tiros Lejanos", "shots_outside_box s JOIN player_match_details p ON s.player_id = p.player_id AND s.match_id = p.match_id", "COUNT(*)", []),
            ("Faltas Cometidas", "player_match_details p", "SUM(p.fouls_committed)", []),
            ("Faltas Recibidas", "player_match_details p", "SUM(p.fouls_received)", []),
            ("Tarjetas", "cards c JOIN player_match_details p ON c.player_id = p.player_id AND c.match_id = p.match_id", "COALESCE(SUM(CASE WHEN c.card_type = 'Red' THEN 2 WHEN card_id IS NOT NULL THEN 1 ELSE 0 END), 0)", []),
            ("Cabezazos", "headers s JOIN player_match_details p ON s.player_id = p.player_id AND s.match_id = p.match_id", "COUNT(*)", [])
        ]
        
        scopes = {
            "liga": "1=1",
            "equipo": f"p.team_id = '{info['team_id']}'",
            "posicion": f"p.position = '{info['position']}'"
        }

        results = {"liga": [], "equipo": [], "posicion": []}

        for scope_name, scope_filter in scopes.items():
            for label, table_clause, agg_func, metric_filters in metrics:
                where_conditions = [scope_filter] + metric_filters
                where_clause = " WHERE " + " AND ".join(where_conditions)
                
                query = f"""
                    SELECT p.player_id, {agg_func} as val 
                    FROM {table_clause} 
                    {where_clause} 
                    GROUP BY p.player_id 
                    ORDER BY val DESC
                """
                res = conn.execute(query).fetchall()
                for i, r in enumerate(res):
                    pos = i + 1
                    if pos > 20: break
                    if str(r[0]) == str(player_id):
                        results[scope_name].append({
                            "label": label,
                            "pos": pos,
                            "total": int(r['val'])
                        })
                        break
        return results

    rankings_top = get_top_rankings()
    note = conn.execute('SELECT notes FROM player_notes WHERE player_id = ?', (player_id,)).fetchone()
    conn.close()

    # Si hay contexto de partido específico, buscar sus stats
    current_match_record = next((m for m in all_matches if m['match_id'] == str(match_id)), None)
    partido_stats = current_match_record['match_stats'] if current_match_record else {
        "pj": 0, "mins": 0, "shots": 0, "target": 0, "long": 0, "headers": 0,
        "tackles": 0, "offsides": 0, "cards": 0, "f_c": 0, "f_r": 0
    }

    d_info = dict(info)
    return jsonify({
        "match_id": str(match_id or d_info.get('match_id', '')),
        "home_team_id": str(info["home_team_id"]),
        "away_team_id": str(info["away_team_id"]),
        "home_team": info["home_team"],
        "away_team": info["away_team"],
        "score": info["score"] if info["score"] else "",
        "team_id": str(d_info['team_id']),
        "name": d_info.get('short_name') or d_info.get('name') or f"{d_info.get('first_name', '')} {d_info.get('last_name', '')}".strip(),
        "team": info["home_team"] if str(info["team_id"]) == str(info["home_team_id"]) else info["away_team"],
        "pos": "Delantero" if info["position"] == "DL" else "Mediocampista" if info["position"] == "M" else "Defensor" if info["position"] == "DF" else "Arquero" if info["position"] == "ARQ" else "Desconocido",
        "number": info["shirt_number"],
        "age": info["age"],
        "teams_history": teams_history,
        "teams_names": teams_names,
        "all_matches": all_matches,
        "team_matches": team_matches,
        "current_match_stats": partido_stats,
        "shots": player_shots,
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
    match = conn.execute('SELECT home_team_id, away_team_id, referee FROM matches WHERE id = ?', (str(match_id),)).fetchone()
    conn.close()
    if not match: return jsonify({"error": "N/A"}), 404
    ft = request.args.get('shot_filter', 'all')
    limit = request.args.get('limit', type=int)
    venue_split = request.args.get('venue_split') == 'true'
    return jsonify({
        "shots": get_prediction_logic(match['home_team_id'], match['away_team_id'], 'shots', ft, limit=limit, venue_split=venue_split),
        "headers": get_prediction_logic(match['home_team_id'], match['away_team_id'], 'headers', limit=limit, venue_split=venue_split),
        "cards": get_prediction_logic(match['home_team_id'], match['away_team_id'], 'cards', referee=match['referee'], limit=limit, venue_split=venue_split),
        "fouls": get_prediction_logic(match['home_team_id'], match['away_team_id'], 'fouls', referee=match['referee'], limit=limit, venue_split=venue_split),
        "tackles": get_prediction_logic(match['home_team_id'], match['away_team_id'], 'tackles', limit=limit, venue_split=venue_split),
        # "corners": get_prediction_logic(match['home_team_id'], match['away_team_id'], 'corners', limit=limit, venue_split=venue_split),
        "offsides": get_prediction_logic(match['home_team_id'], match['away_team_id'], 'offsides', limit=limit, venue_split=venue_split)
    })

@app.route('/api/match_heatmap/<match_id>')
def api_match_heatmap(match_id):
    conn = get_db_connection()
    match = conn.execute('SELECT home_team_id, away_team_id FROM matches WHERE id = ?', (str(match_id),)).fetchone()
    if not match: return jsonify({"error": "Match not found"}), 404
    
    home_id = str(match['home_team_id'])
    away_id = str(match['away_team_id'])
    limit = request.args.get('limit', type=int)
    
    only_arco = request.args.get('only_arco') == '1'
    hide_cabeza = request.args.get('hide_cabeza') == '1'
    only_lejos = request.args.get('only_lejos') == '1'
    only_home_away = request.args.get('only_home_away') == '1'

    def get_shots(team_id, is_home, type_shot, limit_n=None):
        team_id_str = str(team_id)
        if type_shot == 'made':
            from_table = "shots s"
            where = f"CAST(s.team_id AS TEXT) = '{team_id_str}'"
        else:
            from_table = "shots_received s"
            where = f"CAST(s.against_team_id AS TEXT) = '{team_id_str}'"
        
        limit_sql = ""
        if limit_n:
            sub_q = "SELECT id FROM matches WHERE (CAST(home_team_id AS TEXT) = ? OR CAST(away_team_id AS TEXT) = ?) AND finished = 1 ORDER BY date DESC LIMIT ?"
            m_rows = conn.execute(sub_q, (team_id_str, team_id_str, limit_n)).fetchall()
            if not m_rows: return {"shots": [], "matches_count": 1}
            ids = ",".join([f"'{r[0]}'" for r in m_rows])
            limit_sql = f"AND s.match_id IN ({ids})"
            
        filter_sql = ""
        if only_home_away:
            if is_home:
                filter_sql += f" AND CAST(m.home_team_id AS TEXT) = '{team_id_str}'"
            else:
                filter_sql += f" AND CAST(m.away_team_id AS TEXT) = '{team_id_str}'"
        
        if only_arco: filter_sql += " AND s.on_target = 1"
        if hide_cabeza: filter_sql += " AND LOWER(s.shot_type) != 'head'"
        if only_lejos: filter_sql += " AND s.inside_box = 0"
        
        query = f"""
            SELECT s.x as x, s.y as y, is_home_team as was_home, s.inside_box, s.match_id,
                   COALESCE(pmd.short_name, pmd.name) as player_name, pmd.player_id as player_id, pmd.shirt_number as number
            FROM {from_table}
            JOIN matches m ON s.match_id = m.id
            LEFT JOIN player_match_details pmd ON s.match_id = pmd.match_id AND s.player_id = pmd.player_id
            WHERE {where} {limit_sql} {filter_sql} AND s.x IS NOT NULL AND s.y IS NOT NULL
        """

        rows = conn.execute(query).fetchall()
        match_ids = set(str(r['match_id']) for r in rows if r['match_id'] is not None)
        matches_count = max(1, len(match_ids))


        shots_list = []
        for r in rows:

            shots_list.append({
                "x": r['x'] if (is_home == bool(r['was_home'])) == (type_shot == 'made') else 100 - r['x'],
                "y": r['y'] if (is_home == bool(r['was_home'])) == (type_shot == 'made') else 100 - r['y'],
                "inside_box": bool(r['inside_box']), 
                "player_name": r['player_name'], 
                "player_id": r['player_id'], 
                "number": r['number'],
                "was_home": bool(r['was_home']),
                "match_id": str(r['match_id'])
            })

        return {"shots": shots_list, "matches_count": matches_count}

    hm_made = get_shots(home_id, is_home=True, type_shot='made', limit_n=limit)
    hm_rec = get_shots(home_id, is_home=True, type_shot='received', limit_n=limit)
    am_made = get_shots(away_id, is_home=False, type_shot='made', limit_n=limit)
    am_rec = get_shots(away_id, is_home=False, type_shot='received', limit_n=limit)

    data = {
        "home_made": hm_made["shots"],
        "home_made_matches": hm_made["matches_count"],
        "home_received": hm_rec["shots"],
        "home_received_matches": hm_rec["matches_count"],
        "away_made": am_made["shots"],
        "away_made_matches": am_made["matches_count"],
        "away_received": am_rec["shots"],
        "away_received_matches": am_rec["matches_count"]
    }
    conn.close()
    return jsonify(data)


@app.route('/api/lineup/<match_id>/<team_id>')
def api_lineup(match_id, team_id):
    target_side = request.args.get('target_side')
    conn = get_db_connection()
    cards_dict = {str(r['player_id']): r['card_type'] for r in conn.execute('SELECT player_id, card_type FROM cards WHERE match_id = ?', (str(match_id),)).fetchall()}
    conn.close()
    lineup = get_lineup_data(match_id, team_id, cards_dict, target_side=target_side)
    subs = get_bench_subs(match_id, team_id, cards_dict)
    return jsonify({'lineup': lineup, 'subs': subs})

@app.route('/api/match_detail/<match_id>')
def api_match_detail(match_id):
    conn = get_db_connection()
    match = conn.execute('SELECT * FROM matches WHERE id = ?', (str(match_id),)).fetchone()
    if not match:
        conn.close()
        return jsonify({"error": "Match not found"}), 404

    cards_dict = {str(r['player_id']): r['card_type'] for r in conn.execute('SELECT player_id, card_type FROM cards WHERE match_id = ?', (str(match_id),)).fetchall()}

    h_mid = match_id if match['finished'] == 1 else get_last_finished_match_id(match['home_team_id'])
    a_mid = match_id if match['finished'] == 1 else get_last_finished_match_id(match['away_team_id'])

    home_lineup = get_lineup_data(h_mid, match['home_team_id'], cards_dict, target_side='home') if h_mid else []
    away_lineup = get_lineup_data(a_mid, match['away_team_id'], cards_dict, target_side='away') if a_mid else []
    home_subs = get_bench_subs(h_mid or match_id, match['home_team_id'], cards_dict)
    away_subs = get_bench_subs(a_mid or match_id, match['away_team_id'], cards_dict)

    match_shots = []
    if match['finished'] == 1:
        shots_rows = conn.execute('''
            SELECT s.*, COALESCE(pmd.short_name, pmd.name) as player_name 
            FROM shots s 
            LEFT JOIN player_match_details pmd ON s.player_id = pmd.player_id AND s.match_id = pmd.match_id
            WHERE s.match_id = ? 
        ''', (str(match_id),)).fetchall()
        
        for s in shots_rows:
            match_shots.append({
                "x": s['x'], "y": s['y'], 
                "blocked_x": s['blocked_x'], "blocked_y": s['blocked_y'],
                "goal_cross_x": s['goal_cross_x'], "goal_cross_y": s['goal_cross_y'], 
                "is_blocked": s['is_blocked'],
                "own_goal": 1 if s['outcome'] == 'OwnGoal' else 0,
                "outcome": s['outcome'], "shot_type": s['shot_type'],
                "situation": s['situation'], "team_id": str(s['team_id']),
                "player_name": s['player_name'] or "Desconocido",
                "on_target": s['on_target'], "minute": s['minute']
            })

    conn.close()

    return jsonify({
        "match": dict(match),
        "home_lineup": home_lineup,
        "away_lineup": away_lineup,
        "home_subs": home_subs,
        "away_subs": away_subs,
        "match_shots": match_shots
    })

@app.route('/search_players/<team_id>')
def search_players(team_id):
    q = request.args.get('q', '')
    conn = get_db_connection()
    
    # Obtenemos los IDs de los últimos 10 partidos finalizados del equipo
    last_10_rows = conn.execute('''
        SELECT id FROM matches 
        WHERE (home_team_id = ? OR away_team_id = ?) AND finished = 1 
        ORDER BY date DESC LIMIT 10
    ''', (str(team_id), str(team_id))).fetchall()
    
    last_10_ids = [str(r[0]) for r in last_10_rows]
    ids_str = ",".join([f"'{m}'" for m in last_10_ids]) if last_10_ids else "''"

    # Busca jugadores unicos ordenados por minutos jugados en los últimos 10 partidos
    players = conn.execute(f'''
        SELECT pmd.player_id, pmd.name, pmd.short_name, pmd.position,
               (SELECT pmd2.shirt_number FROM player_match_details pmd2 JOIN matches m2 ON pmd2.match_id = m2.id WHERE pmd2.player_id = pmd.player_id AND pmd2.team_id = ? AND pmd2.shirt_number IS NOT NULL ORDER BY m2.date DESC LIMIT 1) as shirt_number,
               COUNT(DISTINCT pmd.match_id) as presences,
               SUM(CASE WHEN pmd.match_id IN ({ids_str}) THEN COALESCE(pmd.minutes_played, 0) ELSE 0 END) as mins_l10
        FROM player_match_details pmd
        JOIN matches m ON pmd.match_id = m.id
        WHERE pmd.team_id = ?
          AND (pmd.name LIKE ? OR pmd.short_name LIKE ? OR pmd.shirt_number = ?)
          AND pmd.unavailable = 0
        GROUP BY pmd.player_id
        ORDER BY mins_l10 DESC, presences DESC
        LIMIT 100
    ''', (str(team_id), str(team_id), f'%{q}%', f'%{q}%', q)).fetchall()
    
    conn.close()
    res = []
    for p in players:
        d = dict(p)
        d['player_name'] = d.get('short_name') or d.get('name') or "Jugador"
        d['last_name'] = d['player_name']
        d['number'] = d['shirt_number'] or '-'
        d['id'] = str(d['player_id'])
        d['presences'] = d['presences']
        d['mins_l10'] = int(d['mins_l10'] or 0)
        res.append(d)
    return jsonify(res)

@app.route('/team/<team_id>')
def team_page(team_id):
    conn = get_db_connection()
    # Obtener nombre del equipo
    team_name = conn.execute('SELECT home_team FROM matches WHERE home_team_id = ? UNION SELECT away_team FROM matches WHERE away_team_id = ? LIMIT 1', (str(team_id), str(team_id))).fetchone()
    if not team_name: return "Equipo no encontrado", 404
    
    # Historial de partidos (Finalizados)
    matches_finished = conn.execute('''
        SELECT * FROM matches 
        WHERE (home_team_id = ? OR away_team_id = ?) AND finished = 1
        ORDER BY date DESC
    ''', (str(team_id), str(team_id))).fetchall()

    # Partidos Proximos (Pendientes)
    matches_upcoming = conn.execute('''
        SELECT * FROM matches 
        WHERE (home_team_id = ? OR away_team_id = ?) AND finished = 0
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
        f_h = conn.execute('SELECT SUM(fouls_committed) FROM player_match_details WHERE match_id=? AND team_id=?', (mid, str(m['home_team_id']))).fetchone()[0] or 0
        f_v = conn.execute('SELECT SUM(fouls_committed) FROM player_match_details WHERE match_id=? AND team_id=?', (mid, str(m['away_team_id']))).fetchone()[0] or 0
        # Tarjetas por equipo
        c_h = conn.execute("SELECT COALESCE(SUM(CASE WHEN card_type = 'Red' THEN 2 WHEN card_id IS NOT NULL THEN 1 ELSE 0 END), 0) FROM cards WHERE match_id=? AND team_id=?", (mid, str(m['home_team_id']))).fetchone()[0]
        c_v = conn.execute("SELECT COALESCE(SUM(CASE WHEN card_type = 'Red' THEN 2 WHEN card_id IS NOT NULL THEN 1 ELSE 0 END), 0) FROM cards WHERE match_id=? AND team_id=?", (mid, str(m['away_team_id']))).fetchone()[0]
        
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
    t_map = {str(r['id']): r['name'] for r in conn.execute('SELECT DISTINCT home_team_id as id, home_team as name FROM matches').fetchall()}

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