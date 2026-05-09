"""
scorer_ia.py — innovando-scripts · Etapa P2e
Mide la visibilidad del negocio en sistemas de IA generativa.

Score P2e (0-5 pts):
  3 pts → Google muestra AI Overview para "[name] [city]"
  2 pts → aparece en ≥2 fuentes de alta autoridad IA
           (Perplexity, TripAdvisor listas, Viator, guías de viaje, Wikipedia)
  Los puntos se suman: máx 5

Pipeline:
  1. Buscar "[name] [city]" con Brave → analizar dominios de resultados
  2. Cargar Google Search con Playwright → detectar AI Overview div
  3. Guardar en business_data module='ai'
  4. Actualizar score_p2e + score_total en businesses

Uso:
    python scorer_ia.py --env test
    python scorer_ia.py --env test --slug hostal-vista-al-mar --verbose
    python scorer_ia.py --env prd --max 30
"""

import asyncio
import argparse
import re
from datetime import date, datetime, timezone

from playwright.async_api import async_playwright

from supabase_client import get_client, update_business
from data_manager import DataManager
from api_usage_tracker import APITracker
from search_client import SearchClient


# ── Dominios de alta autoridad para IA ───────────────────────
AI_AUTHORITY_DOMAINS = {
    "tripadvisor.com":      2,   # muy citado por LLMs
    "tripadvisor.cl":       2,
    "booking.com":          2,
    "airbnb.com":           2,
    "viator.com":           2,
    "getyourguide.com":     2,
    "lonelyplanet.com":     3,   # muy citado por LLMs
    "wikivoyage.org":       3,
    "wikipedia.org":        3,
    "timeout.com":          2,
    "fodors.com":           2,
    "frommers.com":         2,
    "expedia.com":          1,
    "despegar.com":         1,
    "chile.travel":         2,
    "sernatur.cl":          2,
    "turismo.gob.cl":       2,
}

# Umbral de puntos de autoridad para ganar 2 pts en P2e
AI_AUTHORITY_THRESHOLD = 4

# CSS selector para detectar AI Overview en Google (puede cambiar)
# Múltiples selectores para mayor robustez
AI_OVERVIEW_SELECTORS = [
    "[data-attrid='SGEDescription']",
    "div[jscontroller][data-hveid] [data-q]",
    ".AIOverview",
    "[aria-label*='AI Overview']",
    "[aria-label*='Descripción de IA']",
    "div[data-async-type='overviewUpdates']",
]

TIMEOUT_MS = 20_000


# ── Score P2e ─────────────────────────────────────────────────

def calcular_score_p2e(google_ai: bool, authority_points: int) -> int:
    """
    Score P2e (0-5):
      3 pts si Google muestra AI Overview
      2 pts si aparece en ≥ AI_AUTHORITY_THRESHOLD puntos de autoridad
    """
    score = 0
    if google_ai:
        score += 3
    if authority_points >= AI_AUTHORITY_THRESHOLD:
        score += 2
    return min(score, 5)


# ── Detección AI Overview en Google ──────────────────────────

async def detectar_google_ai_overview(page, nombre: str, ciudad: str, verbose: bool) -> tuple[bool, str | None]:
    """
    Carga Google Search y detecta si hay AI Overview para el negocio.
    Retorna (encontrado, snippet).
    """
    query = f"{nombre} {ciudad}"
    url   = f"https://www.google.com/search?q={query.replace(' ', '+')}&hl=es"

    try:
        await page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)  # esperar rendering JS

        for selector in AI_OVERVIEW_SELECTORS:
            try:
                el = await page.query_selector(selector)
                if el:
                    snippet = (await el.inner_text())[:300].strip()
                    if verbose:
                        print(f"   🤖 AI Overview detectado: {snippet[:80]}...")
                    return True, snippet
            except Exception:
                continue

        if verbose:
            print(f"   ℹ️  Sin AI Overview en Google para '{query}'")
        return False, None

    except Exception as e:
        if verbose:
            print(f"   ⚠️  Error Google AI check: {e}")
        return False, None


# ── Búsqueda de autoridad IA vía Brave ───────────────────────

def analizar_autoridad_brave(resultados: list[dict], nombre: str) -> tuple[int, list[str]]:
    """
    Analiza resultados de Brave para calcular puntos de autoridad IA.
    Retorna (total_puntos, dominios_encontrados).
    """
    puntos    = 0
    dominios  = []
    nombre_lw = nombre.lower()

    for r in resultados:
        url   = r.get("url", "").lower()
        title = (r.get("title", "") + " " + r.get("description", "")).lower()

        # Verificar que el resultado mencione el negocio
        palabras_nombre = [w for w in nombre_lw.split() if len(w) > 3]
        if not any(p in title for p in palabras_nombre):
            continue

        for dominio, peso in AI_AUTHORITY_DOMAINS.items():
            if dominio in url:
                puntos += peso
                dominios.append(dominio)
                break

    return puntos, list(set(dominios))


# ── Procesador por negocio ────────────────────────────────────

async def procesar_negocio(
    page,
    business: dict,
    sc: "SearchClient",
    sb,
    verbose: bool,
) -> dict:
    nombre = business["name"]
    ciudad = business.get("city", "")
    bid    = business["id"]

    if verbose:
        print(f"\n🔍 {nombre} ({ciudad})")

    dm = DataManager(sb, bid, step="scorer_ia")

    # 1 — Brave: buscar fuentes de autoridad IA
    query_ia = f"{nombre} {ciudad}"
    resultados_brave = sc._brave(query_ia, num=10) if sc else []
    authority_points, authority_domains = analizar_autoridad_brave(resultados_brave, nombre)

    if verbose and authority_domains:
        print(f"   📚 Fuentes autoridad: {', '.join(authority_domains)} ({authority_points} pts)")

    # 2 — Google: detectar AI Overview
    google_ai, google_snippet = await detectar_google_ai_overview(page, nombre, ciudad, verbose)

    # 3 — Calcular score
    score_p2e = calcular_score_p2e(google_ai, authority_points)

    if verbose:
        print(f"   📊 Score P2e: {score_p2e}/5 (AI Overview: {google_ai}, Autoridad: {authority_points}pts)")

    # 4 — Guardar en business_data
    ai_data = {
        "google_ai_overview":   str(google_ai).lower(),
        "perplexity_mentioned": "false",  # pendiente implementación directa
        "travel_guides_count":  str(len(authority_domains)),
        "has_structured_data":  "false",  # enriched por scorer_web si tiene
        "score_p2e":            str(score_p2e),
    }
    if google_snippet:
        ai_data["google_ai_snippet"] = google_snippet[:500]

    dm.set_many("ai", ai_data, source="scorer_ia", step="scorer_ia")

    # 5 — Actualizar score_p2e + score_total en businesses
    score_p2e_anterior = business.get("score_p2e") or 0
    score_total_actual = business.get("score_total") or 0
    nuevo_total        = min(100, score_total_actual - score_p2e_anterior + score_p2e)

    update_business(sb, business["place_id"], {
        "score_p2e":   score_p2e,
        "score_total": nuevo_total,
    })

    return {
        "slug":             business.get("slug"),
        "score_p2e":        score_p2e,
        "google_ai":        google_ai,
        "authority_points": authority_points,
        "authority_domains": authority_domains,
    }


# ── Obtener leads ─────────────────────────────────────────────

def get_leads(sb, env: str, slug: str | None) -> list[dict]:
    query = (
        sb.table("businesses")
        .select("id, place_id, slug, name, city, score_p2e, score_total")
        .eq("status", "analyzed" if env == "prd" else "new")
        .neq("status", "closed_permanently")
    )
    if slug:
        query = sb.table("businesses").select(
            "id, place_id, slug, name, city, score_p2e, score_total"
        ).eq("slug", slug)

    res = query.execute()
    return res.data or []


# ── Runner ────────────────────────────────────────────────────

async def run(env: str, max_leads: int | None, verbose: bool, slug: str | None):
    sb = get_client(env)

    cse_key  = __import__("os").environ.get("GOOGLE_CSE_API_KEY")
    cse_id   = __import__("os").environ.get("GOOGLE_CSE_ID")
    brave_key= __import__("os").environ.get("BRAVE_SEARCH_API_KEY")

    tracker = APITracker(env=env, sb=sb, script="scorer_ia")
    sc      = SearchClient(cse_key, cse_id, brave_key, tracker)

    leads = get_leads(sb, env, slug)
    if max_leads:
        leads = leads[:max_leads]

    total = len(leads)
    print(f"\n🤖 scorer_ia | {total} leads | environment '{env}'")
    print(f"   Brave disponible: {bool(brave_key)} | CSE disponible: {bool(cse_key)}")

    resultados = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36",
            locale="es-CL",
        )
        page = await context.new_page()

        for i, business in enumerate(leads, 1):
            print(f"\n[{i}/{total}] {business['name']}")
            try:
                resultado = await procesar_negocio(page, business, sc, sb, verbose)
                resultados.append(resultado)
            except Exception as e:
                print(f"   ❌ Error: {e}")
            await asyncio.sleep(2)  # pausa entre negocios

        await browser.close()

    # Resumen
    with_ai = [r for r in resultados if r["google_ai"]]
    with_authority = [r for r in resultados if r["authority_points"] >= AI_AUTHORITY_THRESHOLD]
    avg_score = sum(r["score_p2e"] for r in resultados) / max(len(resultados), 1)

    print(f"\n{'='*50}")
    print(f"🤖  scorer_ia — Resumen")
    print(f"{'='*50}")
    print(f"   Procesados:     {len(resultados)}/{total}")
    print(f"   Con AI Overview: {len(with_ai)} ({round(len(with_ai)/max(len(resultados),1)*100)}%)")
    print(f"   Con autoridad IA: {len(with_authority)} ({round(len(with_authority)/max(len(resultados),1)*100)}%)")
    print(f"   Score P2e prom: {avg_score:.1f}/5")
    print(f"{'='*50}")

    tracker.resumen()


# ── CLI ───────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="innovando-scripts · Scorer IA (P2e)")
    parser.add_argument("--env",     default="test", choices=["test", "prd"])
    parser.add_argument("--max",     type=int, default=None, help="Máx leads a procesar")
    parser.add_argument("--slug",    default=None, help="Slug específico")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    asyncio.run(run(args.env, args.max, args.verbose, args.slug))
