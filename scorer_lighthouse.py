"""
scorer_lighthouse.py — innovando-scripts · Etapa 1
Auditoría Lighthouse via Google PageSpeed Insights API.
Solo procesa negocios con sitio web (website IS NOT NULL).

Uso:
    python scorer_lighthouse.py --env test
    python scorer_lighthouse.py --env test --verbose
    python scorer_lighthouse.py --env test --slug hostal-vista-al-mar
    python scorer_lighthouse.py --env test --forzar

Requiere:
    pip install requests supabase python-dotenv
    Usa PAGESPEED_API_KEY si está definida, sino GOOGLE_PLACES_API_KEY.
"""

import argparse
import os
import time
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

from api_usage_tracker import APITracker
from data_manager import DataManager
from supabase_client import (
    get_client,
    update_business,
    get_business_by_slug,
    now_iso,
)

# ──────────────────────────────────────────────
# CONFIGURACIÓN
# ──────────────────────────────────────────────

PAGESPEED_URL  = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
DELAY_REQUESTS = 2.0
TIMEOUT        = 60
MAX_RETRIES    = 2


def normalizar_url(url: str) -> str:
    """Convierte dominios IDN (ej: cabañas.cl) a su forma ASCII (punycode)."""
    parsed = urlparse(url)
    try:
        host_ascii = parsed.hostname.encode("idna").decode("ascii")
    except Exception:
        return url
    return parsed._replace(netloc=host_ascii if not parsed.port else f"{host_ascii}:{parsed.port}").geturl()

CATEGORIAS = ["performance", "seo", "accessibility", "best-practices"]

# Pesos para score total Lighthouse (suman 100)
PESOS = {
    "performance":  35,
    "seo":          25,
    "accessibility":20,
    "best_practices":20,
}


# ──────────────────────────────────────────────
# API CALL
# ──────────────────────────────────────────────

def correr_pagespeed(url: str, api_key: str, estrategia: str) -> dict:
    """
    Llama a PageSpeed Insights API.
    Retorna scores 0-100 por categoría + métricas Core Web Vitals.
    """
    params = {
        "url":      url,
        "key":      api_key,
        "strategy": estrategia,
        "category": CATEGORIAS,
        "locale":   "es",
    }
    for intento in range(MAX_RETRIES):
        try:
            resp = requests.get(PAGESPEED_URL, params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            break
        except requests.exceptions.Timeout:
            if intento < MAX_RETRIES - 1:
                time.sleep(5)
            else:
                raise
    else:
        raise requests.exceptions.Timeout("Agotados los reintentos")
    data = resp.json()

    lh  = data.get("lighthouseResult", {})
    cats = lh.get("categories", {})
    audits = lh.get("audits", {})

    scores = {}

    # Scores por categoría (PageSpeed retorna 0–1, multiplicamos por 100)
    for cat_id, cat_data in cats.items():
        s = cat_data.get("score")
        if s is not None:
            key = cat_id.replace("-", "_")
            scores[key] = round(s * 100)

    # Core Web Vitals (en ms)
    for audit_id, field in [
        ("first-contentful-paint",    "fcp_ms"),
        ("largest-contentful-paint",  "lcp_ms"),
        ("total-blocking-time",       "tbt_ms"),
        ("speed-index",               "speed_index_ms"),
        ("interactive",               "tti_ms"),
    ]:
        val = audits.get(audit_id, {}).get("numericValue")
        if val is not None:
            scores[field] = round(val)

    # CLS (sin unidad)
    cls = audits.get("cumulative-layout-shift", {}).get("numericValue")
    if cls is not None:
        scores["cls"] = round(cls, 3)

    return scores


# ──────────────────────────────────────────────
# DIAGNÓSTICOS Y RECOMENDACIONES
# ──────────────────────────────────────────────

def generar_diagnostico(name: str, mobile: dict, desktop: dict) -> str:
    """Genera diagnóstico de venta basado en los scores."""
    perf  = mobile.get("performance", 100)
    seo   = mobile.get("seo", 100)
    mob   = mobile.get("performance", 100)  # mobile performance = qué tan bien carga en celular
    lcp   = mobile.get("lcp_ms", 0)

    problemas = []

    if perf < 50:
        seg = round(lcp / 1000, 1) if lcp else "?"
        problemas.append(
            f"carga lento en móvil ({seg}s — el 53% abandona sitios que tardan más de 3s)"
        )
    if seo < 70:
        problemas.append(
            f"Google no puede indexar el sitio correctamente (SEO: {seo}/100)"
        )
    if mobile.get("accessibility", 100) < 60:
        problemas.append(
            f"dificultad de navegación para muchos usuarios (Accesibilidad: {mobile.get('accessibility')}/100)"
        )
    if mobile.get("best_practices", 100) < 60:
        problemas.append(
            f"problemas de seguridad o buenas prácticas ({mobile.get('best_practices')}/100)"
        )

    if not problemas:
        return ""

    return f"El sitio web de {name}: {'; '.join(problemas)}."


def recomendar_accion(mobile: dict) -> str:
    """
    Retorna acción recomendada:
    - 'optimizar'   → mejorar el sitio existente ($99 USD)
    - 'reemplazar'  → crear uno nuevo ($149 USD)
    - 'mantener'    → está OK, solo vender reporte
    """
    perf = mobile.get("performance", 100)
    seo  = mobile.get("seo", 100)
    mob  = mobile.get("performance", 100)
    avg  = (perf + seo + mob) / 3

    if avg < 30:
        return "reemplazar"
    if avg < 60:
        return "optimizar"
    return "mantener"


def calcular_score_total(mobile: dict) -> int:
    """Score compuesto Lighthouse (0–100)."""
    return round(
        mobile.get("performance",   0) * PESOS["performance"]   / 100 +
        mobile.get("seo",           0) * PESOS["seo"]           / 100 +
        mobile.get("accessibility", 0) * PESOS["accessibility"] / 100 +
        mobile.get("best_practices",0) * PESOS["best_practices"]/ 100
    )


# ──────────────────────────────────────────────
# HELPER DE DISPLAY
# ──────────────────────────────────────────────

def semaforo(score) -> str:
    if not isinstance(score, (int, float)):
        return "—"
    return "🟢" if score >= 70 else ("🟡" if score >= 50 else "🔴")


# ──────────────────────────────────────────────
# COLUMNAS EN SUPABASE
# ──────────────────────────────────────────────
# Antes de correr por primera vez, ejecutar en Supabase SQL Editor:
#
# alter table businesses
#   add column if not exists lh_performance  int,
#   add column if not exists lh_seo          int,
#   add column if not exists lh_accessibility int,
#   add column if not exists lh_best_practices int,
#   add column if not exists lh_fcp_ms              int,
#   add column if not exists lh_lcp_ms              int,
#   add column if not exists lh_tbt_ms              int,
#   add column if not exists lh_cls                 float,
#   add column if not exists lh_performance_desktop int,
#   add column if not exists lh_seo_desktop         int,
#   add column if not exists lh_score         int,
#   add column if not exists lh_diagnosis         text,
#   add column if not exists lh_action              text,
#   add column if not exists lh_date               timestamptz;


# ──────────────────────────────────────────────
# PIPELINE PRINCIPAL
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="innovando-scripts · Scorer Lighthouse")
    parser.add_argument("--env",     required=True, choices=["test", "prd"])
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--slug",    default=None)
    parser.add_argument("--max",     type=int, default=None)
    parser.add_argument("--forzar",  action="store_true",
                        help="Re-analizar aunque ya tenga datos Lighthouse")
    args = parser.parse_args()

    load_dotenv(f".env.{args.env}")
    # Usa PAGESPEED_API_KEY si existe, sino fallback a GOOGLE_PLACES_API_KEY
    api_key = os.getenv("PAGESPEED_API_KEY") or os.getenv("GOOGLE_PLACES_API_KEY")
    if not api_key:
        raise ValueError(f"PAGESPEED_API_KEY o GOOGLE_PLACES_API_KEY no definida en .env.{args.env}")

    sb      = get_client(env=args.env)
    tracker = APITracker(env=args.env)

    # Obtener leads con sitio web
    if args.slug:
        lead = get_business_by_slug(sb, args.slug)
        leads = [lead] if lead else []
    else:
        result = (
            sb.table("businesses")
            .select("*")
            .not_.is_("website", "null")
            .execute()
        )
        leads = result.data or []
        if not args.forzar:
            leads = [l for l in leads if not l.get("lh_performance")]

    if args.max:
        leads = leads[:args.max]

    total = len(leads)
    print(f"\n🔦 Lighthouse — {total} sitios a analizar | environment '{args.env}'")

    analizados    = 0
    con_problemas = 0
    errores       = 0

    for i, lead in enumerate(leads, 1):
        name  = lead.get("name", f"Lead {i}")
        website = (lead.get("website") or "").strip()

        if not website:
            continue
        if not website.startswith(("http://", "https://")):
            website = "https://" + website
        website = normalizar_url(website)

        print(f"\n[{i}/{total}] {name}")
        print(f"   🌐 {website}")

        try:
            # ── Mobile ──────────────────────────────
            if args.verbose:
                print(f"   📱 Corriendo Lighthouse mobile...")
            tracker.track("pagespeed", used=1)
            mobile = correr_pagespeed(website, api_key, "mobile")
            time.sleep(DELAY_REQUESTS)

            # ── Desktop ─────────────────────────────
            if args.verbose:
                print(f"   🖥️  Corriendo Lighthouse desktop...")
            tracker.track("pagespeed", used=1)
            desktop = correr_pagespeed(website, api_key, "desktop")
            time.sleep(DELAY_REQUESTS)

            # ── Scores y diagnóstico ─────────────────
            score_total  = calcular_score_total(mobile)
            diagnostico  = generar_diagnostico(name, mobile, desktop)
            accion       = recomendar_accion(mobile)

            # Display
            print(f"   📱 Mobile:")
            print(f"      Performance:   {mobile.get('performance','—')}/100  {semaforo(mobile.get('performance'))}")
            print(f"      SEO:           {mobile.get('seo','—')}/100  {semaforo(mobile.get('seo'))}")
            print(f"      Accessibility: {mobile.get('accessibility','—')}/100  {semaforo(mobile.get('accessibility'))}")
            print(f"      Best Practices:{mobile.get('best_practices','—')}/100  {semaforo(mobile.get('best_practices'))}")
            if mobile.get("lcp_ms"):
                print(f"      LCP:           {round(mobile['lcp_ms']/1000,1)}s")
            print(f"   🖥️  Desktop Performance: {desktop.get('performance','—')}/100")
            print(f"   📊 Score total:  {score_total}/100")
            print(f"   💡 Acción:       {accion}")
            if diagnostico:
                print(f"   ⚠️  {diagnostico}")
                con_problemas += 1

            # ── Guardar en Supabase ──────────────────
            updates = {
                "lh_performance":   mobile.get("performance"),
                "lh_seo":           mobile.get("seo"),
                "lh_accessibility": mobile.get("accessibility"),
                "lh_best_practices":mobile.get("best_practices"),
                "lh_fcp_ms":               mobile.get("fcp_ms"),
                "lh_lcp_ms":               mobile.get("lcp_ms"),
                "lh_tbt_ms":               mobile.get("tbt_ms"),
                "lh_cls":                  mobile.get("cls"),
                "lh_performance_desktop":  desktop.get("performance"),
                "lh_seo_desktop":          desktop.get("seo"),
                "lh_score":          score_total,
                "lh_diagnosis":          diagnostico or None,
                "lh_action":               accion,
                "lh_date":                now_iso(),
            }
            updates = {k: v for k, v in updates.items() if v is not None}
            update_business(sb, lead["place_id"], updates)
            analizados += 1

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                print(f"   ⏳ Rate limit — esperando 60 segundos...")
                time.sleep(60)
            else:
                print(f"   ❌ HTTP Error: {e}")
                errores += 1
        except Exception as e:
            print(f"   ❌ Error: {e}")
            errores += 1

    # ── Resumen final ────────────────────────────
    print(f"\n{'='*55}")
    print(f"✅ Lighthouse completado")
    print(f"   Analizados:      {analizados}")
    print(f"   Con problemas:   {con_problemas}")
    print(f"   Errores:         {errores}")
    print(f"{'='*55}")

    tracker.resumen("scorer_lighthouse.py")

    result = sb.table("businesses").select(
        "name, lh_action, lh_score, lh_performance"
    ).not_.is_("lh_action", "null").execute()

    if result.data:
        optimizar  = [r for r in result.data if r.get("lh_action") == "optimizar"]
        reemplazar = [r for r in result.data if r.get("lh_action") == "reemplazar"]
        mantener   = [r for r in result.data if r.get("lh_action") == "mantener"]

        print(f"\n💰 Oportunidades de venta:")
        print(f"   🔧 Optimizar sitio:      {len(optimizar)} negocios → desde $99 USD")
        print(f"   🆕 Reemplazar por nuevo: {len(reemplazar)} negocios → desde $149 USD")
        print(f"   ✅ Mantener (OK):        {len(mantener)} negocios")

    print(f"\n➡️  Siguiente: python report_builder.py --env {args.env} --all")


if __name__ == "__main__":
    main()