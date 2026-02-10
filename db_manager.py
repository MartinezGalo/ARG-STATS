import sqlite3

DB_NAME = "LIGA_ARG_2025.db"

def initialize_database():
    # sqlite3.connect(): Abre una conexión al archivo de la base de datos. 
    # Si el archivo no existe, lo crea automáticamente.
    connection = sqlite3.connect(DB_NAME)
    
    # connection.cursor(): Crea un objeto 'cursor' que es el encargado de enviar 
    # y ejecutar las sentencias SQL en la base de datos.
    cursor = connection.cursor()
    
    # 1. Table for general match information
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS matches (
            id TEXT PRIMARY KEY,
            date TEXT,
            finished BOOLEAN,
            tournament TEXT,
            gameweek TEXT,
            home_team TEXT,
            id_home_team TEXT,
            away_team TEXT,
            id_away_team TEXT,
            score TEXT,
            referee TEXT
        )
    ''')

    # 2. Table for player performance per match (The "Played" entity)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS player_match_details (
            match_id TEXT,
            player_id TEXT,
            team_id TEXT,
            first_name TEXT,
            last_name TEXT,
            position TEXT,
            shirt_number TEXT,
            age INTEGER,
            is_starter BOOLEAN,
            minutes_played INTEGER,
            substitution TEXT,
            sub_minute TEXT,
            rating REAL,
            role_x REAL,
            role_y REAL,
            fouls_committed INTEGER,
            fouls_received INTEGER,
            tackles INTEGER,
            offsides INTEGER,
            corners INTEGER,
            unavailable BOOLEAN,
            unavailability_reason TEXT,
            PRIMARY KEY (match_id, player_id),
            FOREIGN KEY(match_id) REFERENCES matches(id)
        )
    ''')
    
    # 3. Table for individual shots
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS shots (
            shot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT,
            player_id TEXT,
            team_id TEXT,
            minute TEXT,
            on_target BOOLEAN,
            shot_type TEXT,
            situation TEXT,
            outcome TEXT,
            x REAL,
            y REAL,
            goal_cross_x REAL,
            goal_cross_y REAL,
            blocked_x REAL,
            blocked_y REAL,
            is_blocked BOOLEAN,
            own_goal BOOLEAN,
            assist_id TEXT,
            inside_box BOOLEAN,
            FOREIGN KEY(match_id) REFERENCES matches(id),
            FOREIGN KEY(match_id, player_id) REFERENCES player_match_details(match_id, player_id)
        )
    ''')

        # 4. Tabla de tarjetas (amarillas y rojas)
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

        # Tablas de notas
    cursor.execute('CREATE TABLE IF NOT EXISTS player_notes (player_id TEXT PRIMARY KEY, notes TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS match_notes (match_id TEXT PRIMARY KEY, notes TEXT)')
            


    cursor.execute('CREATE INDEX IF NOT EXISTS idx_pmd_player_match ON player_match_details (player_id, match_id);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_pmd_team ON player_match_details (team_id);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_shots_player_match ON shots (player_id, match_id);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_matches_date ON matches (date DESC);')

    # Indices de optimizacion basados en el uso de app.py
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_matches_referee ON matches (referee, finished, date DESC);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_matches_home_context ON matches (id_home_team, finished, date DESC);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_matches_away_context ON matches (id_away_team, finished, date DESC);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_matches_gameweek ON matches (gameweek, tournament);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_shots_match_id ON shots (match_id);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_cards_match_id ON cards (match_id);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_pmd_match_team_starter ON player_match_details (match_id, team_id, is_starter);')

    # Views creation
    cursor.execute('CREATE VIEW IF NOT EXISTS goals AS SELECT * FROM shots WHERE outcome = "Goal"')
    cursor.execute('CREATE VIEW IF NOT EXISTS shots_on_target AS SELECT * FROM shots WHERE on_target = 1 AND own_goal = 0')
    cursor.execute('''
        CREATE VIEW IF NOT EXISTS shots_received AS 
        SELECT s.*, 
               CASE WHEN s.team_id = m.id_home_team THEN m.id_away_team ELSE m.id_home_team END as against_team_id
        FROM shots s
        JOIN matches m ON s.match_id = m.id
        WHERE s.own_goal = 0
    ''')
    # connection.commit(): Guarda permanentemente todos los cambios realizados por el cursor.
    connection.commit()
    
    # connection.close(): Cierra la conexión para liberar recursos del sistema.
    connection.close()

    print("✅ Base de datos inicializada correctamente.")
    print("   - Tabla 'matches' creada.")
    print("   - Tabla 'player_match_details' creada.")
    print("   - Tabla 'shots' creada.")
    print("   - Tabla 'cards' creada.")
if __name__ == "__main__":
    initialize_database()