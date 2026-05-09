"""
scorer_plataformas.py — innovando-scripts · Etapa 3
Busca y extrae datos reales de cada negocio en plataformas OTA.

Plataformas:
  Booking.com · Airbnb · TripAdvisor · Expedia · Despegar

Estrategia por plataforma:
  1. Google CSE  → encontrar URL del perfil (site:booking.com "nombre" "ciudad")
  2. Playwright  → visitar perfil y extraer JSON-LD + selectores específicos
  3. DataManager → guardar en business_data (módulo platform)
  4. Score P2f   → actualizar score_p2f en businesses (0-20 pts)

Scoring P2f:
  4 pts por plataforma encontrada (5 × 4 = 20 pts base)
  Total máx: 20 pts

Uso:
    python scorer_plataformas.py --env test
    python scorer_plataformas.py --env test --slug hostal-vista-al-mar --verbose
    python scorer_plataformas.py --env test --max 20 --forzar

Requiere:
    pip install playwright requests supabase python-dotenv
    playwright install chromium
"""

import argparse
import asyncio
import json
import os
import re
import time

import requests
from dotenv import load_dotenv
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from data_manager import DataManager
from search_client import SearchClient
from api_usage_tracker import APITracker
from supabase_client import get_client, update_business, get_business_by_slug, now_iso


# ──────────────────────────────────────────────
# CONFIGURACIÓN
# ──────────────────────────────────────────────

TIMEOUT_MS        = 14000
DELAY_ENTRE_LEADS = 2.5
DELAY_ENTRE_PLATS = 1.2

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# Plataformas a buscar — orden por relevancia para turismo latinoamericano
PLATAFORMAS = [
    {
        "id":      "booking",
        "nombre":  "Booking.com",
        "dominio": "booking.com",
        "emoji":   "🏨",
        "campos":  ["booking_url", "booking_rating", "booking_reviews",
                    "booking_photos", "booking_price_avg"],
    },
    {
        "id":      "airbnb",
        "nombre":  "Airbnb",
        "dominio": "airbnb.com",
        "emoji":   "🏠",
        "campos":  ["airbnb_url", "airbnb_rating", "airbnb_reviews",
                    "airbnb_price_night", "airbnb_superhost"],
    },
    {
        "id":      "tripadvisor",
        "nombre":  "TripAdvisor",
        "dominio": "tripadvisor.com",
        "emoji":   "🌍",
        "campos":  ["tripadvisor_url", "tripadvisor_rating", "tripadvisor_reviews",
                    "tripadvisor_ranking", "tripadvisor_excellence"],
    },
    {
        "id":      "expedia",
        "nombre":  "Expedia",
        "dominio": "expedia.com",
        "emoji":   "✈️",
        "campos":  ["expedia_url", "expedia_rating", "expedia_reviews"],
    },
    {
        "id":      "despegar",
        "nombre":  "Despegar",
        "dominio": "despegar.com",
        "emoji":   "🚀",
        "campos":  ["despegar_url", "despegar_rating", "despegar_reviews"],
    },
]

PTS_POR_PLATAFORMA  = 4


# ──────────────────────────────────────────────
# BÚSQUEDA VIA GOOGLE CSE
# ──────────────────────────────────────────────

def buscar_url_cse(
    nombre: str,
    ciudad: str,
    dominio: str,
    cse_key: str,
    cse_id: str,
) -> str | None:
    """
    Busca el perfil del negocio en una plataforma via Google CSE.
    Retorna la primera URL relevante encontrada, o None.
    """
    queries = [
        f'site:{dominio} "{nombre}" "{ciudad}"',
        f'site:{dominio} "{nombre}"',
    ]
    for query in queries:
        try:
            resp = requests.get(
                "https://www.googleapis.com/customsearch/v1",
                params={"key": cse_key, "cx": cse_id, "q": query, "num": 3},
                timeout=10,
            )
            items = resp.json().get("items", [])
            for item in items:
                url = item.get("link", "")
                # Filtrar páginas genéricas (homepage, search, listas)
                if dominio in url and not es_url_generica(url, dominio):
                    return url
        except Exception:
            pass
    return None


def buscar_url_google(page_sync, nombre: str, ciudad: str, dominio: str) -> str | None:
    """
    Fallback sin CSE: busca en Google y extrae primer link del dominio.
    Solo se usa si no hay CSE configurado.
    """
    # Se llama desde contexto async — retorna None para procesar después
    return None


def es_url_generica(url: str, dominio: str) -> bool:
    """Detecta URLs que son páginas genéricas, no perfiles de negocios."""
    genericas = [
        f"{dominio}/es/",
        f"{dominio}/s/",
        f"{dominio}/search",
        f"{dominio}/hotel/list",
        f"{dominio}/hotels/",
        f"{dominio}/rooms/",
        f"{dominio}/experiences",
        f"{dominio}/stays",
        f"tripadvisor.com/Tourism",
        f"tripadvisor.com/Restaurants",
        f"tripadvisor.com/Hotels",
        f"expedia.com/Hotel-Search",
        f"despegar.com/hoteles/",
    ]
    url_lower = url.lower()
    return any(g.lower() in url_lower for g in genericas)


# ──────────────────────────────────────────────
# EXTRACCIÓN DE JSON-LD
# ──────────────────────────────────────────────

def extraer_jsonld(html: str) -> list[dict]:
    """Extrae todos los bloques JSON-LD de la página."""
    bloques = []
    for match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE
    ):
        try:
            data = json.loads(match.group(1).strip())
            if isinstance(data, list):
                bloques.extend(data)
            else:
                bloques.append(data)
        except Exception:
            pass
    return bloques


def buscar_en_jsonld(bloques: list[dict], *types) -> dict | None:
    """Busca un bloque JSON-LD por @type."""
    types_lower = [t.lower() for t in types]
    for bloque in bloques:
        t = str(bloque.get("@type", "")).lower()
        if any(tt in t for tt in types_lower):
            return bloque
    return None


def rating_desde_jsonld(bloque: dict) -> tuple[float | None, int | None]:
    """Extrae rating y número de reviews desde un bloque JSON-LD."""
    ar = bloque.get("aggregateRating") or {}
    rating  = ar.get("ratingValue") or ar.get("ratingValue")
    reviews = ar.get("reviewCount") or ar.get("userInteractionCount")
    try:
        rating = round(float(rating), 1) if rating else None
    except Exception:
        rating = None
    try:
        reviews = int(reviews) if reviews else None
    except Exception:
        reviews = None
    return rating, reviews


# ──────────────────────────────────────────────
# EXTRACTORES POR PLATAFORMA
# ──────────────────────────────────────────────

async def extraer_booking(page, url: str, verbose: bool) -> dict:
    """Extrae rating, reviews, fotos y precio de Booking.com."""
    datos = {"booking_url": url}
    try:
        await page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        html = await page.content()

        # JSON-LD
        bloques = extraer_jsonld(html)
        lodging = buscar_en_jsonld(bloques, "Hotel", "LodgingBusiness", "Accommodation", "BedAndBreakfast")
        if lodging:
            rating, reviews = rating_desde_jsonld(lodging)
            if rating:   datos["booking_rating"]  = rating
            if reviews:  datos["booking_reviews"] = reviews

        # Fallback: selectores CSS de Booking
        if not datos.get("booking_rating"):
            try:
                score_el = await page.query_selector('[data-testid="review-score-right-component"] .ac4a7896c7')
                if not score_el:
                    score_el = await page.query_selector('.b5cd09854e')
                if score_el:
                    texto = await score_el.inner_text()
                    m = re.search(r'(\d[,\.]\d)', texto)
                    if m:
                        datos["booking_rating"] = float(m.group(1).replace(',', '.'))
            except Exception:
                pass

        if not datos.get("booking_reviews"):
            try:
                rev_el = await page.query_selector('[data-testid="review-score-right-component"] .abf093bdfe')
                if not rev_el:
                    rev_el = await page.query_selector('.b5cd09854e + span')
                if rev_el:
                    texto = await rev_el.inner_text()
                    m = re.search(r'([\d\.]+)', texto.replace('.', '').replace(',', ''))
                    if m:
                        datos["booking_reviews"] = int(m.group(1))
            except Exception:
                pass

        # Fotos
        try:
            fotos = await page.query_selector_all('[data-testid="property-gallery"] img, .bh-photo-grid img')
            if fotos:
                datos["booking_photos"] = len(fotos)
        except Exception:
            pass

        # Precio promedio
        try:
            precio_el = await page.query_selector('[data-testid="price-and-discounts-price"], .prco-valign-middle-helper')
            if precio_el:
                texto = await precio_el.inner_text()
                m = re.search(r'[\$\$€£]?\s*([\d\.]+)', texto.replace(',', ''))
                if m:
                    datos["booking_price_avg"] = float(m.group(1))
        except Exception:
            pass

    except Exception as e:
        if verbose:
            print(f"      ⚠️  Booking error: {e}")

    return datos


async def extraer_airbnb(page, url: str, verbose: bool) -> dict:
    """Extrae rating, reviews, precio y superhost de Airbnb."""
    datos = {"airbnb_url": url}
    try:
        await page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)
        html = await page.content()

        # JSON-LD
        bloques = extraer_jsonld(html)
        listing = buscar_en_jsonld(bloques, "LodgingBusiness", "Accommodation", "Product")
        if listing:
            rating, reviews = rating_desde_jsonld(listing)
            if rating:  datos["airbnb_rating"]  = rating
            if reviews: datos["airbnb_reviews"] = reviews

        # Fallback: selectores Airbnb
        if not datos.get("airbnb_rating"):
            try:
                # Airbnb usa spans con aria-label="X.X out of 5"
                rating_el = await page.query_selector('[aria-label*="out of 5"], ._17p6nbba')
                if rating_el:
                    aria = await rating_el.get_attribute("aria-label") or ""
                    m = re.search(r'([\d\.]+)\s*out of', aria)
                    if not m:
                        texto = await rating_el.inner_text()
                        m = re.search(r'([\d\.]+)', texto)
                    if m:
                        datos["airbnb_rating"] = float(m.group(1))
            except Exception:
                pass

        if not datos.get("airbnb_reviews"):
            try:
                rev_el = await page.query_selector('._s65ijh7, [aria-label*="review"]')
                if rev_el:
                    texto = await rev_el.inner_text()
                    m = re.search(r'(\d+)\s*review', texto, re.IGNORECASE)
                    if m:
                        datos["airbnb_reviews"] = int(m.group(1))
            except Exception:
                pass

        # Superhost
        try:
            texto_pagina = await page.inner_text("body")
            datos["airbnb_superhost"] = bool(
                re.search(r'superhost|super\s*anfitrión', texto_pagina, re.IGNORECASE)
            )
        except Exception:
            pass

        # Precio por noche
        try:
            precio_el = await page.query_selector('._tyxjp1, [data-testid="price-summary"]')
            if precio_el:
                texto = await precio_el.inner_text()
                m = re.search(r'[\$\$€£]?\s*([\d\.]+)', texto.replace(',', ''))
                if m:
                    datos["airbnb_price_night"] = float(m.group(1))
        except Exception:
            pass

    except Exception as e:
        if verbose:
            print(f"      ⚠️  Airbnb error: {e}")

    return datos


async def extraer_tripadvisor(page, url: str, verbose: bool) -> dict:
    """Extrae rating, reviews, ranking y badge de TripAdvisor."""
    datos = {"tripadvisor_url": url}
    try:
        await page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        html = await page.content()

        # JSON-LD
        bloques = extraer_jsonld(html)
        hotel = buscar_en_jsonld(bloques, "Hotel", "LodgingBusiness", "Restaurant",
                                  "TouristAttraction", "LocalBusiness")
        if hotel:
            rating, reviews = rating_desde_jsonld(hotel)
            if rating:  datos["tripadvisor_rating"]  = rating
            if reviews: datos["tripadvisor_reviews"] = reviews

        # Fallback selectores
        if not datos.get("tripadvisor_rating"):
            try:
                bubbles = await page.query_selector_all('.ui_bubble_rating')
                if bubbles:
                    cls = await bubbles[0].get_attribute("class") or ""
                    m = re.search(r'bubble_(\d+)', cls)
                    if m:
                        datos["tripadvisor_rating"] = int(m.group(1)) / 10
            except Exception:
                pass

        if not datos.get("tripadvisor_reviews"):
            try:
                texto_pagina = await page.inner_text("body")
                m = re.search(r'([\d,\.]+)\s*(?:opiniones|reviews|reseñas)', texto_pagina, re.IGNORECASE)
                if m:
                    datos["tripadvisor_reviews"] = int(m.group(1).replace(',', '').replace('.', ''))
            except Exception:
                pass

        # Ranking ("#1 de 45 hoteles en Ciudad")
        try:
            texto_pagina = await page.inner_text("body")
            m = re.search(r'#(\d+)\s+(?:de|of)\s+\d+', texto_pagina)
            if m:
                datos["tripadvisor_ranking"] = int(m.group(1))
        except Exception:
            pass

        # Badge Travelers' Choice / Excellence
        try:
            texto_pagina = await page.inner_text("body")
            datos["tripadvisor_excellence"] = bool(
                re.search(r"travelers'?\s*choice|excelencia|certificado de excelencia",
                          texto_pagina, re.IGNORECASE)
            )
        except Exception:
            pass

    except Exception as e:
        if verbose:
            print(f"      ⚠️  TripAdvisor error: {e}")

    return datos


async def extraer_expedia(page, url: str, verbose: bool) -> dict:
    """Extrae rating y reviews de Expedia."""
    datos = {"expedia_url": url}
    try:
        await page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        html = await page.content()

        bloques = extraer_jsonld(html)
        hotel = buscar_en_jsonld(bloques, "Hotel", "LodgingBusiness")
        if hotel:
            rating, reviews = rating_desde_jsonld(hotel)
            if rating:  datos["expedia_rating"]  = rating
            if reviews: datos["expedia_reviews"] = reviews

        if not datos.get("expedia_rating"):
            try:
                texto_pagina = await page.inner_text("body")
                m = re.search(r'(\d[\.,]\d)\s*/\s*5|(\d[\.,]\d)\s*(?:out of 5|de 5)', texto_pagina)
                if m:
                    val = (m.group(1) or m.group(2)).replace(',', '.')
                    datos["expedia_rating"] = float(val)
            except Exception:
                pass

    except Exception as e:
        if verbose:
            print(f"      ⚠️  Expedia error: {e}")

    return datos


async def extraer_despegar(page, url: str, verbose: bool) -> dict:
    """Extrae rating y reviews de Despegar."""
    datos = {"despegar_url": url}
    try:
        await page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)
        html = await page.content()

        bloques = extraer_jsonld(html)
        hotel = buscar_en_jsonld(bloques, "Hotel", "LodgingBusiness")
        if hotel:
            rating, reviews = rating_desde_jsonld(hotel)
            if rating:  datos["despegar_rating"]  = rating
            if reviews: datos["despegar_reviews"] = reviews

        if not datos.get("despegar_rating"):
            try:
                texto_pagina = await page.inner_text("body")
                m = re.search(r'([\d,\.]+)\s*/\s*(?:10|5)|calificación[:\s]+([\d,\.]+)', texto_pagina, re.IGNORECASE)
                if m:
                    val = (m.group(1) or m.group(2)).replace(',', '.')
                    rating = float(val)
                    # Normalizar a escala /5 si viene /10
                    if rating > 5:
                        rating = round(rating / 2, 1)
                    datos["despegar_rating"] = rating
            except Exception:
                pass

    except Exception as e:
        if verbose:
            print(f"      ⚠️  Despegar error: {e}")

    return datos


# Mapa plataforma_id → función extractora
EXTRACTORES = {
    "booking":     extraer_booking,
    "airbnb":      extraer_airbnb,
    "tripadvisor": extraer_tripadvisor,
    "expedia":     extraer_expedia,
    "despegar":    extraer_despegar,
}


# ──────────────────────────────────────────────
# SCORE P2f
# ──────────────────────────────────────────────

def calcular_score_p2f(resultados: dict) -> tuple[int, int, float | None]:
    """
    Calcula score_p2f (0-20), platforms_count y avg_rating.
    resultados = { "booking": {...}, "airbnb": {...}, ... }
    4 pts por plataforma encontrada (5 plataformas × 4 = 20 pts).
    """
    score   = 0
    count   = 0
    ratings = []

    for plat_id, datos in resultados.items():
        url_key = f"{plat_id}_url"
        if datos.get(url_key):
            score += PTS_POR_PLATAFORMA
            count += 1
            rating_key = f"{plat_id}_rating"
            r = datos.get(rating_key)
            if r:
                ratings.append(r)

    avg_rating = round(sum(ratings) / len(ratings), 2) if ratings else None
    return min(score, 20), count, avg_rating


# ──────────────────────────────────────────────
# PROCESAMIENTO DE UN NEGOCIO
# ──────────────────────────────────────────────

async def procesar_negocio(
    page,
    business: dict,
    sc: SearchClient,
    sb,
    verbose: bool,
) -> dict:
    """Analiza todas las plataformas para un negocio."""
    nombre = business.get("name", "")
    ciudad = business.get("city", "")
    bid    = business.get("id")

    resultados = {}

    for plat in PLATAFORMAS:
        pid     = plat["id"]
        dominio = plat["dominio"]
        emoji   = plat["emoji"]

        if verbose:
            print(f"   {emoji} {plat['nombre']}...")

        # 1 — Buscar URL via SearchClient (CSE → Brave automático)
        url = sc.buscar_en_sitio(nombre, ciudad, dominio)
        await asyncio.sleep(0.3)

        if not url:
            if verbose:
                print(f"      ✗ No encontrado en {plat['nombre']}")
            resultados[pid] = {}
            await asyncio.sleep(0.5)
            continue

        if verbose:
            print(f"      ✓ URL: {url[:70]}...")

        # 2 — Extraer datos
        extractor = EXTRACTORES.get(pid)
        if extractor:
            try:
                datos = await extractor(page, url, verbose)
                resultados[pid] = datos
                rating = datos.get(f"{pid}_rating")
                reviews = datos.get(f"{pid}_reviews")
                info = f"rating={rating}" if rating else "sin rating"
                if reviews:
                    info += f" · {reviews} reseñas"
                if verbose:
                    print(f"      📊 {info}")
            except Exception as e:
                if verbose:
                    print(f"      ⚠️  Error extrayendo {plat['nombre']}: {e}")
                resultados[pid] = {f"{pid}_url": url}
        else:
            resultados[pid] = {f"{pid}_url": url}

        await asyncio.sleep(DELAY_ENTRE_PLATS)

    # 3 — Calcular score
    score_p2f, platforms_count, avg_rating = calcular_score_p2f(resultados)

    # 4 — Guardar en business_data (módulo platform)
    if bid:
        dm = DataManager(sb, bid)
        datos_a_guardar = {"platforms_count": platforms_count}
        if avg_rating is not None:
            datos_a_guardar["platforms_avg_rating"] = avg_rating

        for pid_r, datos_plat in resultados.items():
            datos_a_guardar.update(datos_plat)

        dm.set_many("platform", datos_a_guardar,
                    source="scorer_plataformas", step="scorer_plataformas")

        # 5 — Actualizar score_p2f en businesses
        score_total_actual = business.get("score_total", 0) or 0
        score_p2f_actual   = business.get("score_p2f", 0) or 0
        nuevo_total        = score_total_actual - score_p2f_actual + score_p2f

        sb.table("businesses").update({
            "score_p2f":   score_p2f,
            "score_total": nuevo_total,
            "updated_at":  now_iso(),
        }).eq("id", bid).execute()

    return {
        "resultados":       resultados,
        "score_p2f":        score_p2f,
        "platforms_count":  platforms_count,
        "avg_rating":       avg_rating,
    }


# ──────────────────────────────────────────────
# OBTENER LEADS
# ──────────────────────────────────────────────

def get_leads_para_plataformas(sb, forzar: bool = False) -> list[dict]:
    """
    Obtiene negocios a analizar.
    Sin --forzar: solo los que no tienen datos de plataformas aún.
    Con --forzar: todos.
    """
    if forzar:
        result = (
            sb.table("businesses")
            .select("id, place_id, name, city, country, score_p2f, score_total, category")
            .neq("status", "closed_permanently")
            .order("score_total", desc=False)
            .execute()
        )
        return result.data or []

    # Solo los que no tienen booking_url en business_data
    result = (
        sb.table("businesses")
        .select("id, place_id, name, city, country, score_p2f, score_total, category")
        .neq("status", "closed_permanently")
        .execute()
    )
    todos = result.data or []

    # Filtrar los que ya tienen datos de plataformas
    ya_procesados = set()
    if todos:
        bids = [b["id"] for b in todos]
        existing = (
            sb.table("business_data")
            .select("business_id")
            .eq("module", "platform")
            .eq("key", "platforms_count")
            .in_("business_id", bids)
            .execute()
        )
        ya_procesados = {r["business_id"] for r in (existing.data or [])}

    return [b for b in todos if b["id"] not in ya_procesados]


# ──────────────────────────────────────────────
# PIPELINE PRINCIPAL
# ──────────────────────────────────────────────

async def run(env: str, max_leads: int | None, verbose: bool,
              slug: str | None, forzar: bool):
    sb        = get_client(env=env)
    cse_key   = os.getenv("GOOGLE_CSE_KEY")
    cse_id    = os.getenv("GOOGLE_CSE_ID")
    brave_key = os.getenv("BRAVE_SEARCH_KEY")
    tracker   = APITracker(env=env, sb=sb, script="scorer_plataformas")

    sc = SearchClient(cse_key=cse_key, cse_id=cse_id,
                      brave_key=brave_key, tracker=tracker)

    if not cse_key and not brave_key:
        print("⚠️  Ninguna API de búsqueda configurada.")
        print("   Configurar GOOGLE_CSE_KEY+GOOGLE_CSE_ID o BRAVE_SEARCH_KEY")
        print("   en .env.{env} y volver a ejecutar.")
        return

    print(f"\n{sc.estado()}")

    # Obtener leads
    if slug:
        lead = get_business_by_slug(sb, slug)
        leads = [lead] if lead else []
    else:
        leads = get_leads_para_plataformas(sb, forzar=forzar)

    if max_leads:
        leads = leads[:max_leads]

    total = len(leads)
    print(f"\n{'='*55}")
    print(f"🏨 scorer_plataformas · {total} negocios · env={env}")
    print(f"   Plataformas: Booking · Airbnb · TripAdvisor · Expedia · Despegar")
    if not forzar:
        print(f"   Modo: solo nuevos (--forzar para re-analizar todos)")
    print(f"{'='*55}")

    con_booking     = 0
    con_airbnb      = 0
    con_tripadvisor = 0
    con_expedia     = 0
    con_despegar    = 0
    score_total_sum = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 768},
            locale="es-CL",
            timezone_id="America/Santiago",
        )
        page = await context.new_page()

        # Bloquear recursos innecesarios
        await page.route(
            "**/*.{png,jpg,jpeg,gif,webp,ico,woff,woff2,ttf,eot,mp4,webm}",
            lambda r: r.abort()
        )

        for i, lead in enumerate(leads, 1):
            nombre = lead.get("name", f"Lead {i}")
            print(f"\n[{i}/{total}] {nombre} ({lead.get('city', '')})")

            try:
                resultado = await procesar_negocio(
                    page, lead, sc, sb, verbose
                )

                score   = resultado["score_p2f"]
                count   = resultado["platforms_count"]
                rating  = resultado["avg_rating"]
                res     = resultado["resultados"]

                score_total_sum += score

                if res.get("booking",     {}).get("booking_url"):      con_booking += 1
                if res.get("airbnb",      {}).get("airbnb_url"):       con_airbnb += 1
                if res.get("tripadvisor", {}).get("tripadvisor_url"):  con_tripadvisor += 1
                if res.get("expedia",     {}).get("expedia_url"):      con_expedia += 1
                if res.get("despegar",    {}).get("despegar_url"):     con_despegar += 1

                rating_str = f" · avg rating {rating}" if rating else ""
                print(f"   ✅ {count}/5 plataformas · score_p2f={score}/20{rating_str}")

            except Exception as e:
                print(f"   💥 Error: {e}")

            await asyncio.sleep(DELAY_ENTRE_LEADS)

        await browser.close()

    # Resumen
    avg_score = round(score_total_sum / total, 1) if total else 0

    print(f"\n{'='*55}")
    print(f"✅ scorer_plataformas completado")
    print(f"   Procesados:          {total}")
    print(f"   Score P2f promedio:  {avg_score}/20")
    print(f"")
    print(f"   Booking.com:    {con_booking}/{total} ({pct(con_booking, total)}%)")
    print(f"   Airbnb:         {con_airbnb}/{total} ({pct(con_airbnb, total)}%)")
    print(f"   TripAdvisor:    {con_tripadvisor}/{total} ({pct(con_tripadvisor, total)}%)")
    print(f"   Expedia:        {con_expedia}/{total} ({pct(con_expedia, total)}%)")
    print(f"   Despegar:       {con_despegar}/{total} ({pct(con_despegar, total)}%)")
    print(f"{'='*55}")
    print(f"\n➡️  Siguiente paso: python scorer_huella.py --env {env}")


def pct(n: int, total: int) -> int:
    return int(n / total * 100) if total else 0


# ──────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="innovando-scripts · Scorer Plataformas OTA"
    )
    parser.add_argument("--env",    required=True, choices=["test", "prd"])
    parser.add_argument("--max",    type=int, default=None, help="Límite de negocios")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--slug",   default=None, help="Analizar solo un negocio")
    parser.add_argument("--forzar", action="store_true",
                        help="Re-analizar aunque ya tenga datos")
    args = parser.parse_args()

    load_dotenv(f".env.{args.env}")

    asyncio.run(run(args.env, args.max, args.verbose, args.slug, args.forzar))
