# ⚽ ARG STATS — Advance Football Analytics System

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-2.0+-black.svg?style=for-the-badge&logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![Tailwind CSS](https://img.shields.io/badge/Tailwind_CSS-3.0+-38B2AC.svg?style=for-the-badge&logo=tailwind-css&logoColor=white)](https://tailwindcss.com/)
[![SQLite](https://img.shields.io/badge/SQLite-3.0+-003B57.svg?style=for-the-badge&logo=sqlite&logoColor=white)](https://www.sqlite.org/)

**ARG STATS** es una plataforma **Full-Stack** de analítica avanzada orientada al **análisis táctico** y la **predicción de escenarios estadísticos** en el fútbol argentino. Diseñada para analistas y entusiastas, la herramienta transforma datos crudos en inteligencia accionable a través de una interfaz moderna y reactiva.

---

## 📊 Fuente de Datos

Toda la información estadística, detalles de jugadores, formaciones y eventos de partidos han sido obtenidos y procesados a partir de los datos públicos de **[FotMob](https://www.fotmob.com)**.

---

## 🧠 Core: Intelligence & Prediction

A diferencia de los dashboards convencionales, **ARG STATS** implementa lógica de normalización y cruce de variables:

- **Normalización p90 Real:** Todas las métricas de jugadores se calculan en base a minutos jugados efectivos, eliminando el sesgo de jugadores con pocos minutos y métricas infladas.
- **Motor de Predicción Contextual:** Cruza los rankings de ataque del local contra la defensa del visitante (y viceversa), ajustando los resultados según la **rigurosidad histórica del árbitro** designado para el encuentro.
- **Análisis de Tendencias:** Capacidad de filtrar por "Últimos 5 Partidos" para detectar picos de forma, rachas y variaciones tácticas recientes.

---

## 🚀 Funcionalidades Principales

### 1. Match Intelligence & Pizarra Táctica
- **Pizarra Interactiva:** Drag & Drop de jugadores, selección múltiple (Lasso), y guardado de posiciones.
- **Último XI Real:** Carga automática de la última formación confirmada para partidos pendientes.
- **Notas de Scouting:** Persistencia de análisis táctico por partido y por jugador.

### 2. Perfil de Jugador 360°
- **Multicapa:** Visualización de stats del partido, últimos 5 y acumulado de temporada.
- **Rankings Relativos:** Posición del jugador en el Top 20 a nivel liga, equipo y posición específica.
- **Historial de Clubes:** Seguimiento automático de transferencias y equipos anteriores.

### 3. Análisis Disciplinario (Árbitros)
- **Perfil de Rigurosidad:** Promedio de tarjetas y faltas vs promedio de la liga.
- **Top Targets:** Identificación de los equipos más castigados por cada colegiado.

---

## 🌐 Demo Online

Explora la aplicación en producción aquí:
🔗 **[arg-stats.onrender.com](https://arg-stats.onrender.com)**

*(Desplegado en Render. Puede tardar unos segundos en iniciar si la instancia está inactiva).*

---

## 🛠️ Stack Tecnológico

- **Backend:** Python + Flask (Monolito optimizado para alta concurrencia de lectura).
- **Database:** SQLite con indexación estratégica para subconsultas complejas de ordenamiento temporal.
- **Frontend:** JavaScript (ES6+) Vanilla + Tailwind CSS para una experiencia UI/UX fluida y sin dependencias pesadas.
- **Visuals:** Renderizado dinámico de escudos y posiciones mediante coordenadas normalizadas.

---

## ⚙️ Instalación y Uso Local

1. **Clonar el repositorio:**
   ```bash
   git clone https://github.com/MartinezGalo/ARG-STATS.git
   cd ARG-STATS
   ```

2. **Instalar dependencias:**
   ```bash
   pip install flask
   ```

3. **Ejecutar la aplicación:**
   ```bash
   python app.py
   ```

4. **Acceder:**
   Abre [http://127.0.0.1:5001](http://127.0.0.1:5001) en tu navegador.

---

## 👥 Autores

- **MartinezGalo** — Arquitectura de Datos, Backend & Algoritmos Predictivos.
- **francoqdev** — Frontend, Experiencia de Usuario & Diseño Visual.

---

## 📌 Estado del Proyecto
Proyecto en **desarrollo activo**.