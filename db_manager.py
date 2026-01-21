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
            player_name TEXT,
            position TEXT,
            shirt_number TEXT,
            is_starter BOOLEAN,
            minutes_played INTEGER,
            rating REAL,
            role_x REAL,
            role_y REAL,
            fouls_committed INTEGER,
            fouls_received INTEGER, -- Scalability: added a stat here
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
            player_name TEXT,
            team_id TEXT,
            minute TEXT,
            on_target BOOLEAN,
            shot_type TEXT,
            situation TEXT,
            outcome TEXT,
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
            player_name TEXT,
            team_id TEXT,
            card_type TEXT, -- Yellow, Red, YellowRed
            minute TEXT,
            FOREIGN KEY(match_id) REFERENCES matches(id),
            FOREIGN KEY(match_id, player_id) REFERENCES player_match_details(match_id, player_id)
        )
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_pmd_player_match ON player_match_details (player_id, match_id);
        CREATE INDEX IF NOT EXISTS idx_pmd_team ON player_match_details (team_id);
        CREATE INDEX IF NOT EXISTS idx_shots_player_match ON shots (player_id, match_id);
        CREATE INDEX IF NOT EXISTS idx_matches_date ON matches (date DESC);''')

    # connection.commit(): Guarda permanentemente todos los cambios realizados por el cursor.
    connection.commit()
    
    # connection.close(): Cierra la conexión para liberar recursos del sistema.
    connection.close()

    print("✅ Base de datos inicializada correctamente.")
    print("   - Tabla 'matches' creada.")
    print("   - Tabla 'player_match_details' creada.")
    print("   - Tabla 'shots' creada.")
    print("   - Tabla 'cards' creada.")