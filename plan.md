# innovando-scripts — Etapa 1
**Python 3.11 · Supabase · Playwright · PageSpeed API**
Versión Etapa 1 v3 · Mayo 2025

---

## Objetivo

Pipeline completo con 15 leads reales de Ancud.
Detectar contactos, auditar presencia digital y enviar reportes personalizados.

---

## Archivos

```
innovando-scripts/
├── supabase_client.py      # Cliente Supabase + CRUD
├── data_manager.py         # Tabla business_data centralizada ⭐
├── contact_manager.py      # business_emails + business_phones
├── socials_manager.py      # business_socials (14 redes soportadas)
├── api_usage_tracker.py    # Control de créditos de APIs
├── maps_scraper.py         # Google Places API
├── scorer_web.py           # Sitio web → contacto + redes
├── scorer_redes.py         # Instagram + Facebook (4 estrategias)
├── scorer_lighthouse.py    # Auditoría web (PageSpeed API)
├── scorer_huella.py        # Huella digital 6 módulos
├── report_builder.py       # Genera snapshots en tabla reports
├── outreach_email.py       # Emails personalizados (SendGrid)
├── requirements.txt
└── .env.example
```

---

## Pipeline completo

```bash
python3 maps_scraper.py      --env test --ciudad "Ancud, Chile" --max 15
python3 scorer_web.py        --env test --verbose
python3 scorer_redes.py      --env test --verbose
python3 scorer_lighthouse.py --env test --verbose
python3 scorer_huella.py     --env test --verbose
python3 report_builder.py    --env test --all
python3 outreach_email.py    --env test --modo preview
python3 outreach_email.py    --env test --max 5
```

---

## Arquitectura de datos

### `business_data` — tabla central key-value

Todos los scorers escriben aquí:

```python
dm = DataManager(sb, business_id)
dm.set("maps", "rating", 4.2, source="google_maps", step="maps_scraper")
dm.set_many("lighthouse", {"lh_performance": 42, "lh_seo": 67})
full = dm.get_full()  # una query — vista v_business_full
```

| Módulo | Script | Datos principales |
|---|---|---|
| `maps` | maps_scraper | rating, num_reviews, reviews, price_level, photo_urls |
| `web` | scorer_web | website, ssl, mobile_friendly, load_time_ms |
| `lighthouse` | scorer_lighthouse | lh_performance, lh_seo, lh_score, lh_action |
| `social` | scorer_redes | instagram_url, facebook_url |
| `platform` | scorer_plataformas *(E2)* | booking_rating, tripadvisor_url |
| `huella` | scorer_huella | huella_score, h1-h6, problems_count |
| `contact` | scorer_web/redes | email, whatsapp |

### Tablas de detalle

```
business_emails   → múltiples emails por negocio (source + step)
business_phones   → teléfonos + WhatsApp (type + source)
business_socials  → todas las redes: IG, FB, TikTok, YouTube, X, TripAdvisor...
```

### Vistas SQL

```sql
v_business_full    -- todo el negocio en una query (jsonb por módulo)
v_business_scores  -- solo scores para el dashboard
```

---

## `maps_scraper.py`

**Extrae de Google Places API:**
- Datos básicos: name, address, phone, website, category
- Métricas: rating, num_reviews, num_photos, has_hours, has_description
- Reviews (5 max): texto, rating, autor → keywords pos/neg automáticos
- Extras: price_level, business_status, utc_offset_minutes, open_now, photo_urls

**Negocios `CLOSED_PERMANENTLY`:**
→ Se guardan con `status: "closed_permanently"`
→ `sales_diagnosis`: oferta de limpieza de huella digital ($49.000 CLP)

**Uso:**
```bash
python3 maps_scraper.py --env test --ciudad "Ancud, Chile" --max 15
python3 maps_scraper.py --env prd  --ciudad "Castro, Chile"
```

---

## `scorer_web.py`

**Detecta si el sitio web es una red social:**

| URL detectada | Acción |
|---|---|
| instagram.com/… | Guarda en `business_socials`, limpia `website` |
| facebook.com/… | Idem |
| wa.me/… | Extrae WhatsApp, limpia `website` |
| linktr.ee + 6 agregadores | Visita el Linktree, extrae todos los links |

**Extrae del sitio:**
- Emails, WhatsApp, links de todas las redes
- SSL, mobile-friendly, tiempo de carga

**Guarda en:** `business_emails`, `business_phones`, `business_socials`, `business_data.web`

---

## `scorer_redes.py`

**4 estrategias en cascada:**
```
1. URL desde business_socials (scorer_web ya la encontró)
2. Google Custom Search API  → 100 búsquedas/día gratis
3. Brave Search API          → 2000 búsquedas/mes gratis
4. Playwright directo        → fallback sin API
```

**Variables:**
```env
GOOGLE_CSE_KEY=   # misma key de Places
GOOGLE_CSE_ID=    # programmablesearchengine.google.com
BRAVE_SEARCH_KEY= # api.search.brave.com (opcional)
```

**Al terminar muestra créditos usados:**
```
Google CSE uso: 24/100 búsquedas hoy [████████░░] 🟢
```

---

## `scorer_lighthouse.py`

**Solo negocios con `website` en Supabase.**

| Categoría | Peso | Qué mide |
|---|---|---|
| Performance | 35% | Velocidad de carga en móvil |
| SEO | 25% | Indexabilidad en Google |
| Accessibility | 20% | Usabilidad |
| Best Practices | 20% | Seguridad |

**Core Web Vitals:** LCP, FCP, TBT, CLS — mobile + desktop

**Acción recomendada:**
| Score | Acción | Precio |
|---|---|---|
| < 30 | `reemplazar` | $149 USD |
| 30–60 | `optimizar` | $79 USD |
| > 60 | `mantener` | — |

**Variable:**
```env
PAGESPEED_API_KEY=   # activar "PageSpeed Insights API" en Google Cloud Console
```

---

## `scorer_huella.py`

**6 módulos de análisis:**

| Módulo | Pts | Qué detecta |
|---|---|---|
| H1 · Google Maps | 30 | Fichas duplicadas, fotos insuficientes, sin horarios |
| H2 · Directorios | 20 | Yelp, Foursquare, Páginas Amarillas — NAP inconsistente |
| H3 · Plataformas | 20 | Booking, TripAdvisor — sin presencia o datos viejos |
| H4 · Redes | 15 | Perfiles huérfanos, inactivos, inexistentes |
| H5 · Medios | 10 | Menciones negativas en Google y blogs |
| H6 · NAP | 5 | Coherencia nombre/dirección/teléfono en todos los sitios |

**Por cada problema → 3 niveles:**
- Easy → Tutorial DIY gratis (ej: agregar fotos a Maps)
- Medium → Tutorial DIY $3 (ej: crear perfil en directorios)
- Hard → Servicio Innovando (ej: eliminar contenido negativo — $99.000 CLP)

**Servicios de limpieza en `country_pricing`:**
| Servicio | Chile | Colombia | Bolivia |
|---|---|---|---|
| digital_cleanup | $49.000 CLP | $89.000 COP | Bs. 85 |
| nap_correction | $49.000 CLP | — | — |
| negative_content_removal | $99.000 CLP | — | — |
| directory_setup | $49.000 CLP | — | — |
| review_campaign | $49.000 CLP | — | — |

---

## `report_builder.py`

Lee todo desde `DataManager` — una sola query:

```python
dm       = DataManager(sb, business["id"])
all_data = dm.get_all_modules()
maps     = all_data.get("maps", {})
lh       = all_data.get("lighthouse", {})
```

Genera notas automáticas por módulo usando los datos reales de Lighthouse y Google Maps.

---

## `outreach_email.py`

**Asunto según situación:**
- Normal: `"Tu presencia digital en Google: 41/100 — Hostal Vista al Mar"`
- Cerrado: `"Tu negocio aparece como cerrado en Google Maps — Hostal Vista al Mar"`

**Precio en moneda local** desde `country_pricing` — nunca hardcodeado.

**Modos:**
```bash
--modo preview   # genera HTML por lead, no envía nada
--max 5          # envía solo primeros 5
--ciudad "Ancud" # filtra por ciudad
```

---

## Control de APIs

```bash
python3 api_usage_tracker.py --env test         # ver uso del día
python3 api_usage_tracker.py --env test --reset # borrar historial
```

---

## Variables de entorno

```env
# Supabase
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=eyJ...

# Google
GOOGLE_PLACES_API_KEY=AIzaSy...
PAGESPEED_API_KEY=AIzaSy...
GOOGLE_CSE_KEY=AIzaSy...
GOOGLE_CSE_ID=a1b2c3...

# Brave (opcional)
BRAVE_SEARCH_KEY=

# SendGrid
SENDGRID_API_KEY=
EMAIL_FROM=hola@innovando.cl
EMAIL_FROM_NAME=Innovando
FIRMA_NOMBRE=Sebastián
FIRMA_CARGO=Especialista en presencia digital turística
FIRMA_WHATSAPP=+56 9 XXXX XXXX
REPORTES_URL=https://reportes.innovando.cl

APP_ENV=test
```

---

## Primeros comandos

```bash
cd innovando-scripts
pip3 install -r requirements.txt
playwright install chromium
cp .env.example .env.test
# Completar credenciales
python3 maps_scraper.py --env test --ciudad "Ancud, Chile" --max 15
```

---

*innovando-scripts · Etapa 1 v3 · Python + Supabase + Playwright*
*Meta: 15 leads Ancud → pipeline completo → primer pago*
