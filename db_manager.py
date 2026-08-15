import sqlite3

DB_NAME = "ARGSTATS.db"

def initialize_database():
    # sqlite3.connect(): Abre una conexión al archivo de la base de datos. 
    # Si el archivo no existe, lo crea automáticamente.
    connection = sqlite3.connect(DB_NAME)
    
    # connection.cursor(): Crea un objeto 'cursor' que es el encargado de enviar 
    # y ejecutar las sentencias SQL en la base de datos.
    cursor = connection.cursor()
    
    # Table for general match information
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS matches (
            id TEXT PRIMARY KEY,
            date TEXT,
            finished BOOLEAN,
            cancelled BOOLEAN,
            tournament TEXT,
            gameweek TEXT,
            score TEXT,
            home_team_id TEXT,
            home_team TEXT,
            home_team_formation TEXT,
            away_team_id TEXT,
            away_team TEXT,
            away_team_formation TEXT,
            referee_id TEXT,
            referee TEXT
        )
    ''')

    # Table for player performance per match (The "Played" entity)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS player_match_details (
            match_id TEXT,
            player_id TEXT,
            team_id TEXT,
            name TEXT,
            short_name TEXT,
            position TEXT,
            shirt_number TEXT,
            age INTEGER,
            is_starter BOOLEAN,
            minutes_played INTEGER,
            rating REAL,
            role_x REAL,
            role_y REAL,
            fouls_committed INTEGER,
            fouls_received INTEGER,
            tackles INTEGER,
            offsides INTEGER,
            unavailable BOOLEAN,
            unavailability_reason TEXT,
            PRIMARY KEY (match_id, player_id),
            FOREIGN KEY(match_id) REFERENCES matches(id)
        )
    ''')
    
    # Table for individual shots
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS shots (
            shot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT,
            player_id TEXT,
            team_id TEXT,
            is_home_team BOOLEAN,
            minute TEXT,
            on_target BOOLEAN,
            shot_type TEXT,
            inside_box BOOLEAN,
            situation TEXT,
            outcome TEXT,
            x REAL,
            y REAL,
            goal_cross_x REAL,
            goal_cross_y REAL,
            blocked_x REAL,
            blocked_y REAL,
            is_blocked BOOLEAN,
            FOREIGN KEY(match_id) REFERENCES matches(id),
            FOREIGN KEY(match_id, player_id) REFERENCES player_match_details(match_id, player_id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS goals (
            goal_id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT,
            player_id TEXT,
            team_id TEXT,
            minute TEXT,
            situation TEXT,
            is_own_goal BOOLEAN,
            assist_id TEXT,
            FOREIGN KEY(match_id) REFERENCES matches(id),
            FOREIGN KEY(match_id, player_id) REFERENCES player_match_details(match_id, player_id)
        )
    ''')

        #Tabla de tarjetas (amarillas y rojas)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cards (
            card_id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT,
            player_id TEXT,
            team_id TEXT,
            card_type TEXT, -- Yellow, Red, YellowRed
            minute TEXT,
            FOREIGN KEY(match_id) REFERENCES matches(id),
            FOREIGN KEY(match_id, player_id) REFERENCES player_match_details(match_id, player_id)
        )
    ''')

    #tabla de sustituciones
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS substitutions (
            match_id TEXT,
            player_out_id TEXT,
            player_in_id TEXT,
            team_id TEXT,
            minute TEXT,
            injury BOOLEAN,
            PRIMARY KEY (match_id, player_out_id, player_in_id),
            FOREIGN KEY(match_id) REFERENCES matches(id),
            FOREIGN KEY(match_id, player_out_id) REFERENCES player_match_details(match_id, player_id)
        )
    ''')

    #tabla de heatmaps de jugadores
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS player_heatmap_points (
            player_id TEXT,
            match_id TEXT,
            team_id TEXT,
            x REAL,
            y REAL,
            count INTEGER,
            PRIMARY KEY (player_id, match_id, x, y),
            FOREIGN KEY(match_id) REFERENCES matches(id),
            FOREIGN KEY(player_id) REFERENCES player_match_details(player_id)
        )
    ''')

        # Tablas de notas
    cursor.execute('CREATE TABLE IF NOT EXISTS player_notes (player_id TEXT PRIMARY KEY, notes TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS match_notes (match_id TEXT PRIMARY KEY, notes TEXT)')
            


    cursor.execute('CREATE INDEX IF NOT EXISTS idx_pmd_player_match ON player_match_details (player_id, match_id);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_pmd_team ON player_match_details (team_id);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_shots_player_match ON shots (player_id, match_id);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_matches_date ON matches (date DESC);')

    # Indices de optimizacion basados en el uso de app.py
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_matches_referee ON matches (referee, finished, date DESC);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_matches_home_context ON matches (home_team_id, finished, date DESC);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_matches_away_context ON matches (away_team_id, finished, date DESC);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_matches_gameweek ON matches (gameweek, tournament);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_shots_match_id ON shots (match_id);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_cards_match_id ON cards (match_id);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_pmd_match_team_starter ON player_match_details (match_id, team_id, is_starter);')

    # Views creation
    cursor.execute('DROP VIEW IF EXISTS shots_on_target')
    cursor.execute('DROP VIEW IF EXISTS shots_outside_box')
    cursor.execute('DROP VIEW IF EXISTS headers')
    cursor.execute('DROP VIEW IF EXISTS shots_goals')
    cursor.execute('DROP VIEW IF EXISTS shots_corners')
    cursor.execute('DROP VIEW IF EXISTS shots_received')

    cursor.execute('CREATE VIEW IF NOT EXISTS shots_on_target AS SELECT * FROM shots WHERE on_target = 1')
    cursor.execute('CREATE VIEW IF NOT EXISTS shots_outside_box AS SELECT * FROM shots WHERE inside_box = 0')
    cursor.execute('CREATE VIEW IF NOT EXISTS headers AS SELECT * FROM shots WHERE LOWER(shot_type) = "head"')
    cursor.execute('''
        CREATE VIEW IF NOT EXISTS shots_received AS 
        SELECT s.*, 
               CASE WHEN s.team_id = m.home_team_id THEN m.away_team_id ELSE m.home_team_id END as against_team_id
        FROM shots s
        JOIN matches m ON s.match_id = m.id
    ''')

    # connection.commit(): Guarda permanentemente todos los cambios realizados por el cursor.
    connection.commit()
    
    # connection.close(): Cierra la conexión para liberar recursos del sistema.
    connection.close()

    print("[OK] Base de datos inicializada correctamente.")
    print("   - Tabla 'matches' creada.")
    print("   - Tabla 'player_match_details' creada.")
    print("   - Tabla 'shots' creada.")
    print("   - Tabla 'cards' creada.")
if __name__ == "__main__":
    initialize_database()