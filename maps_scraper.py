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
    "reviews,price_level,business_status"
)

PHOTOS_URL = "https://maps.googleapis.com/maps/api/place/photo"
MAPS_BASE  = "https://www.google.com/maps/place/?q=place_id:"

CATEGORIAS = {
    "hotel":       "hotel",
    "hostal":      "hostal",
    "restaurante": "restaurant",
    "cabaña":      "cabaña turística",
}

# Pesos para calcular score Maps (P2a) y Reputación (P2c)
PESOS = {
    "phone":       10,
    "website":     20,
    "hours":       15,
    "photos":      15,
    "description": 10,
    "rating":      10,
    "reviews":     10,
    "address":     10,
}

DIAGNOSTICOS = {
    "website":     "{name} no tiene sitio web — pierde visibilidad en Google y ChatGPT.",
    "phone":       "{name} no tiene teléfono en Maps — los clientes no pueden llamar directamente.",
    "hours":       "{name} no tiene horarios en Maps — los viajeros no saben cuándo está abierto.",
    "photos":      "{name} tiene pocas fotos en Maps — primer motivo de abandono de viajeros.",
    "description": "{name} no tiene descripción en Maps — no comunica qué lo hace especial.",
    "reviews":     "{name} tiene pocas reseñas — genera desconfianza en viajeros nuevos.",
    "rating":      "{name} aún no tiene ratinges en Maps.",
    "address":     "{name} no tiene dirección completa en Maps.",
}


# ──────────────────────────────────────────────
# HELPERS — nuevos campos
# ──────────────────────────────────────────────

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
    Calcula score_p2a (P2a), score_p2c (P2c),
    sales_diagnosis y desglose por campo.
    """
    name = detalle.get("name", "Este negocio")
    missing_fields = []
    desglose = {}

    # Teléfono
    tiene_tel = bool(
        detalle.get("formatted_phone_number") or
        detalle.get("international_phone_number")
    )
    desglose["phone"] = PESOS["phone"] if tiene_tel else 0
    if not tiene_tel: missing_fields.append("phone")

    # Sitio web
    tiene_web = bool(detalle.get("website"))
    desglose["website"] = PESOS["website"] if tiene_web else 0
    if not tiene_web: missing_fields.append("website")

    # Horarios
    has_hours = bool(detalle.get("opening_hours", {}).get("weekday_text"))
    desglose["hours"] = PESOS["hours"] if has_hours else 0
    if not has_hours: missing_fields.append("hours")

    # Fotos (mínimo 5)
    num_fotos = len(detalle.get("photos", []))
    if num_fotos >= 5:
        desglose["photos"] = PESOS["photos"]
    elif num_fotos > 0:
        desglose["photos"] = PESOS["photos"] // 2
    else:
        desglose["photos"] = 0
        missing_fields.append("photos")

    # Descripción
    tiene_desc = bool(detalle.get("editorial_summary", {}).get("overview"))
    desglose["description"] = PESOS["description"] if tiene_desc else 0
    if not tiene_desc: missing_fields.append("description")

    # Calificación
    rating = detalle.get("rating", 0) or 0
    tiene_rating = rating > 0
    desglose["rating"] = PESOS["rating"] if tiene_rating else 0
    if not tiene_rating: missing_fields.append("rating")

    # Reseñas (mínimo 10)
    num_reviews = detalle.get("user_ratings_total", 0) or 0
    if num_reviews >= 10:
        desglose["reviews"] = PESOS["reviews"]
    elif num_reviews > 0:
        desglose["reviews"] = PESOS["reviews"] // 2
    else:
        desglose["reviews"] = 0
        missing_fields.append("reviews")

    # Dirección
    tiene_dir = bool(detalle.get("formatted_address"))
    desglose["address"] = PESOS["address"] if tiene_dir else 0
    if not tiene_dir: missing_fields.append("address")

    # Scores
    score_p2a = desglose["phone"] + desglose["hours"] + desglose["photos"] + desglose["description"]
    score_rep = desglose["rating"] + desglose["reviews"]
    score_total = sum(desglose.values())

    # Diagnóstico — campo más crítico faltante
    diagnostico = ""
    if missing_fields:
        campo_critico = max(missing_fields, key=lambda c: PESOS.get(c, 0))
        diagnostico = DIAGNOSTICOS.get(campo_critico, "").format(name=name)

    return score_p2a, score_rep, score_total, diagnostico, desglose


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

    tracker = APITracker(env=env)

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
                        "types":               business_data.get("types"),
                    }, source="google_maps", step="maps_scraper")

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