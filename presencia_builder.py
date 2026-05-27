"""
presencia_builder.py — innovando-scripts

Genera el **Informe de Presencia Digital** para un lead que llegó desde el
formulario `Informe de Presencia Digital` del landing.

A diferencia de `huella_builder.py` y `web_builder.py` (que construyen
todo desde cero), este script es un **orchestrator**: usa el pipeline
existente del proyecto (`maps_scraper`, `scorer_*`, `report_builder`).

Pipeline:
  1. Lee el lead desde la tabla `leads`.
  2. Intenta matchear con un negocio existente en `businesses`:
       - Por dominio del sitio web (website_linkedin)
       - Por nombre + país (fuzzy match)
  3. Si NO hay match (y `--no-scrape` no fue pasado):
       - Llama Google Places Text Search con `name + país/ciudad`.
       - Crea fila en `businesses` con datos básicos de Maps.
  4. Llama `build_report()` para generar el snapshot en la tabla `reports`.
  5. Marca el lead como `qualified` y guarda el reporte URL pública.

Modos de uso:

  A) Scrapeo masivo por ciudad (existente, sigue funcionando):
       python maps_scraper.py    --env test --ciudad "Ancud, Chile"
       python scorer_*.py        --env test
       python report_builder.py  --env test --all

  B) Individual por lead del formulario:
       python presencia_builder.py --env test --list
       python presencia_builder.py --env test --lead-id <uuid>

  C) Forzar match manual (si auto-match falla):
       python presencia_builder.py --env test --lead-id <uuid> --slug <slug>

  D) Desactivar scrapeo on-demand (si sólo querés usar BD existente):
       python presencia_builder.py --env test --lead-id <uuid> --no-scrape

  E) Procesar todos los pendientes:
       python presencia_builder.py --env test --all --max 10

Variables en .env.test / .env.prd:
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY
    GOOGLE_PLACES_API_KEY          (requerido para scrapeo on-demand)
"""

import argparse
import os
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from supabase_client import get_client, get_business_by_slug, upsert_business, now_iso
from report_builder import build_report

# Directorio del proyecto (para invocar scorers como subprocess)
SCRIPTS_DIR = Path(__file__).resolve().parent


# ──────────────────────────────────────────────────────────────────────
# CONSTANTES
# ──────────────────────────────────────────────────────────────────────

LEAD_SERVICE_VALUES = (
    "informe-presencia",
    "presence-report",
    "relatorio-presenca",
    "rapport-presence",
)

# Stop-words que ignoramos al matchear nombre comercial
NAME_STOPWORDS = {
    "hostal", "hotel", "hosteria", "cabañas", "cabanas", "cabaña", "cabana",
    "tour", "operador", "operadora", "restaurante", "restaurant",
    "el", "la", "los", "las", "del", "de", "y", "&",
}

REPORTS_BASE = "https://reports.innovando.cl"

PLACES_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
PLACES_DETAIL_URL = "https://maps.googleapis.com/maps/api/place/details/json"
PLACES_DETAIL_FIELDS = (
    "name,formatted_phone_number,international_phone_number,website,"
    "opening_hours,photos,rating,user_ratings_total,"
    "formatted_address,editorial_summary,place_id,geometry,types,"
    "reviews,price_level,business_status"
)

# Mapeo de country code → término de localización en español
COUNTRY_HINTS = {
    "cl": "Chile",  "co": "Colombia", "ec": "Ecuador",
    "br": "Brasil", "bo": "Bolivia",  "pe": "Perú",
    "ar": "Argentina", "uy": "Uruguay", "py": "Paraguay",
    "mx": "México", "es": "España",  "us": "United States",
}


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def normalize_text(s: str | None) -> str:
    if not s: return ""
    s = s.lower().strip()
    # quita acentos básicos
    s = (s.replace("á", "a").replace("é", "e").replace("í", "i")
           .replace("ó", "o").replace("ú", "u").replace("ñ", "n"))
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def extract_domain(url: str | None) -> str | None:
    if not url: return None
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    try:
        h = urlparse(url).hostname or ""
        return h.lower().lstrip("www.")
    except Exception:
        return None


def name_tokens(s: str) -> set[str]:
    """Extrae tokens significativos de un nombre comercial."""
    return {t for t in normalize_text(s).split() if t and t not in NAME_STOPWORDS}


def name_similarity(a: str, b: str) -> float:
    """0-1, jaccard sobre tokens significativos."""
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def slugify(text: str) -> str:
    """Convierte a slug kebab-case (sin acentos, espacios → guiones)."""
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")[:80]


# ──────────────────────────────────────────────────────────────────────
# On-demand scraping — un solo negocio via Places Text Search
# ──────────────────────────────────────────────────────────────────────

def _places_text_search(query: str, key: str, verbose: bool = False) -> dict | None:
    """Primer resultado de Places Text Search para `query`. None si vacío."""
    if verbose: print(f"    🔎 Places search: '{query}'")
    r = requests.get(PLACES_SEARCH_URL, params={
        "query": query, "key": key, "language": "es",
    }, timeout=20)
    r.raise_for_status()
    data = r.json()
    if data.get("status") not in ("OK", "ZERO_RESULTS"):
        if verbose: print(f"      ✗ status={data.get('status')} {data.get('error_message', '')}")
        return None
    results = data.get("results") or []
    return results[0] if results else None


def _places_details(place_id: str, key: str, verbose: bool = False) -> dict:
    """Detalle completo del place."""
    if verbose: print(f"    📍 Places details: {place_id}")
    r = requests.get(PLACES_DETAIL_URL, params={
        "place_id": place_id, "key": key,
        "fields":   PLACES_DETAIL_FIELDS, "language": "es",
    }, timeout=20)
    r.raise_for_status()
    return (r.json() or {}).get("result", {})


def _detect_category(place_types: list[str], place_name: str) -> str:
    """Heurística simple para categorizar el negocio."""
    types = [t.lower() for t in (place_types or [])]
    name  = (place_name or "").lower()
    if "lodging" in types or any(w in name for w in ("hostal", "hotel", "hosteria", "cabaña", "cabana")):
        if "hostal"  in name: return "hostal"
        if "hostería"in name or "hosteria" in name: return "hosteria"
        if "cabaña"  in name or "cabana" in name: return "cabaña"
        return "hotel"
    if "restaurant" in types or "restaurante" in name: return "restaurante"
    if "cafe" in types or "café" in name or "cafe" in name: return "café"
    if "bar" in types: return "bar"
    if "travel_agency" in types or "tour" in name: return "tour"
    return "turismo"


def scrape_business_on_demand(sb, lead: dict, places_key: str,
                              verbose: bool = False) -> dict | None:
    """
    Hace un Places Text Search puntual a partir del lead, crea la fila en
    `businesses` con datos básicos de Maps, y la retorna.
    """
    name    = (lead.get("name") or "").strip()
    country = (lead.get("country") or "").lower()
    if not name:
        return None

    # Armar query con hint de país (Places lo necesita para narrow)
    hint = COUNTRY_HINTS.get(country, "")
    # Ciudad: prioridad al campo nuevo del form; fallback a regex sobre message
    city = (lead.get("city") or "").strip()
    if not city:
        msg  = lead.get("message") or ""
        m = re.search(r"\b([A-ZÁÉÍÓÚÑ][a-záéíóúñ]{3,}(?:\s[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?)\b", msg)
        if m: city = m.group(1)
    query = f"{name} {city or hint}".strip()

    place = _places_text_search(query, places_key, verbose=verbose)
    if not place:
        if verbose: print("    ✗ Sin resultados en Places")
        return None

    place_id = place.get("place_id")
    if not place_id:
        return None

    # Si ya existe por place_id (race condition), devolverlo
    try:
        r = sb.table("businesses").select("*").eq("place_id", place_id).maybe_single().execute()
        if r and r.data:
            if verbose: print(f"    ↻ Ya existía por place_id={place_id}")
            return r.data
    except Exception:
        pass

    # Pedir detalles completos
    time.sleep(0.5)
    detail = _places_details(place_id, places_key, verbose=verbose) or place

    # Armar fila businesses (campos compatibles con maps_scraper / report_builder)
    geo = detail.get("geometry", {}).get("location", {})
    address = detail.get("formatted_address") or place.get("formatted_address") or ""
    # Intentar separar ciudad: último segmento antes del país suele ser la ciudad/región
    city_guess = ""
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if len(parts) >= 2:
        city_guess = parts[-2]

    payload = {
        "place_id":        place_id,
        "slug":            slugify(detail.get("name") or name),
        "name":            detail.get("name") or name,
        "category":        _detect_category(detail.get("types"), detail.get("name") or name),
        "city":            city_guess or city or None,
        "country":         country or "cl",
        "address":         address or None,
        "phone":           detail.get("formatted_phone_number") or detail.get("international_phone_number"),
        "website":         detail.get("website"),
        "rating":          detail.get("rating"),
        "num_reviews":     detail.get("user_ratings_total") or 0,
        "latitude":        geo.get("lat"),
        "longitude":       geo.get("lng"),
        "google_maps_url": f"https://maps.google.com/?cid={place_id}",
        "status":          "analyzed",
        "scraped_at":      now_iso(),
        "created_at":      now_iso(),
    }

    # Adjuntar email/redes del lead si los aportó
    if lead.get("email"):    payload["email"]    = lead["email"]
    if lead.get("phone"):    payload["phone"]    = payload["phone"] or lead["phone"]

    biz = upsert_business(sb, payload)
    print(f"    ✓ Business creado on-demand: {biz['name']}  slug={biz['slug']}")
    return biz


# ──────────────────────────────────────────────────────────────────────
# Lead → business matching
# ──────────────────────────────────────────────────────────────────────

def find_business_for_lead(sb, lead: dict, verbose: bool = False) -> dict | None:
    """Intenta encontrar el `business` correspondiente al lead.

    Estrategia:
      1. Match por dominio (website_url o website_linkedin)
      2. Match por nombre comercial (fuzzy) en cualquier país,
         priorizando el country del lead si lo tiene.
    """
    # ── 1. Match por dominio ── prioridad al campo nuevo website_url
    site = lead.get("website_url") or lead.get("website_linkedin") or ""
    if site and "linkedin" not in site.lower():
        domain = extract_domain(site)
        if domain and "." in domain:
            if verbose: print(f"  🔎 Buscando por dominio '{domain}'")
            r = (sb.table("businesses")
                   .select("*")
                   .ilike("website", f"%{domain}%")
                   .limit(1)
                   .execute())
            if r.data:
                if verbose: print(f"    ✓ match: {r.data[0]['name']}")
                return r.data[0]

    # ── 2. Match por nombre comercial fuzzy ──
    lead_name = (lead.get("name") or "").strip()
    if not lead_name:
        return None

    tokens = name_tokens(lead_name)
    if not tokens:
        return None

    if verbose: print(f"  🔎 Buscando por nombre '{lead_name}' (tokens: {tokens})")

    # Heurística simple: tomamos el token más largo (>3 letras) para narrowing por SQL
    longest = max((t for t in tokens if len(t) > 3), key=len, default="")
    if not longest:
        return None

    query = (sb.table("businesses")
              .select("*")
              .ilike("name", f"%{longest}%")
              .limit(50))

    country = lead.get("country")
    if country:
        query = query.eq("country", country.lower())

    candidates = (query.execute()).data or []
    if not candidates:
        return None

    # Score fuzzy y devuelve el mejor (>= 0.4)
    scored = [(name_similarity(lead_name, b["name"]), b) for b in candidates]
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best = scored[0]

    if verbose:
        print(f"    Top 3 candidatos:")
        for s, b in scored[:3]:
            print(f"      {s:.2f}  {b['name']} ({b.get('city', '?')})")

    if best_score >= 0.4:
        return best
    return None


# ──────────────────────────────────────────────────────────────────────
# Enrichment — corre scorers sobre un business recién creado
# ──────────────────────────────────────────────────────────────────────

# Cada scorer: (label, script, ¿corre siempre o solo si tiene website?)
SCORER_CHAIN: list[tuple[str, str, bool]] = [
    ("Sitio web (contact + redes)", "scorer_web.py",        True),
    ("Lighthouse (perf, SEO, a11y)", "scorer_lighthouse.py", True),  # requiere website pero el scorer salta solo
    ("Redes sociales",               "scorer_redes.py",      False),
    ("IA / SEO local",               "scorer_ia.py",         False),
    ("Plataformas OTA",              "scorer_plataformas.py",False),
    ("Huella turística (NAP)",       "scorer_huella.py",     False),
]


def _run_scorer(script: str, env: str, slug: str, verbose: bool = False) -> tuple[bool, str]:
    """Invoca un scorer como subprocess con --slug.

    Devuelve (ok, mensaje). No falla el pipeline si un scorer da error —
    el reporte se genera con la data que haya.
    """
    cmd = [sys.executable, script, "--env", env, "--slug", slug]
    if verbose: cmd.append("--verbose")
    try:
        r = subprocess.run(
            cmd,
            cwd=SCRIPTS_DIR,
            capture_output=True,
            text=True,
            timeout=300,  # 5 min max por scorer
        )
        if r.returncode == 0:
            return True, "ok"
        return False, (r.stderr or r.stdout or "").strip().splitlines()[-1][:200]
    except subprocess.TimeoutExpired:
        return False, "timeout (>5min)"
    except Exception as e:
        return False, str(e)[:200]


def enrich_business(env: str, slug: str, verbose: bool = False) -> dict[str, str]:
    """Corre todos los scorers para enriquecer el business. Devuelve dict {scorer: status}."""
    results: dict[str, str] = {}
    print("\n  🔧 Enriqueciendo business con scorers…")
    for label, script, _required in SCORER_CHAIN:
        print(f"     • {label} ({script})…", end=" ", flush=True)
        ok, msg = _run_scorer(script, env, slug, verbose=verbose)
        results[script] = "ok" if ok else f"fallo: {msg}"
        print("✓" if ok else f"✗ ({msg})")
    return results


# ──────────────────────────────────────────────────────────────────────
# I/O
# ──────────────────────────────────────────────────────────────────────

def fetch_lead(sb, lead_id: str) -> dict | None:
    r = sb.table("leads").select("*").eq("id", lead_id).maybe_single().execute()
    return r.data if r else None


def list_pending_leads(sb, limit: int = 50) -> list[dict]:
    r = (sb.table("leads")
           .select("id, name, email, website_url, website_linkedin, city, country, page_slug, "
                   "service_interest, status, created_at, business_id")
           .in_("service_interest", list(LEAD_SERVICE_VALUES))
           .order("created_at", desc=True)
           .limit(limit)
           .execute())
    leads = r.data or []
    # Filtra los que ya tienen reporte generado (business_id seteado + status qualified+)
    out = []
    for l in leads:
        if l.get("status") in ("converted", "qualified") and l.get("business_id"):
            continue
        out.append(l)
    return out


def link_lead_to_business(sb, lead_id: str, business_id: str, business_slug: str):
    """Marca el lead como qualified, lo linkea al business y guarda nota."""
    now = datetime.now(timezone.utc).isoformat()
    note = f"Reporte generado: {REPORTS_BASE}/{business_slug}"
    sb.table("leads").update({
        "business_id":  business_id,
        "status":       "qualified",
        "status_changed_at": now,
        "internal_note": note,
        "updated_at":   now,
    }).eq("id", lead_id).execute()


# ──────────────────────────────────────────────────────────────────────
# Pipeline
# ──────────────────────────────────────────────────────────────────────

def process_lead(sb, lead: dict, force_slug: str | None = None,
                 scrape: bool = True, enrich: bool = True,
                 env: str = "test",
                 dry_run: bool = False, verbose: bool = False) -> dict:
    """Procesa un lead. Devuelve dict {ok, business, report, message}.

    Si no encuentra match en businesses y `scrape=True`, intenta crearlo
    on-demand via Google Places Text Search.

    Si `enrich=True`, corre los scorers (web, lighthouse, redes, IA, plataformas,
    huella) en serie antes de armar el reporte.
    """
    name = lead.get("name") or "Sin nombre"
    print(f"\n→ Procesando lead: {name}  ({lead.get('email') or '—'})")

    # 1. Encontrar el business
    if force_slug:
        if verbose: print(f"  🔎 --slug forzado: {force_slug}")
        business = get_business_by_slug(sb, force_slug)
        if not business:
            return {"ok": False, "message": f"slug '{force_slug}' no encontrado en businesses"}
    else:
        business = find_business_for_lead(sb, lead, verbose=verbose)

        # Fallback: scrapeo on-demand si está habilitado
        if not business and scrape:
            places_key = os.getenv("GOOGLE_PLACES_API_KEY")
            if not places_key:
                return {"ok": False, "message":
                        "Sin match y GOOGLE_PLACES_API_KEY no está en .env — "
                        "no puedo scrapear on-demand. Pasá --slug o --no-scrape."}
            print("  ↻ Sin match — scrapeando on-demand con Places API…")
            try:
                business = scrape_business_on_demand(sb, lead, places_key, verbose=verbose)
            except Exception as e:
                return {"ok": False, "message": f"Places API falló: {e}"}

        if not business:
            country = lead.get("country", "?")
            return {
                "ok": False,
                "message": (
                    f"No matché el lead y el scrapeo on-demand no encontró nada.\n"
                    f"   Pasos sugeridos:\n"
                    f"   1. Verificar que el lead tenga 'name' útil (actual: '{name}').\n"
                    f"   2. Correr maps_scraper para la ciudad (país: {country}).\n"
                    f"   3. O pasar --slug <slug> si ya sabés el negocio."
                ),
            }

    print(f"  ✓ Match: {business['name']}  ({business.get('city', '?')}, "
          f"{business.get('country', '?')})  slug={business['slug']}")

    # 2. Enriquecer con scorers (sólo si está habilitado)
    enrich_results: dict[str, str] = {}
    if enrich and not dry_run:
        enrich_results = enrich_business(env, business["slug"], verbose=verbose)
        # Re-fetch del business porque los scorers actualizaron columnas
        business = get_business_by_slug(sb, business["slug"]) or business

    # 3. Generar/actualizar reporte
    if dry_run:
        print("  ⊘ Dry-run: no se genera reporte ni se actualiza lead")
        return {"ok": True, "business": business, "report": None,
                "message": f"Match OK (dry-run): {business['slug']}"}

    print("\n  📄 Llamando build_report()…")
    try:
        report = build_report(sb, business)
    except Exception as e:
        return {"ok": False, "business": business,
                "message": f"build_report falló: {e}"}

    score = report.get("score_total", 0)
    print(f"  ✓ Reporte generado: score_total={score}/100")

    # 3. Linkear lead → business
    link_lead_to_business(sb, lead["id"], business["id"], business["slug"])
    url = f"{REPORTS_BASE}/{business['slug']}"
    print(f"  ✓ Lead marcado como 'qualified' · URL: {url}")

    return {
        "ok": True,
        "business": business,
        "report":   report,
        "url":      url,
        "message":  f"Reporte listo: {url}",
    }


# ──────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generador de Informe de Presencia Digital")
    parser.add_argument("--env",     choices=["test", "prd"], default="test")
    parser.add_argument("--lead-id", help="UUID del lead a procesar")
    parser.add_argument("--slug",    help="Slug del business a forzar (override del match)")
    parser.add_argument("--all",     action="store_true", help="Procesa todos los leads pendientes")
    parser.add_argument("--list",    action="store_true", help="Lista pendientes y sale")
    parser.add_argument("--max",     type=int, default=None, help="Cap para --all")
    parser.add_argument("--no-scrape", action="store_true",
                        help="Desactiva el scrapeo on-demand (default: activo si hay GOOGLE_PLACES_API_KEY)")
    parser.add_argument("--no-enrich", action="store_true",
                        help="No corre los scorers (web, lighthouse, redes, etc.). "
                             "Reporte sólo con datos básicos de Maps.")
    parser.add_argument("--quick", action="store_true",
                        help="Atajo: equivale a --no-enrich. Genera reporte rápido (~10s).")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    sb = get_client(env=args.env)

    if args.list:
        leads = list_pending_leads(sb)
        if not leads:
            print("Sin leads pendientes.")
            return
        print(f"\n{len(leads)} lead(s) pendiente(s):\n")
        for l in leads:
            print(f"  {l['id']}  ·  {l['name']:<35.35}  ·  {l.get('email') or '—':<30.30}  ·  {l['created_at'][:10]}")
        return

    if args.all:
        leads = list_pending_leads(sb)
        if args.max:
            leads = leads[:args.max]
        if not leads:
            print("Sin leads pendientes.")
            return
        print(f"📦 Procesando {len(leads)} leads pendientes…\n")
        ok = err = 0
        for l in leads:
            res = process_lead(sb, l, scrape=not args.no_scrape,
                               enrich=not (args.no_enrich or args.quick),
                               env=args.env,
                               dry_run=args.dry_run, verbose=args.verbose)
            if res["ok"]:
                ok += 1
            else:
                err += 1
                print(f"  ✗ {res['message']}")
        print(f"\n✅ {ok} OK · ✗ {err} con error/sin match")
        return

    if not args.lead_id:
        parser.error("Tenés que pasar --lead-id, --all o --list")

    lead = fetch_lead(sb, args.lead_id)
    if not lead:
        parser.error(f"lead-id {args.lead_id} no encontrado")

    res = process_lead(sb, lead, force_slug=args.slug,
                       scrape=not args.no_scrape,
                       enrich=not (args.no_enrich or args.quick),
                       env=args.env,
                       dry_run=args.dry_run, verbose=args.verbose)
    if not res["ok"]:
        print(f"\n✗ {res['message']}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
