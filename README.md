# ⚽ ARG STATS — Sistema de Scouting y Analítica de Fútbol Pro

**ARG STATS** es una plataforma **Full‑Stack** de analítica avanzada orientada al **scouting profesional**, el **análisis táctico** y la **predicción de eventos** en el fútbol argentino. Está diseñada para trabajar directamente sobre **datos reales de partidos**, priorizando consistencia estadística, comparabilidad y rendimiento.

La aplicación integra un **motor predictivo**, una **pizarra táctica interactiva**, rankings normalizados y perfiles 360° de jugadores, equipos y árbitros.

---

## 🧠 ¿Qué hace diferente a ARG STATS?

- Procesa **datos históricos reales** (partidos, eventos y actas) en SQLite optimizado.
- Evita métricas infladas usando **suelo de minutos** y normalización **p90**.
- Cruza **ataque + defensa + árbitro** para generar predicciones probabilísticas.
- Unifica **scouting visual (pizarra)** con **analítica cuantitativa**.
- Pensado para uso **real de analistas**, no solo dashboards.

---

🌐 Demo Online

La aplicación puede previsualizarse en producción en el siguiente enlace:

🔗 https://arg-stats.onrender.com

El despliegue se realiza en Render y puede tardar unos segundos en iniciar si la instancia está en reposo.

---

## 🚀 Funcionalidades Clave


### 1. Match Intelligence & Pizarra Táctica

- **Motor Predictivo Propio**
  - Cruza rankings ofensivos y defensivos de ambos equipos.
  - Ajusta el output según la **rigurosidad histórica del árbitro** (tarjetas y faltas).

- **Pizarra Táctica Interactiva**
  - Drag & Drop de jugadores titulares.
  - Posiciones normalizadas (escala 0–1).
  - Visualización del **último XI real** si el partido no se jugó.

- **Interacción Avanzada**
  - Selección múltiple (Lasso Select).
  - Sustituciones dinámicas con buscador.
  - Persistencia de notas tácticas por partido.

---

### 2. Engine de Estadísticas Avanzadas

- **Normalización p90**
  - Todas las métricas de jugadores se ajustan por minutos jugados.

- **Filtro de Ultimos 5 Partidos**
  - Rankings basados solo en los últimos N partidos por equipo.
  - Detección de rachas, picos de forma y caídas de rendimiento.

- **Ordenación Inteligente**
  - Algoritmo de suelo de minutos:
    - >300 min (liga)
    - >150 min (last‑matches)

---

### 3. Perfil de Jugador 360°

Modal dinámico con:

- **Estadísticas Multicapa**
  - Partido actual
  - Últimos 5 partidos
  - Total histórico

- **Rankings Contextuales (Top 20)**
  - A nivel:
    - Liga
    - Equipo
    - Posición

- **Transfer Tracker Automático**
  - Detección de cambio de club (`is_transferred`) si el último partido fue en otro equipo.

- **Notas de Scouting Persistentes**
  - Guardadas por jugador en base de datos.

---

### 4. Análisis de Equipos

- Historial completo de partidos.
- Rankings globales:
  - Ataque vs Defensa
  - Totales y recibidos
- Comparativas claras por categoría.

---

### 5. Análisis de Árbitros

- **Perfiles Disciplinarios**
  - Promedios reales por partido.
  - Rankings globales en tarjetas y faltas.

- **Top Targets**
  - Equipos más castigados por cada árbitro.

---

## 🔮 Motor Predictivo (Cómo Funciona)

El predictor transforma rankings en **probabilidades relativas (0–100)**:

- Ataque propio (ranking a favor)
- Defensa rival (ranking en contra)
- Ajuste por árbitro (tarjetas / faltas)

Esto permite:
- Comparar partidos heterogéneos.
- Detectar contextos de alto volumen de eventos.

> No es una predicción de marcador, sino de **escenario estadístico**.

---

## 🛠️ Stack Tecnológico

- **Backend**: Python + Flask (monolito optimizado)
- **Base de Datos**: SQLite
  - Índices estratégicos
  - Subqueries y agregaciones controladas
- **Frontend**: Tailwind CSS + JavaScript (ES6)
- **Templates**: Jinja2 embebido
- **Deploy**: Render (compatible out‑of‑the‑box)

---

## 🧠 Soluciones Técnicas Destacadas

- **CTEs & Subqueries Controladas**
  - Evita N+1 queries en rankings complejos.

- **Window‑like Logic**
  - Última camiseta, posición y equipo vía subconsultas ordenadas por fecha.

- **Integridad Estadística**
  - Partidos jugados (PJ) calculados desde actas reales.
  - No se infieren PJ desde eventos.

- **Escalabilidad Lógica**
  - API preparada para separar frontend / backend.

---

## 🔌 Endpoints Principales

### API

- `/api/team_stats`
- `/api/player_stats`
- `/api/team_ranking/<team_id>`
- `/api/match_prediction/<match_id>`

### Vistas

- `/` — Centro de Monitoreo
- `/stats` — Estadísticas de Liga
- `/match/<id>` — Match Intelligence
- `/team/<id>` — Perfil de Equipo
- `/referee/<name>` — Perfil de Árbitro

---

## ⚙️ Instalación Local

```bash
git clone https://github.com/MartinezGalo/ARG-STATS.git
cd ARG-STATS
pip install flask
python app.py
```

Abrir en el navegador:

```
http://127.0.0.1:5001
```

---

## 👥 Autores

- **MartinezGalo** — Arquitectura, backend, analítica
- **francoqdev** — Frontend, UI y experiencia visual

---

## 📌 Estado del Proyecto

En desarrollo activo.




