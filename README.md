# ⚽ ARGSTATS — Plataforma de Análisis Estadístico del Fútbol Argentino

**ARGSTATS** es una plataforma web completa de analítica deportiva enfocada en la Liga Profesional de Fútbol Argentino. Integra una base de datos relacional optimizada (SQLite) y una interfaz web interactiva desarrollada en Flask.

---

## 📊 Fuente de Datos

Toda la información estadística, detalles de jugadores, formaciones y eventos de partidos han sido obtenidos y procesados a partir de los datos públicos de **[Sofascore](https://www.sofascore.com)**.

---

## Características Principales

### 1. 📊 Vista de Partidos e Interactividad Avanzada
* **Pizarra Interactiva:** Drag & Drop de jugadores, selección múltiple (Lasso), y guardado de posiciones.
* **Alineaciones y Esquemas Tácticos:** Visualización de formaciones y posicionamiento táctico en cancha con coordenadas relativas.
* **Mapas de Tiros (Shotmaps):** Representación gráfica de remates (al arco, fuera del área, bloqueados, goles).
* **Mapas de Calor (Heatmaps):** Visualización interactiva del mapa de calor individual de cada jugador por partido y por temporada.
* **Notas de Scouting:** Sistema de anotaciones y notas privadas para análisis de partidos persistidas en base de datos.

### 2. 👤 Perfiles y Rendimiento de Jugadores
* **Ficha Técnica y Métricas Clave:** Minutos jugados, entradas, faltas cometidas y recibidas, fueras de juego, etc.
* **Modal Interactivo de Jugador:** Consulta rápida del historial de partidos y mapa de calor de cualquier jugador.
* **Notas de Jugador:** Cuaderno de scouting personalizado por futbolista.

### 3. 🛡️ Análisis por Equipos
* **Estadísticas Colectivas:** Estadisticas a favor vs. en contra, goles, tarjetas y tendencias.
* **Planteles Completos:** Listado de jugadores con filtrado por posición y estadísticas individuales acumuladas.
* **Rankings de Rendimiento:** Comparativa del equipo frente al promedio de la liga.

### 4. 🟨 Estadísticas de Árbitros
* **Perfil por Colegiado:** Historial de partidos dirigidos en el torneo.
* **Promedios Disciplinarios:** Frecuencia de tarjetas amarillas, rojas y faltas pitadas por partido.
* **Equipos más amonestados:** Identificación de patrones y equipos con mayor cantidad de sanciones recibidas.

---

## 🌐 Demo Online

Explora la aplicación en producción aquí:
🔗 **[arg-stats.up.railway.app](https://arg-stats.up.railway.app)**

---

## 🛠️ Stack Tecnológico

* **Backend:** Python 3.11, [Flask](https://flask.palletsprojects.com/)
* **Servidor WSGI:** [Gunicorn](https://gunicorn.org/) (Multithread)
* **Base de Datos:** SQLite3 con vistas personalizadas e índices optimizados
* **Procesamiento de Datos:** [Pandas](https://pandas.pydata.org/)
* **Networking / Scraping:** [Camoufox](https://github.com/daijro/camoufox) (Firefox Anti-Detect), Playwright, Requests, BeautifulSoup4
* **Frontend:** HTML5 Semántico, CSS3, JavaScript Vanilla, Jinja2 Templates
* **CI/CD & Automatización:** GitHub Actions (`auto_update.yml`)

---

## 📂 Estructura del Proyecto

```text
├── app.py                  # Aplicación principal Flask (Rutas, controladores y lógica web)
├── api.py                  # Script de actualización e ingesta incremental de partidos
├── sofa_request.py         # Módulo de red con Camoufox (Playwright) para Sofascore
├── data.py                 # Transformación y carga de datos crudos hacia SQLite
├── ARGSTATS.db             # Base de datos SQLite relacional
├── requirements.txt        # Dependencias del proyecto
├── Procfile                # Comando de inicio para servidores en producción (Gunicorn)
├── auto_update.yml         # Workflow de GitHub Actions para sincronización diaria
├── templates/              # Vistas HTML (Jinja2)
│   ├── base.html           # Layout base
│   ├── index.html          # Dashboard principal y fixture
│   ├── match.html          # Vista de partido (Shotmaps, Heatmaps, Alineaciones)
│   ├── team.html           # Perfil de equipo y plantilla
│   ├── stats.html          # Tablas de estadísticas generales
│   ├── referee.html        # Análisis de árbitros
│   └── player_modal.html   # Modal interactivo de jugador
└── static/                 # Recursos estáticos (CSS, JS, imágenes, escudos)
```

---

## 💻 Instalación y Ejecución Local

### 1. Clonar el repositorio
```bash
git clone https://github.com/MartinezGalo/ARG-STATS
cd sofascore
```

### 2. Crear y activar entorno virtual
```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux / macOS
python3 -m venv venv
source venv/bin/activate
```

### 3. Instalar dependencias
```bash
pip install -r requirements.txt
python -m camoufox fetch
```

### 4. Iniciar la aplicación en modo desarrollo
```bash
python app.py
```
Abrí tu navegador en `http://127.0.0.1:5001`.

---

## 🔄 Actualización Manual de Datos

Para actualizar la base de datos con los últimos partidos disputados, alineaciones y mapas de calor:
```bash
python api.py
```
Los logs del proceso se registrarán tanto en la consola como en `update_log.txt`.

---

## 👥 Autores

- **MartinezGalo**
- **francoqdev** 
---

## 📌 Estado del Proyecto
Proyecto en **desarrollo activo**.