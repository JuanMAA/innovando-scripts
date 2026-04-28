# innovando-scripts — Plan de trabajo
**Python 3.11 · Supabase · Playwright**
Versión 8.0 · Abril 2025

---

## Descripción

Scripts Python que manejan el lado operativo de Innovando: scraping, análisis de presencia digital, extracción de emails y links, generación de notas, análisis de huella digital y outreach. Soporte completo de precios dinámicos por país y ambientes test/prd.

---

## Ambientes

```bash
python maps_scraper.py --env test --ciudad "Ancud, Chile"
python maps_scraper.py --env prd  --ciudad "Ancud, Chile"
```

```
.env.test    ← Supabase innovando-test (no subir a git)
.env.prd     ← Supabase innovando-prd  (no subir a git)
.env.example ← plantilla sin valores   (sí subir a git)
```

---

## Stack

| Componente | Tecnología |
|---|---|
| Lenguaje | Python 3.11+ |
| Scraping | Playwright (headless Chromium) |
| HTTP | requests |
| Data | pandas |
| Base de datos | supabase-py |
| Emails | SendGrid |
| WhatsApp | Twilio |
| Google Places | Google Places API |

---

## Estructura de archivos

```
innovando-scripts/
├── supabase_client.py          # Cliente Supabase compartido
├── links_manager.py            # Gestión centralizada de links
├── refresh_manager.py          # Control de fechas de actualización
├── notas_generator.py          # Generación automática de notas
├── precios_manager.py          # Consulta de precios por país + categoría
├── maps_scraper.py             # Scraping Google Places → Supabase
├── scorer_web.py               # Análisis web + emails + links
├── scorer_redes.py             # Instagram, Facebook
├── scorer_plataformas.py       # Booking, Airbnb, TripAdvisor, Expedia, Despegar
├── scorer_huella.py            # Análisis de huella digital ⭐
├── outreach_email.py           # Emails + WhatsApp
├── requirements.txt
├── .env.test
├── .env.prd
├── .env.example
└── .gitignore
```

---

## Descripción de cada script

### `supabase_client.py`
Cliente compartido con soporte de ambientes.

**Funciones:**
- `upsert_negocio(data)`
- `update_negocio(place_id, fields)`
- `get_leads_por_estado(estado)`
- `get_leads_desactualizados(modulo, dias)`
- `get_problemas_pendientes()`
- `marcar_problema_resuelto(problema_id, nota_interna)`
- `registrar_outreach(negocio_id, tipo, estado, metadata)`
- `get_precio(pais, categoria, servicio)` — consulta tabla precios_por_pais

---

### `links_manager.py`
Módulo centralizado para registrar y verificar links.

**Tipos de links:**

| Tipo | Script | Dato asociado |
|---|---|---|
| `google_maps` | maps_scraper | ficha, calificacion |
| `sitio_web` | scorer_web | velocidad, ssl, mobile |
| `subpagina_web` | scorer_web | contacto, reservas |
| `instagram` | scorer_redes | perfil, bio, engagement |
| `facebook` | scorer_redes | perfil, info |
| `noticia` | scorer_web / scorer_huella | mención en medios |
| `booking` | scorer_plataformas | rating, reseñas, precio |
| `airbnb` | scorer_plataformas | rating, precio, superhost |
| `tripadvisor` | scorer_plataformas | rating, reseñas, excelencia |
| `expedia` | scorer_plataformas | rating, reseñas |
| `despegar` | scorer_plataformas | rating, reseñas |
| `google_search` | scorer_huella | posición en resultados |
| `bing_search` | scorer_huella | posición en resultados |

---

### `refresh_manager.py`
Controla cuándo cada módulo necesita actualizarse.

**Módulos soportados:**
- `maps`, `web`, `redes`, `plataformas`, `huella`

**Flags disponibles:**
```bash
python scorer_huella.py --env prd --solo-desactualizados
python scorer_huella.py --env prd --forzar
python scorer_huella.py --env prd --slug hostal-vista-al-mar
```

**Frecuencias en `.env`:**
```env
REFRESH_MAPS_DIAS=30
REFRESH_WEB_DIAS=30
REFRESH_REDES_DIAS=30
REFRESH_PLATAFORMAS_DIAS=30
REFRESH_HUELLA_DIAS=30
```

---

### `notas_generator.py`
Genera notas personalizadas para el reporte y el informe de huella.

**Notas generadas:**

| Campo | Generado por | Considera |
|---|---|---|
| `nota_general` | maps_scraper | Score vs promedio ciudad, temporada |
| `nota_p2a` | maps_scraper | Fotos, horarios, descripción |
| `nota_p2b` | scorer_web | SSL, mobile, velocidad |
| `nota_p2c` | maps_scraper | Reseñas, calificación |
| `nota_p2d` | scorer_redes | Instagram, Facebook |
| `nota_p2f` | scorer_plataformas | Plataformas detectadas |
| `nota_huella` | scorer_huella | Huella global, buscadores, fotos |

**Regla:** si `nota_*_editada = true`, el script NO sobreescribe.

---

### `precios_manager.py`
Consulta los precios configurados por país y categoría desde Supabase.

```python
from precios_manager import get_precio

precio = get_precio(
    pais="Chile",
    categoria="hotel",
    servicio="reporte_completo",
    env="prd"
)
# → {"precio": 20000, "moneda": "CLP", "display": "$20.000 CLP"}
```

**Lógica de búsqueda:**
1. Busca precio específico: `pais + categoria + servicio`
2. Si no existe → busca precio genérico: `pais + "todos" + servicio`
3. Si no existe → usa precio base en USD desde `.env`

**Precios base USD en `.env` (fallback):**
```env
PRECIO_BASE_REPORTE_USD=20
PRECIO_BASE_HUELLA_USD=29
PRECIO_BASE_TUTORIAL_USD=3
PRECIO_BASE_SITIO_USD=149
```

**Uso en outreach:** el email de venta muestra el precio en moneda local del lead.

---

### `maps_scraper.py`
Busca negocios en Google Places. Calcula scores P2a y P2c. Genera notas.

**Input:** ciudad + categorías + ambiente
**Output:** leads en `negocios` + links + notas → estado: `nuevo`

**Calcula:** score P2a, P2c, `diagnostico_venta`
**Genera:** `nota_general`, `nota_p2a`, `nota_p2c`
**Actualiza:** `fecha_analisis_maps`

---

### `scorer_web.py`
Analiza sitio web. Extrae emails y links. Genera nota P2b.

**Input:** leads con estado `nuevo` + desactualizados
**Output:** scores + links + nota → estado: `analizado_web`

**Estrategias de email:**
1. Página principal + subpáginas de contacto
2. Bio Instagram / Info Facebook
3. Google Search

**Calcula:** score P2b
**Genera:** `nota_p2b`
**Actualiza:** `fecha_analisis_web`

---

### `scorer_redes.py`
Detecta y analiza redes sociales. Genera nota P2d.

**Input:** leads con estado `analizado_web` + desactualizados
**Output:** scores + links + nota → estado: `analizado_redes`

**Calcula:** score P2d (Instagram, Facebook — seguidores, engagement, frecuencia)
**Genera:** `nota_p2d`
**Actualiza:** `fecha_analisis_redes`

---

### `scorer_plataformas.py`
Analiza plataformas de alojamiento. Mayor peso (P2f · 25%).

**Arquitectura extensible:**
```python
PLATAFORMAS = {
    "booking":     BookingScraper,
    "airbnb":      AirbnbScraper,
    "tripadvisor": TripAdvisorScraper,
    "expedia":     ExpediaScraper,
    "despegar":    DespegarScraper,
}
```

**Score P2f (0–25 pts):**
```
En al menos 1 plataforma:      5 pts
En 3 o más plataformas:       10 pts
Rating promedio ≥ 4.0:         5 pts
10+ reseñas en total:          5 pts
5+ fotos por plataforma:       5 pts
```

**Calcula:** score P2f, `plataformas_count`
**Genera:** `nota_p2f`
**Actualiza:** `fecha_analisis_plataformas`

---

### `scorer_huella.py` ⭐
Analiza la huella digital completa del negocio. Nuevo script.

**Input:** leads con estado `analizado` + desactualizados
**Output:** datos de huella guardados en Supabase → estado: `analizado_huella`

**4 módulos de análisis:**

**1. Reseñas consolidadas**
- Consolida ratings de Google Maps + Booking + TripAdvisor
- Calcula rating global promedio ponderado
- Detecta tendencia (mejorando / estable / bajando)
- Extrae palabras más repetidas en reseñas positivas y negativas

**2. Presencia en redes sociales**
- Seguidores en Instagram y Facebook
- Engagement estimado
- Frecuencia de publicación
- Última publicación (cuántos días hace)

**3. Aparición en buscadores**
```python
BUSCADORES = {
    "google":  GoogleSearchScraper,    # ¿Aparece en página 1?
    "bing":    BingSearchScraper,      # ¿Aparece en primeras 3 páginas?
    "chatgpt": ChatGPTSearchScraper,   # ¿Aparece al buscar "hostales en {ciudad}"?
}
```

**4. Fotos e imágenes públicas**
- Cuenta fotos indexadas por plataforma
- Detecta antigüedad de las fotos más recientes
- Estima calidad (resolución cuando es posible)

**Score de huella digital (0–100 pts):**
```
Reseñas consolidadas:    30 pts
  Rating global ≥ 4.0:  15 pts
  20+ reseñas total:    10 pts
  Tendencia positiva:    5 pts

Redes sociales:          20 pts
  Activo en Instagram:  10 pts
  Activo en Facebook:   10 pts

Buscadores:              30 pts
  Aparece en Google:    15 pts
  Aparece en Bing:       5 pts
  Aparece en ChatGPT:   10 pts

Fotos:                   20 pts
  20+ fotos totales:    10 pts
  Fotos recientes:      10 pts
```

**Genera:** `nota_huella`, `score_huella`
**Actualiza:** `fecha_analisis_huella`

---

### `outreach_email.py`
Emails personalizados + WhatsApp. Incluye precio en moneda local.

**Secuencia:**
1. Email teaser — score + nota + link reporte + precio local → `contactado`
2. Email seguimiento 1 (3 días)
3. WhatsApp via Twilio (7 días)
4. Email final — urgencia de temporada (10 días)

**Email con precio en moneda local:**
```
"Preparamos un reporte completo de auditoría para
Hostal Vista al Mar. Podés acceder por solo $20.000 CLP."
```

---

## Schema Supabase completo

### Tabla `negocios` — campos nuevos

```sql
-- Huella digital
score_huella                int default 0,
fecha_analisis_huella       timestamptz,
refresh_huella_dias         int default 30,

-- Reseñas consolidadas (scorer_huella)
huella_rating_global        float,
huella_total_resenas        int,
huella_tendencia            text,    -- mejorando | estable | bajando
huella_palabras_positivas   jsonb,   -- ["limpio", "amable", "ubicación"]
huella_palabras_negativas   jsonb,   -- ["wifi", "ruido", "precio"]

-- Buscadores (scorer_huella)
huella_google_pagina1       boolean,
huella_bing_pagina1_3       boolean,
huella_chatgpt_mencionado   boolean,

-- Fotos consolidadas (scorer_huella)
huella_total_fotos          int,
huella_fotos_recientes      boolean,

-- Notas huella
nota_huella                 text,
nota_huella_editada         boolean default false,

-- Problema reportado
tiene_problema_reportado    boolean default false,
```

### Tabla `negocios` — schema completo

```sql
create table negocios (
  id                          uuid primary key default gen_random_uuid(),
  place_id                    text unique not null,
  slug                        text unique not null,
  nombre                      text not null,
  categoria                   text,
  ciudad                      text,
  pais                        text default 'Chile',
  telefono                    text,
  email                       text,
  direccion                   text,
  website_propio              text,
  calificacion                float,
  num_resenas                 int default 0,
  num_fotos                   int default 0,
  tiene_horarios              boolean default false,
  tiene_descripcion           boolean default false,
  latitud                     float,
  longitud                    float,

  -- Scores módulos reporte
  score_maps                  int default 0,
  score_web                   int default 0,
  score_reputacion            int default 0,
  score_redes                 int default 0,
  score_seo                   int default 0,
  score_plataformas           int default 0,
  score_total                 int default 0,

  -- Score huella digital
  score_huella                int default 0,

  -- Fechas de actualización
  fecha_analisis_maps         timestamptz,
  fecha_analisis_web          timestamptz,
  fecha_analisis_redes        timestamptz,
  fecha_analisis_plataformas  timestamptz,
  fecha_analisis_seo          timestamptz,
  fecha_analisis_huella       timestamptz,

  -- Frecuencias de refresco
  refresh_maps_dias           int default 30,
  refresh_web_dias            int default 30,
  refresh_redes_dias          int default 30,
  refresh_plataformas_dias    int default 30,
  refresh_huella_dias         int default 30,

  -- Notas del reporte
  nota_general                text,
  nota_p2a                    text,
  nota_p2b                    text,
  nota_p2c                    text,
  nota_p2d                    text,
  nota_p2e                    text,
  nota_p2f                    text,
  nota_huella                 text,
  nota_general_editada        boolean default false,
  nota_p2a_editada            boolean default false,
  nota_p2b_editada            boolean default false,
  nota_p2c_editada            boolean default false,
  nota_p2d_editada            boolean default false,
  nota_p2f_editada            boolean default false,
  nota_huella_editada         boolean default false,

  -- Análisis web
  web_ssl                     boolean,
  web_mobile                  boolean,
  web_velocidad               int,
  web_instagram               text,
  web_facebook                text,

  -- Plataformas
  booking_url                 text,
  booking_rating              float,
  booking_reviews             int,
  booking_fotos               int,
  booking_precio_avg          float,
  airbnb_url                  text,
  airbnb_rating               float,
  airbnb_reviews              int,
  airbnb_fotos                int,
  airbnb_precio_noche         float,
  airbnb_superhost            boolean,
  tripadvisor_url             text,
  tripadvisor_rating          float,
  tripadvisor_reviews         int,
  tripadvisor_fotos           int,
  tripadvisor_excelencia      boolean,
  expedia_url                 text,
  expedia_rating              float,
  expedia_reviews             int,
  despegar_url                text,
  despegar_rating             float,
  despegar_reviews            int,
  plataformas_count           int default 0,
  plataformas_rating_avg      float,

  -- Huella digital
  huella_rating_global        float,
  huella_total_resenas        int,
  huella_tendencia            text,
  huella_palabras_positivas   jsonb,
  huella_palabras_negativas   jsonb,
  huella_google_pagina1       boolean,
  huella_bing_pagina1_3       boolean,
  huella_chatgpt_mencionado   boolean,
  huella_total_fotos          int,
  huella_fotos_recientes      boolean,

  -- Diagnóstico y estado
  diagnostico_venta           text,
  estado                      text default 'nuevo',
  -- nuevo → analizado_web → analizado_redes → analizado
  -- → analizado_huella → contactado → seguimiento_1 → seguimiento_2
  -- → seguimiento_3 → reporte_pagado → huella_pagada → tutorial_pagado
  -- → sitio_activo → renovacion_pendiente → renovado | perdido

  tiene_problema_reportado    boolean default false,
  plan                        text,
  sitio_url                   text,
  sitio_activo                boolean default false,
  vercel_project_id           text,
  fecha_pago_reporte          timestamptz,
  fecha_pago_huella           timestamptz,
  fecha_pago_sitio            timestamptz,
  fecha_renovacion            timestamptz,
  fecha_scraping              date,
  fecha_alta                  timestamptz default now(),
  updated_at                  timestamptz default now()
);
```

### Tabla `precios_por_pais`

```sql
create table precios_por_pais (
  id              uuid primary key default gen_random_uuid(),
  pais            text not null,
  categoria       text not null,
  -- hotel | hostal | restaurante | tour_operador | cabaña | todos
  servicio        text not null,
  -- reporte_completo | huella_digital | tutorial | sitio_web |
  -- correccion_maps | setup_plataformas | setup_redes |
  -- gestion_reputacion | guia_diy
  precio          float not null,
  moneda          text not null,
  precio_display  text not null,
  activo          boolean default true,
  updated_at      timestamptz default now()
);
```

### Tabla `reportes_problema`

```sql
create table reportes_problema (
  id              uuid primary key default gen_random_uuid(),
  negocio_id      uuid references negocios(id),
  tipo            text not null,
  detalle         text,
  estado          text default 'pendiente',
  nota_interna    text,
  reportado_at    timestamptz default now(),
  resuelto_at     timestamptz,
  accion_tomada   text
);
```

### Tabla `links`

```sql
create table links (
  id                  uuid primary key default gen_random_uuid(),
  negocio_id          uuid references negocios(id),
  tipo                text not null,
  url                 text not null,
  titulo              text,
  fuente              text,
  estado              text default 'activo',
  dato_asociado       text,
  ultima_verificacion timestamptz,
  fecha_extraccion    timestamptz default now(),
  metadata            jsonb
);
```

### Tabla `outreach`

```sql
create table outreach (
  id          uuid primary key default gen_random_uuid(),
  negocio_id  uuid references negocios(id),
  tipo        text,
  estado      text,
  enviado_at  timestamptz default now(),
  metadata    jsonb
);
```

### Tabla `pagos`

```sql
create table pagos (
  id                uuid primary key default gen_random_uuid(),
  negocio_id        uuid references negocios(id),
  tipo              text,
  -- reporte | huella_digital | tutorial | sitio | renovacion
  monto             float,
  moneda            text default 'USD',
  plataforma        text,
  mp_payment_id     text,
  paypal_order_id   text,
  webpay_token      text,
  estado            text,
  ambiente          text default 'prd',
  created_at        timestamptz default now()
);
```

### Tabla `tutoriales`

```sql
create table tutoriales (
  id              uuid primary key default gen_random_uuid(),
  slug            text unique not null,
  titulo          text not null,
  problema        text not null,
  dificultad      text not null,
  precio          float default 0,
  pasos           jsonb not null,
  servicio_cta    text,
  precio_servicio text,
  activo          boolean default true,
  created_at      timestamptz default now()
);
```

---

## Variables de entorno

```env
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
GOOGLE_PLACES_API_KEY=
SENDGRID_API_KEY=
EMAIL_FROM=hola@innovando.cl
EMAIL_FROM_NAME=Innovando
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_WHATSAPP_FROM=whatsapp:+56900000000
FIRMA_NOMBRE=Sebastián
FIRMA_CARGO=Especialista en presencia digital turística
FIRMA_WHATSAPP=+56 9 XXXX XXXX
REFRESH_MAPS_DIAS=30
REFRESH_WEB_DIAS=30
REFRESH_REDES_DIAS=30
REFRESH_PLATAFORMAS_DIAS=30
REFRESH_HUELLA_DIAS=30
PRECIO_BASE_REPORTE_USD=20
PRECIO_BASE_HUELLA_USD=29
PRECIO_BASE_TUTORIAL_USD=3
PRECIO_BASE_SITIO_USD=149
APP_ENV=test
APP_URL=https://innovando.cl
```

---

## Fases de construcción

### FASE 0 — Configuración
- [ ] Crear repo `innovando-scripts` en GitHub (privado)
- [ ] Crear ramas `main` y `dev`
- [ ] Crear archivos `.env`
- [ ] `pip3 install -r requirements.txt`
- [ ] `playwright install chromium`

### FASE 1 — Módulos compartidos
- [ ] `supabase_client.py` con soporte de ambientes
- [ ] `links_manager.py`
- [ ] `refresh_manager.py` — soporta módulo `huella`
- [ ] `notas_generator.py` — incluye `nota_huella`
- [ ] `precios_manager.py` — consulta precios por país + fallback USD

### FASE 2 — Scraper principal
- [ ] `maps_scraper.py` escribe en Supabase
- [ ] Genera slug, scores, notas, links
- [ ] Estado: `nuevo`

### FASE 3 — Scorer web
- [ ] Extrae emails + links + nota P2b
- [ ] Estado: `analizado_web`

### FASE 4 — Scorer redes
- [ ] Nota P2d
- [ ] Estado: `analizado_redes`

### FASE 5 — Scorer plataformas ⭐
- [ ] 5 scrapers extensibles
- [ ] Links + metadata + nota P2f
- [ ] Estado: `analizado`

### FASE 6 — Scorer huella ⭐
- [ ] `scorer_huella.py` con 4 módulos
- [ ] Reseñas consolidadas + palabras clave
- [ ] Redes: seguidores + engagement + frecuencia
- [ ] Buscadores: Google, Bing, ChatGPT
- [ ] Fotos: conteo + antigüedad
- [ ] Score huella 0–100
- [ ] Nota huella
- [ ] Estado: `analizado_huella`

### FASE 7 — Outreach con precios locales
- [ ] Templates con precio en moneda local via `precios_manager`
- [ ] Secuencia completa de 4 pasos
- [ ] Twilio WhatsApp
- [ ] Modo preview

### FASE 8 — Validación Ancud
- [ ] Pipeline completo en test
- [ ] Verificar precios en moneda local
- [ ] Verificar informe de huella
- [ ] Outreach a 15 leads reales
- [ ] Migrar a prd si todo OK

---

## Pipeline completo

```bash
# Primera vez
python maps_scraper.py --env test --ciudad "Ancud, Chile"
python scorer_web.py --env test
python scorer_redes.py --env test
python scorer_plataformas.py --env test
python scorer_huella.py --env test

# Refresco mensual
python scorer_web.py --env prd --solo-desactualizados
python scorer_redes.py --env prd --solo-desactualizados
python scorer_plataformas.py --env prd --solo-desactualizados
python scorer_huella.py --env prd --solo-desactualizados

# Negocio específico
python scorer_huella.py --env prd --slug hostal-vista-al-mar --forzar

# Outreach
python outreach_email.py --env test --modo preview
python outreach_email.py --env test --max 5
python outreach_email.py --env prd
```

---

## requirements.txt

```
requests==2.31.0
pandas==2.1.0
playwright==1.40.0
supabase==2.3.0
sendgrid==6.11.0
twilio==8.10.0
python-dotenv==1.0.0
```

---

## Convenciones

- Siempre `--env test` durante desarrollo
- Todo link pasa por `links_manager`
- Todo refresco pasa por `refresh_manager`
- Todo precio pasa por `precios_manager` — nunca hardcodeado
- Notas generadas por `notas_generator`
- No sobreescribir notas con `*_editada = true`
- Guardar progreso cada 10 leads
- Commits: `feat:` `fix:` `chore:` `docs:`
- Nunca subir `.env.test` ni `.env.prd` a git

---

*innovando-scripts · v8.0 · Python + Supabase + Playwright*
*Parte del proyecto Innovando · innovando.cl*
