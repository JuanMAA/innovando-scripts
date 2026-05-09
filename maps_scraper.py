"""
maps_scraper.py — innovando-scripts · Etapa 1
Scraping de Google Places API por city y categoría.
Guarda leads en Supabase y actualiza tabla cities.

Uso:
    python maps_scraper.py --env test --city "Ancud, Chile"
    python maps_scraper.py --env test --city "Ancud, Chile" --max 15
    python maps_scraper.py --env prd  --city "Ancud, Chile"

Requiere:
    pip install requests pandas supabase python-dotenv
"""

import argparse
import re
import time
from datetime import datetime, timezone

import requests

from supabase_client import get_client, upsert_business, update_city, upsert_city, now_iso
from api_usage_tracker import APITracker
from contact_manager import save_phone
from data_manager import DataManager, set_data_bulk


# ──────────────────────────────────────────────
# CONFIGURACIÓN
# ──────────────────────────────────────────────

PLACES_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
PLACES_DETAIL_URL = "https://maps.googleapis.com/maps/api/place/details/json"

DETAIL_FIELDS = (
    "name,formatted_phone_number,international_phone_number,"
    "website,opening_hours,photos,rating,user_ratings_total,"
    "formatted_address,editorial_summary,place_id,geometry,types,"
    "reviews,price_level,business_status,"
    # Amenities booleanas de Places API
    "wheelchair_accessible_entrance,delivery,dine_in,takeout,"
    "reservable,serves_breakfast,serves_lunch,serves_dinner,"
    "serves_brunch,serves_vegetarian_food,outdoor_seating,"
    "live_music"
)

# Campos booleanos de Places API → etiqueta con emoji
PLACES_AMENITY_MAP = {
    "wheelchair_accessible_entrance": "♿ Acceso accesible",
    "delivery":                        "🚚 Delivery",
    "dine_in":                         "🍽️ Comer en el local",
    "takeout":                         "🥡 Para llevar",
    "reservable":                      "📅 Reservas disponibles",
    "serves_breakfast":                "🍳 Desayuno",
    "serves_lunch":                    "☀️ Almuerzo",
    "serves_dinner":                   "🌙 Cena",
    "serves_brunch":                   "🥂 Brunch",
    "serves_vegetarian_food":          "🥗 Opciones vegetarianas",
    "outdoor_seating":                 "🌿 Terraza / área exterior",
    "live_music":                      "🎵 Música en vivo",
}

# Defaults por categoría cuando Places no tiene campos booleanos
AMENITY_CATEGORY_DEFAULTS = {
    "hotel":       ["🛜 WiFi gratis", "🅿️ Estacionamiento", "🕐 Recepción 24h", "🛏️ Ropa de cama", "🔥 Calefacción", "🚿 Baño privado"],
    "hostal":      ["🛜 WiFi gratis", "🔒 Lockers", "🍳 Cocina compartida", "🛏️ Ropa de cama", "👥 Zona común", "🕐 Recepción"],
    "hostería":    ["🛜 WiFi gratis", "🅿️ Estacionamiento", "🛏️ Ropa de cama", "🍳 Desayuno incluido", "🔥 Calefacción", "🌿 Jardín"],
    "cabaña":      ["🛜 WiFi gratis", "🍳 Cocina equipada", "🅿️ Estacionamiento", "🛏️ Ropa de cama", "🔥 Parrilla/BBQ", "🌿 Jardín privado"],
    "restaurante": ["🛜 WiFi gratis", "🍽️ Dine in", "🥡 Para llevar", "📅 Reservas", "🅿️ Estacionamiento", "💳 Pagos con tarjeta"],
    "café":        ["🛜 WiFi gratis", "🥡 Para llevar", "💳 Pagos con tarjeta", "😌 Ambiente tranquilo", "🌿 Terraza"],
    "bar":         ["🎵 Música en vivo", "🌿 Terraza", "🅿️ Estacionamiento", "💳 Pagos con tarjeta", "📅 Reservas"],
    "turismo":     ["🗣️ Guía en español", "🚌 Transporte incluido", "🛡️ Seguro básico", "📅 Reserva online", "📷 Oportunidades fotográficas"],
    "tour":        ["🗣️ Guía en español", "🚌 Transporte incluido", "🛡️ Seguro básico", "📅 Reserva online", "👨‍👩‍👧 Grupos pequeños"],
}

AMENITY_FALLBACK = [
    "🛜 WiFi gratis",
    "💳 Pagos con tarjeta",
    "📅 Reservas disponibles",
    "🅿️ Estacionamiento",
    "♿ Acceso accesible",
    "🌟 Atención personalizada",
]

PHOTOS_URL = "https://maps.googleapis.com/maps/api/place/photo"
MAPS_BASE  = "https://www.google.com/maps/place/?q=place_id:"

CATEGORIAS = {
    "hotel":       "hotel",
    "hostal":      "hostal",
    "restaurante": "restaurant",
    "cabaña":      "cabaña turística",
}

# ── Pesos P2a (máx 20) ────────────────────────
PESOS_P2A = {
    "phone":       4,
    "hours":       5,
    "photos":      6,   # ≥10 fotos: 6 | ≥5: 3 | <5: 0
    "description": 5,
}
# ── Pesos P2c (máx 20) ────────────────────────
PESOS_P2C = {
    "rating":      10,  # ≥4.5:10 | ≥4.0:7 | ≥3.5:4 | <3.5:0
    "reviews":     10,  # ≥100:10 | ≥50:7 | ≥20:4 | ≥5:2 | <5:0
}

DIAGNOSTICOS = {
    "phone":       "{name} no tiene teléfono en Maps — los clientes no pueden llamar directamente.",
    "hours":       "{name} no tiene horarios en Maps — los viajeros no saben cuándo está abierto.",
    "photos":      "{name} tiene pocas fotos en Maps — primer motivo de abandono de viajeros.",
    "description": "{name} no tiene descripción en Maps — no comunica qué lo hace especial.",
    "reviews":     "{name} tiene pocas reseñas — genera desconfianza en viajeros nuevos.",
    "rating":      "{name} aún no tiene ratinges en Maps.",
}


# ──────────────────────────────────────────────
# HELPERS — nuevos campos
# ──────────────────────────────────────────────

def extraer_amenities(detalle: dict, category: str) -> list[str]:
    """
    Extrae amenities del detalle de Places API.
    Cadena: campos booleanos → defaults por categoría → fallback genérico.
    """
    amenities = [
        label
        for field, label in PLACES_AMENITY_MAP.items()
        if detalle.get(field) is True
    ]
    if amenities:
        return amenities

    # Defaults por categoría (búsqueda parcial)
    cat = (category or "").lower().strip()
    for key, defaults in AMENITY_CATEGORY_DEFAULTS.items():
        if cat == key or cat in key or key in cat:
            return defaults

    return AMENITY_FALLBACK


def obtener_foto_urls(photos: list, api_key: str, max_photos: int = 10) -> list[str]:
    """Construye URLs de fotos de Google Places."""
    urls = []
    for photo in photos[:max_photos]:
        ref = photo.get("photo_reference")
        if ref:
            url = (
                f"{PHOTOS_URL}?maxwidth=800"
                f"&photo_reference={ref}"
                f"&key={api_key}"
            )
            urls.append(url)
    return urls


def analizar_reviews(reviews: list) -> dict:
    """
    Analiza las reseñas para extraer keywords y tendencia.
    Retorna: {keywords_pos, keywords_neg, trend, reviews_clean}
    """
    if not reviews:
        return {"keywords_pos": [], "keywords_neg": [], "trend": "stable", "reviews_clean": []}

    reviews_clean = []
    ratings = []

    for r in reviews:
        text   = r.get("text", "")
        rating = r.get("rating", 0)
        if text:
            reviews_clean.append({
                "text":     text[:300],
                "rating":   rating,
                "author":   r.get("author_name", ""),
                "time":     r.get("relative_time_description", ""),
                "language": r.get("language", ""),
            })
        if rating:
            ratings.append(rating)

    # Keywords simples — palabras frecuentes en reseñas positivas/negativas
    pos_words = []
    neg_words = []
    stopwords = {"el", "la", "los", "las", "un", "una", "de", "en", "y",
                 "que", "se", "es", "del", "al", "muy", "me", "con", "por",
                 "más", "no", "pero", "the", "and", "is", "was", "it", "a"}

    for r in reviews:
        text   = (r.get("text") or "").lower()
        rating = r.get("rating", 3)
        words  = [w.strip(".,!?()") for w in text.split() if len(w) > 4]
        words  = [w for w in words if w not in stopwords]
        if rating >= 4:
            pos_words.extend(words)
        elif rating <= 2:
            neg_words.extend(words)

    # Top 5 más frecuentes
    from collections import Counter
    keywords_pos = [w for w, _ in Counter(pos_words).most_common(5)]
    keywords_neg = [w for w, _ in Counter(neg_words).most_common(5)]

    # Tendencia — comparar primera mitad vs segunda mitad
    trend = "stable"
    if len(ratings) >= 4:
        mid = len(ratings) // 2
        avg_old = sum(ratings[mid:]) / len(ratings[mid:])
        avg_new = sum(ratings[:mid]) / len(ratings[:mid])
        if avg_new > avg_old + 0.3:
            trend = "improving"
        elif avg_new < avg_old - 0.3:
            trend = "declining"

    return {
        "keywords_pos":  keywords_pos,
        "keywords_neg":  keywords_neg,
        "trend":         trend,
        "reviews_clean": reviews_clean,
    }


# ──────────────────────────────────────────────
# UTILS
# ──────────────────────────────────────────────

def generar_slug(name: str) -> str:
    """Convierte un name de negocio en slug URL-friendly."""
    slug = name.lower().strip()
    slug = re.sub(r'[áàäâ]', 'a', slug)
    slug = re.sub(r'[éèëê]', 'e', slug)
    slug = re.sub(r'[íìïî]', 'i', slug)
    slug = re.sub(r'[óòöô]', 'o', slug)
    slug = re.sub(r'[úùüû]', 'u', slug)
    slug = re.sub(r'[ñ]', 'n', slug)
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'\s+', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    return slug.strip('-')


def calcular_scores(detalle: dict) -> tuple[int, int, str, dict]:
    """
    Calcula score_p2a (P2a, máx 20), score_p2c (P2c, máx 20),
    sales_diagnosis y desglose por campo.
    score_total inicial = score_p2a + score_p2c (los demás los calculan otros scorers).
    """
    name = detalle.get("name", "Este negocio")
    missing_fields = []
    desglose = {}

    # Teléfono (P2a)
    tiene_tel = bool(
        detalle.get("formatted_phone_number") or
        detalle.get("international_phone_number")
    )
    desglose["phone"] = PESOS_P2A["phone"] if tiene_tel else 0
    if not tiene_tel: missing_fields.append("phone")

    # Horarios (P2a)
    has_hours = bool(detalle.get("opening_hours", {}).get("weekday_text"))
    desglose["hours"] = PESOS_P2A["hours"] if has_hours else 0
    if not has_hours: missing_fields.append("hours")

    # Fotos (P2a): ≥10: 6pts, ≥5: 3pts, <5: 0pts
    num_fotos = len(detalle.get("photos", []))
    if num_fotos >= 10:
        desglose["photos"] = PESOS_P2A["photos"]
    elif num_fotos >= 5:
        desglose["photos"] = PESOS_P2A["photos"] // 2
    else:
        desglose["photos"] = 0
        missing_fields.append("photos")

    # Descripción (P2a)
    tiene_desc = bool(detalle.get("editorial_summary", {}).get("overview"))
    desglose["description"] = PESOS_P2A["description"] if tiene_desc else 0
    if not tiene_desc: missing_fields.append("description")

    # Calificación (P2c): ≥4.5:10 | ≥4.0:7 | ≥3.5:4 | <3.5:0
    rating = detalle.get("rating", 0) or 0
    if rating >= 4.5:
        desglose["rating"] = 10
    elif rating >= 4.0:
        desglose["rating"] = 7
    elif rating >= 3.5:
        desglose["rating"] = 4
    else:
        desglose["rating"] = 0
        if rating == 0: missing_fields.append("rating")

    # Reseñas (P2c): ≥100:10 | ≥50:7 | ≥20:4 | ≥5:2 | <5:0
    num_reviews = detalle.get("user_ratings_total", 0) or 0
    if num_reviews >= 100:
        desglose["reviews"] = 10
    elif num_reviews >= 50:
        desglose["reviews"] = 7
    elif num_reviews >= 20:
        desglose["reviews"] = 4
    elif num_reviews >= 5:
        desglose["reviews"] = 2
    else:
        desglose["reviews"] = 0
        missing_fields.append("reviews")

    # Scores
    score_p2a = desglose["phone"] + desglose["hours"] + desglose["photos"] + desglose["description"]
    score_p2c = desglose["rating"] + desglose["reviews"]
    score_total = score_p2a + score_p2c  # solo P2a + P2c; los demás los calculan otros scorers

    # Diagnóstico — campo más crítico faltante
    pesos_diag = {**PESOS_P2A, "rating": PESOS_P2C["rating"], "reviews": PESOS_P2C["reviews"]}
    diagnostico = ""
    if missing_fields:
        campo_critico = max(missing_fields, key=lambda c: pesos_diag.get(c, 0))
        diagnostico = DIAGNOSTICOS.get(campo_critico, "").format(name=name)

    return score_p2a, score_p2c, score_total, diagnostico, desglose


# ──────────────────────────────────────────────
# API CALLS
# ──────────────────────────────────────────────

def buscar_lugares(api_key: str, query: str, city: str) -> list[dict]:
    """Busca lugares en Google Places Text Search con paginación."""
    lugares = []
    params = {
        "query": f"{query} en {city}",
        "key": api_key,
        "language": "es",
        "region": "cl",
    }
    while True:
        resp = requests.get(PLACES_SEARCH_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        status = data.get("status")
        if status == "ZERO_RESULTS":
            break
        if status != "OK":
            print(f"  ⚠️  API status: {status} — {data.get('error_message', '')}")
            break

        lugares.extend(data.get("results", []))

        next_token = data.get("next_page_token")
        if not next_token:
            break
        time.sleep(2)
        params = {"pagetoken": next_token, "key": api_key}

    return lugares


def obtener_detalle(api_key: str, place_id: str) -> dict:
    """Obtiene detalle completo de un lugar."""
    params = {
        "place_id": place_id,
        "fields": DETAIL_FIELDS,
        "key": api_key,
        "language": "es",
    }
    resp = requests.get(PLACES_DETAIL_URL, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json().get("result", {})


# ──────────────────────────────────────────────
# PROCESAMIENTO PRINCIPAL
# ──────────────────────────────────────────────

def scrape_city(
    api_key: str,
    city: str,
    country: str,
    env: str,
    max_leads: int | None = None
):
    """Pipeline completo: buscar → detallar → scorear → guardar en Supabase."""
    sb = get_client(env=env)

    tracker = APITracker(env=env, sb=sb, script="maps_scraper")

    print(f"\n{'='*55}")
    print(f"🌍 Ciudad: {city} | Ambiente: {env}")
    print(f"{'='*55}")

    # Registrar city como en_proceso
    upsert_city(sb, city.split(",")[0].strip(), country, {
        "status": "en_proceso",
        "started_at": now_iso(),
    })

    todos = []
    place_ids_vistos = set()

    for cat_name, cat_query in CATEGORIAS.items():
        print(f"\n🔍 Buscando {cat_name}s...")
        lugares = buscar_lugares(api_key, cat_query, city)
        tracker.track("google_places", used=len(lugares))
        print(f"   Encontrados: {len(lugares)}")

        for lugar in lugares:
            if max_leads and len(todos) >= max_leads:
                print(f"\n   ⏹️  Límite de {max_leads} leads alcanzado")
                break

            place_id = lugar.get("place_id")
            if not place_id or place_id in place_ids_vistos:
                continue
            place_ids_vistos.add(place_id)

            name = lugar.get("name", "Sin name")
            print(f"   📍 {name}")

            try:
                detalle = obtener_detalle(api_key, place_id)
                time.sleep(0.1)
            except Exception as e:
                print(f"      ⚠️  Error al obtener detalle: {e}")
                continue

            score_p2a, score_rep, score_total, diagnostico, desglose = calcular_scores(detalle)
            geo          = detalle.get("geometry", {}).get("location", {})
            review_data  = analizar_reviews(detalle.get("reviews", []))
            foto_urls    = obtener_foto_urls(detalle.get("photos", []), api_key)
            amenities    = extraer_amenities(detalle, cat_name)
            business_status = detalle.get("business_status", "OPERATIONAL")

            slug = generar_slug(name)

            business_data = {
                "place_id":          place_id,
                "slug":              slug,
                "name":              name,
                "category":          cat_name,
                "city":              city.split(",")[0].strip(),
                "country":           country,
                "phone":             detalle.get("formatted_phone_number") or
                                     detalle.get("international_phone_number"),
                "website":           detalle.get("website"),
                "address":           detalle.get("formatted_address"),
                "latitude":          geo.get("lat"),
                "longitude":         geo.get("lng"),
                # Google Maps data
                "rating":            detalle.get("rating") or 0,
                "num_reviews":       detalle.get("user_ratings_total") or 0,
                "num_photos":        len(detalle.get("photos", [])),
                "has_hours":         bool(detalle.get("opening_hours", {}).get("weekday_text")),
                "has_description":   bool(detalle.get("editorial_summary", {}).get("overview")),
                "business_status":   business_status,
                "price_level":       detalle.get("price_level"),
                "google_maps_url":   MAPS_BASE + place_id,
                "types":             detalle.get("types"),
                # Scores
                "score_p2a":         score_p2a,
                "score_p2c":         score_rep,
                "score_total":       score_total,
                "sales_diagnosis":   diagnostico,
                "status":            "new",
                "needs_review":      True,
                # Reviews enriched
                "reviews":           review_data.get("reviews_clean"),
                "review_keywords_pos": review_data.get("keywords_pos"),
                "review_keywords_neg": review_data.get("keywords_neg"),
                "review_trend":      review_data.get("trend"),
                "photo_urls":        foto_urls,
                "amenities":         amenities,
                "description":       detalle.get("editorial_summary", {}).get("overview") or None,
            }

            try:
                # Negocios cerrados → oportunidad de venta diferente
                if business_status == "CLOSED_PERMANENTLY":
                    business_data["status"] = "closed_permanently"
                    business_data["sales_diagnosis"] = (
                        f"{name} aparece como cerrado permanentemente en Google Maps "
                        f"con {business_data.get('num_reviews', 0)} reseñas activas. "
                        "Podemos limpiar su huella digital antes de una reapertura."
                    )
                    print(f"      🔴 Cerrado permanentemente — guardando para huella digital")

                result = upsert_business(sb, business_data)
                todos.append(business_data)

                if result.get("id"):
                    bid = result["id"]

                    # Guardar teléfono en business_phones
                    if business_data.get("phone"):
                        save_phone(sb, business_id=bid,
                                   phone=business_data["phone"],
                                   type="phone", source="google_maps",
                                   found_at_step="maps_scraper", is_primary=True)

                    # Guardar datos enriched en business_data
                    dm = DataManager(sb, bid)
                    dm.set_many("maps", {
                        "rating":              business_data.get("rating"),
                        "num_reviews":         business_data.get("num_reviews"),
                        "num_photos":          business_data.get("num_photos"),
                        "has_hours":           business_data.get("has_hours"),
                        "has_description":     business_data.get("has_description"),
                        "price_level":         business_data.get("price_level"),
                        "business_status":     business_data.get("business_status"),
                        "utc_offset_minutes":  business_data.get("utc_offset_minutes"),
                        "google_maps_url":     business_data.get("google_maps_url"),
                        "review_trend":        review_data.get("trend"),
                        "review_keywords_pos": review_data.get("keywords_pos"),
                        "review_keywords_neg": review_data.get("keywords_neg"),
                        "reviews":             review_data.get("reviews_clean"),
                        "photo_urls":          foto_urls,
                        "amenities":           amenities,
                        "types":               business_data.get("types"),
                    }, source="google_maps", step="maps_scraper")

                    # Sincronizar columna amenities en businesses
                    import json as _json
                    sb.table("businesses").update({
                        "amenities": _json.dumps(amenities, ensure_ascii=False)
                    }).eq("id", bid).execute()

                print(f"      ✅ Score: {score_total}/100 | {diagnostico[:60]}...")
            except Exception as e:
                print(f"      ❌ Error al guardar: {e}")

        if max_leads and len(todos) >= max_leads:
            break

    # Stats finales
    con_web = sum(1 for b in todos if b.get("website"))
    con_tel = sum(1 for b in todos if b.get("phone"))

    # Actualizar city como completada
    city_name = city.split(",")[0].strip()
    upsert_city(sb, city_name, country, {
        "status": "completada",
        "last_run_at": now_iso(),
        "total_leads": len(todos),
    })

    print(f"\n{'='*55}")
    print(f"✅ Scraping completado")
    print(f"   Total leads:    {len(todos)}")
    print(f"   Con sitio web:  {con_web} ({int(con_web/len(todos)*100) if todos else 0}%)")
    print(f"   Con teléfono:   {con_tel} ({int(con_tel/len(todos)*100) if todos else 0}%)")
    print(f"   Sin email aún:  {len(todos)} → correr scorer_web.py")
    print(f"{'='*55}")

    tracker.resumen("maps_scraper.py")

    # Preview top 5 leads más deficientes
    todos_sorted = sorted(todos, key=lambda x: x.get("score_total", 100))
    print(f"\n🔥 Top 5 leads más deficientes:")
    for b in todos_sorted[:5]:
        print(f"   [{b['score_total']}/100] {b['name']} — {b.get('sales_diagnosis', '')[:60]}")


# ──────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import os

    parser = argparse.ArgumentParser(description="innovando-scripts · Scraper Google Places")
    parser.add_argument("--env",    required=True, choices=["test", "prd"])
    parser.add_argument("--city", required=True, help='Ej: "Ancud, Chile"')
    parser.add_argument("--country",   default="Chile")
    parser.add_argument("--max",    type=int, default=None, help="Límite de leads")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv(f".env.{args.env}")
    api_key = os.getenv("GOOGLE_PLACES_API_KEY")
    if not api_key:
        raise ValueError(f"GOOGLE_PLACES_API_KEY no definida en .env.{args.env}")

    scrape_city(api_key, args.city, args.country, args.env, args.max)