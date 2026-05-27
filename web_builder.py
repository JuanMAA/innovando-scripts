"""
web_builder.py — innovando-scripts

Genera un informe de **Auditoría de Sitio Web** a partir de un lead capturado
por el formulario del landing (servicio `auditoria-sitio-web` y equivalentes).

Pipeline v1:
  1. Lee URL del sitio desde la tabla `leads` (campo `website_linkedin`).
  2. PageSpeed Insights API → Lighthouse mobile + desktop, Core Web Vitals.
  3. HTTP GET → meta tags, structured data, robots.txt, sitemap.xml, hreflang.
  4. HTTP HEAD → security headers (HSTS, CSP, X-Frame-Options, etc.).
  5. Reglas → findings priorizados (Alto/Medio/Bajo impacto) + plan de acción.
  6. Guarda en `web_audits`.

Uso:
    # Listar leads pendientes con sitio
    python web_builder.py --env test --list

    # Generar para un lead
    python web_builder.py --env test --lead-id <uuid>

    # Forzar URL distinta
    python web_builder.py --env test --lead-id <uuid> --url https://otro-sitio.cl

    # Auditar URL sin lead asociado
    python web_builder.py --env test --url https://ejemplo.cl

    # Re-procesar audit existente
    python web_builder.py --env test --audit-id <uuid> --rebuild

    # Sin guardar en BD
    python web_builder.py --env test --url https://ejemplo.cl --dry-run --verbose

Variables en .env.test / .env.prd:
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY
    PAGESPEED_API_KEY              (requerido — gratis hasta 25k/día)
"""

import argparse
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse, urljoin

import requests

from supabase_client import get_client


# ──────────────────────────────────────────────────────────────────────
# CONSTANTES
# ──────────────────────────────────────────────────────────────────────

PAGESPEED_URL  = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
CATEGORIAS     = ["performance", "seo", "accessibility", "best-practices"]

# Pesos para global_score (suman 100)
PESOS = {"performance": 35, "seo": 25, "accessibility": 20, "best_practices": 20}

# Slugs del lead.service_interest reconocidos
LEAD_SERVICE_VALUES = (
    "auditoria-sitio-web", "website-audit", "auditoria-site", "audit-site-web",
    "auditoria_web", "website_audit", "auditoria_site", "audit_site_web",
)

USER_AGENT = "Mozilla/5.0 (compatible; InnovandoAuditBot/1.0; +https://innovando.cl)"

REQUEST_TIMEOUT = 20
PAGESPEED_TIMEOUT = 60

# Thresholds Core Web Vitals (https://web.dev/vitals/)
CWV_THRESHOLDS = {
    "lcp_ms":  {"good": 2500, "poor": 4000, "label": "Largest Contentful Paint", "target": "< 2.5s", "unit": "ms"},
    "fid_ms":  {"good": 100,  "poor": 300,  "label": "First Input Delay",        "target": "< 100ms","unit": "ms"},
    "cls":     {"good": 0.1,  "poor": 0.25, "label": "Cumulative Layout Shift",  "target": "< 0.1",  "unit": ""},
    "fcp_ms":  {"good": 1800, "poor": 3000, "label": "First Contentful Paint",   "target": "< 1.8s", "unit": "ms"},
    "ttfb_ms": {"good": 500,  "poor": 1500, "label": "Time to First Byte",       "target": "< 500ms","unit": "ms"},
    "tbt_ms":  {"good": 200,  "poor": 600,  "label": "Total Blocking Time",      "target": "< 200ms","unit": "ms"},
}


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def normalize_url(url: str) -> str:
    """Asegura schema https:// y trim."""
    url = (url or "").strip()
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return url


def hostname_of(url: str) -> str:
    try:
        return urlparse(url).hostname or url
    except Exception:
        return url


def cwv_status(field: str, value: float | None) -> str:
    """Devuelve 'good' | 'needs' | 'poor' según thresholds."""
    if value is None:
        return "unknown"
    t = CWV_THRESHOLDS.get(field)
    if not t:
        return "unknown"
    if value <= t["good"]:  return "good"
    if value <= t["poor"]:  return "needs"
    return "poor"


def fmt_cwv(field: str, value: float | None) -> str:
    if value is None: return "—"
    t = CWV_THRESHOLDS.get(field, {})
    unit = t.get("unit", "")
    if field == "cls":
        return f"{value:.2f}"
    if unit == "ms":
        return f"{value/1000:.1f}s" if value >= 1000 else f"{int(value)}ms"
    return str(value)


# ──────────────────────────────────────────────────────────────────────
# 1 · Lighthouse via PageSpeed Insights
# ──────────────────────────────────────────────────────────────────────

def run_lighthouse(url: str, api_key: str, strategy: str = "mobile",
                   verbose: bool = False) -> dict:
    """Llama PageSpeed Insights. Retorna scores + CWV en ms."""
    params = {
        "url":      url,
        "key":      api_key,
        "strategy": strategy,
        "category": CATEGORIAS,
        "locale":   "es",
    }
    if verbose: print(f"    PageSpeed {strategy} · {url}")

    resp = requests.get(PAGESPEED_URL, params=params, timeout=PAGESPEED_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    lh = data.get("lighthouseResult", {})
    cats = lh.get("categories", {})
    audits = lh.get("audits", {})

    scores: dict = {}
    for cat_id, cat in cats.items():
        s = cat.get("score")
        if s is not None:
            scores[cat_id.replace("-", "_")] = round(s * 100)

    # Core Web Vitals (numericValue en ms; CLS unitless)
    metrics = [
        ("first-contentful-paint",    "fcp_ms"),
        ("largest-contentful-paint",  "lcp_ms"),
        ("total-blocking-time",       "tbt_ms"),
        ("speed-index",               "speed_index_ms"),
        ("interactive",               "tti_ms"),
        ("max-potential-fid",         "fid_ms"),
        ("server-response-time",      "ttfb_ms"),
    ]
    for audit_id, key in metrics:
        v = audits.get(audit_id, {}).get("numericValue")
        if v is not None:
            scores[key] = round(v)

    cls = audits.get("cumulative-layout-shift", {}).get("numericValue")
    if cls is not None:
        scores["cls"] = round(cls, 3)

    return scores


def build_cwv(mobile: dict) -> dict:
    """Estructura CWV alineada al UI del demo."""
    cwv: dict = {}
    for field in ["lcp_ms", "fid_ms", "cls", "fcp_ms", "ttfb_ms", "tbt_ms"]:
        v = mobile.get(field)
        cwv[field.replace("_ms", "")] = {
            "value":  fmt_cwv(field, v),
            "status": cwv_status(field, v),
            "target": CWV_THRESHOLDS[field]["target"],
            "label":  CWV_THRESHOLDS[field]["label"],
            "raw":    v,
        }
    return cwv


# ──────────────────────────────────────────────────────────────────────
# 2 · SEO checks (HTML + robots + sitemap)
# ──────────────────────────────────────────────────────────────────────

def fetch_html(url: str, verbose: bool = False) -> tuple[str, dict]:
    """Devuelve (html, response_headers)."""
    if verbose: print(f"    HTML · {url}")
    r = requests.get(url, headers={"User-Agent": USER_AGENT},
                     timeout=REQUEST_TIMEOUT, allow_redirects=True)
    return r.text, dict(r.headers)


def fetch_url(url: str, method: str = "GET", verbose: bool = False) -> dict:
    """Hace HEAD/GET y retorna headers + status."""
    if verbose: print(f"    {method} · {url}")
    try:
        r = requests.request(method, url,
                             headers={"User-Agent": USER_AGENT},
                             timeout=REQUEST_TIMEOUT,
                             allow_redirects=True)
        return {"status": r.status_code, "headers": dict(r.headers), "text": r.text[:5000] if method == "GET" else ""}
    except Exception as e:
        return {"status": 0, "headers": {}, "text": "", "error": str(e)}


def check_seo(url: str, html: str, verbose: bool = False) -> list[dict]:
    """Checks de SEO técnico básico. Retorna lista [{label, pass, detail}]."""
    checks: list[dict] = []
    html_lc = html.lower()
    base = f"{urlparse(url).scheme}://{urlparse(url).hostname}"

    # 1. Título único
    titles = re.findall(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    title = (titles[0].strip() if titles else "")
    checks.append({
        "label":  "Tag <title> presente",
        "pass":   bool(title),
        "detail": f"\"{title[:80]}\"" if title else "Sin <title>",
    })

    # 2. Meta description
    desc_match = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
        html, re.I)
    desc = desc_match.group(1) if desc_match else ""
    checks.append({
        "label":  "Meta description presente",
        "pass":   bool(desc and len(desc) >= 50),
        "detail": f"{len(desc)} caracteres" if desc else "Sin meta description",
    })

    # 3. Canonical
    canonical = re.search(
        r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']',
        html, re.I)
    checks.append({
        "label":  "Canonical configurado",
        "pass":   bool(canonical),
        "detail": canonical.group(1) if canonical else "Sin canonical",
    })

    # 4. Open Graph
    og_count = len(re.findall(r'<meta[^>]+property=["\']og:', html, re.I))
    checks.append({
        "label":  "Open Graph para previews",
        "pass":   og_count >= 3,
        "detail": f"{og_count} tags og:* encontrados",
    })

    # 5. Structured data (JSON-LD)
    jsonld = re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                        html, re.I | re.S)
    sd_types = []
    for block in jsonld:
        types = re.findall(r'"@type"\s*:\s*"([^"]+)"', block)
        sd_types += types
    checks.append({
        "label":  "Structured data (Schema.org)",
        "pass":   bool(sd_types),
        "detail": f"Tipos: {', '.join(set(sd_types))[:80]}" if sd_types else "Sin JSON-LD",
    })

    # 6. Hreflang
    hreflang = re.findall(r'<link[^>]+rel=["\']alternate["\'][^>]+hreflang=', html, re.I)
    checks.append({
        "label":  "Hreflang en multilenguaje",
        "pass":   bool(hreflang),
        "detail": f"{len(hreflang)} alternates" if hreflang else "Sin hreflang",
    })

    # 7. robots.txt
    rob = fetch_url(urljoin(base, "/robots.txt"), "GET", verbose=verbose)
    rob_ok = rob.get("status") == 200 and "Disallow:" in rob.get("text", "")
    checks.append({
        "label":  "robots.txt accesible",
        "pass":   rob_ok,
        "detail": f"HTTP {rob.get('status')}" if rob.get("status") else "No accesible",
    })

    # 8. sitemap.xml
    sm = fetch_url(urljoin(base, "/sitemap.xml"), "GET", verbose=verbose)
    sm_ok = sm.get("status") == 200 and "<urlset" in sm.get("text", "")
    checks.append({
        "label":  "Sitemap.xml accesible",
        "pass":   sm_ok,
        "detail": f"HTTP {sm.get('status')}" if sm.get("status") else "No accesible",
    })

    return checks


# ──────────────────────────────────────────────────────────────────────
# 3 · Security checks (headers)
# ──────────────────────────────────────────────────────────────────────

def check_security(url: str, headers: dict, verbose: bool = False) -> list[dict]:
    """Análisis de cabeceras de seguridad. headers viene de fetch_html()."""
    h = {k.lower(): v for k, v in headers.items()}
    is_https = url.lower().startswith("https://")

    checks: list[dict] = []

    checks.append({
        "label": "HTTPS forzado",
        "pass":  is_https,
        "detail": "Servido sobre HTTPS" if is_https else "Sin HTTPS",
    })

    checks.append({
        "label":  "Strict-Transport-Security",
        "pass":   "strict-transport-security" in h,
        "detail": h.get("strict-transport-security", "Header HSTS no configurado")[:140],
    })

    checks.append({
        "label":  "Content-Security-Policy",
        "pass":   "content-security-policy" in h,
        "detail": "Header CSP configurado" if "content-security-policy" in h else "Sin CSP — vulnerable a XSS",
    })

    checks.append({
        "label":  "X-Frame-Options",
        "pass":   "x-frame-options" in h,
        "detail": h.get("x-frame-options", "No presente — riesgo de clickjacking"),
    })

    checks.append({
        "label":  "X-Content-Type-Options",
        "pass":   h.get("x-content-type-options", "").lower() == "nosniff",
        "detail": h.get("x-content-type-options", "No presente"),
    })

    checks.append({
        "label":  "Referrer-Policy",
        "pass":   "referrer-policy" in h,
        "detail": h.get("referrer-policy", "Header no presente"),
    })

    server = h.get("server", "")
    checks.append({
        "label":  "Server header oculto",
        "pass":   server == "" or "/" not in server,  # ej. "nginx/1.18.0" expone versión
        "detail": f"Server: {server}" if server else "Sin header Server (bien)",
    })

    return checks


# ──────────────────────────────────────────────────────────────────────
# 4 · Findings priorizados (combina todo)
# ──────────────────────────────────────────────────────────────────────

def build_findings(mobile: dict, desktop: dict, cwv: dict,
                   seo_checks: list[dict], security_checks: list[dict]) -> list[dict]:
    """Genera lista de hallazgos priorizados Alto/Medio/Bajo."""
    f: list[dict] = []

    # Lighthouse scores
    if (mobile.get("performance") or 100) < 50:
        f.append({"level": "high", "cat": "Performance",
                  "text": f"Performance mobile {mobile.get('performance')}/100 — sitio muy lento.",
                  "impact": "+15-25% conversión"})
    elif (mobile.get("performance") or 100) < 70:
        f.append({"level": "med", "cat": "Performance",
                  "text": f"Performance mobile {mobile.get('performance')}/100 — mejorable.",
                  "impact": "+10% conversión"})

    # CWV
    cwv_data = cwv or {}
    for k, label_short in [("lcp", "LCP"), ("fid", "FID"), ("cls", "CLS"), ("tbt", "TBT"), ("ttfb", "TTFB")]:
        m = cwv_data.get(k, {})
        if m.get("status") == "poor":
            f.append({
                "level": "high",
                "cat":   "Performance",
                "text":  f"{label_short} de {m['value']} — supera el umbral aceptable.",
                "impact": f"Objetivo: {m.get('target', '')}",
            })

    # SEO
    for c in seo_checks:
        if not c["pass"]:
            level = "high" if c["label"] in ("Meta description presente", "Tag <title> presente") else "med"
            if c["label"] in ("Open Graph para previews",): level = "low"
            f.append({
                "level":  level,
                "cat":    "SEO",
                "text":   f"{c['label']}: {c['detail']}",
                "impact": "Indexación / CTR",
            })

    # SEO score
    if (mobile.get("seo") or 100) < 70:
        f.append({"level": "med", "cat": "SEO",
                  "text": f"SEO técnico Lighthouse {mobile.get('seo')}/100.",
                  "impact": "+10% tráfico orgánico"})

    # Accessibility
    if (mobile.get("accessibility") or 100) < 70:
        f.append({"level": "med", "cat": "A11y",
                  "text": f"Accesibilidad {mobile.get('accessibility')}/100 — afecta WCAG y SEO.",
                  "impact": "WCAG AA"})

    # Security
    for c in security_checks:
        if not c["pass"]:
            level = "high" if c["label"] in ("HTTPS forzado", "Content-Security-Policy") else "med"
            f.append({
                "level":  level,
                "cat":    "Seguridad",
                "text":   f"{c['label']}: {c['detail']}",
                "impact": "Vulnerabilidad XSS / MITM" if level == "high" else "Hardening",
            })

    # Orden: alto, medio, bajo
    order = {"high": 0, "med": 1, "low": 2}
    f.sort(key=lambda x: order.get(x["level"], 9))
    return f


# ──────────────────────────────────────────────────────────────────────
# 5 · Scores agregados + resumen + plan
# ──────────────────────────────────────────────────────────────────────

def global_score(mobile: dict) -> int:
    if not mobile:
        return 0
    total = 0
    weight_sum = 0
    for k, w in PESOS.items():
        v = mobile.get(k)
        if v is not None:
            total += v * w
            weight_sum += w
    return int(total / weight_sum) if weight_sum else 0


def build_resumen(global_sc: int, findings: list[dict],
                  mobile: dict) -> str:
    high  = sum(1 for f in findings if f["level"] == "high")
    med   = sum(1 for f in findings if f["level"] == "med")
    low   = sum(1 for f in findings if f["level"] == "low")
    perf  = mobile.get("performance", 100)
    a11y  = mobile.get("accessibility", 100)

    nivel = ("excelente" if global_sc >= 80 else
             "bueno"     if global_sc >= 60 else
             "regular"   if global_sc >= 40 else
             "crítico")

    parts = [
        f"Tu sitio tiene un score global **{global_sc}/100** ({nivel})."
    ]
    if perf < 50:
        parts.append(
            f"El principal problema es el **rendimiento** ({perf}/100) — "
            "los visitantes abandonan antes de que cargue. Optimizando esto "
            "esperamos +15-25% de conversión."
        )
    elif a11y >= 80 and perf >= 70:
        parts.append("La base técnica es sólida; el plan de acción se enfoca en quick wins de SEO y seguridad.")

    parts.append(
        f"Detectamos **{high} hallazgo(s) de alto impacto**, {med} medio(s) y {low} menor(es). "
        "Resolviendo los Quick Wins (1-2 días) ya mejorarías significativamente."
    )
    return " ".join(parts)


def build_recommendations(findings: list[dict],
                          seo_checks: list[dict],
                          security_checks: list[dict],
                          mobile: dict) -> list[dict]:
    quick:  list[str] = []
    medium: list[str] = []
    long_:  list[str] = []

    # Quick wins típicos
    if (mobile.get("performance") or 100) < 70:
        quick.append("Habilitar lazy-loading en imágenes (`loading=\"lazy\"`).")
        quick.append("Comprimir respuestas con gzip/brotli a nivel servidor.")

    for c in security_checks:
        if not c["pass"]:
            if c["label"] in ("Strict-Transport-Security", "X-Frame-Options",
                              "X-Content-Type-Options", "Referrer-Policy"):
                quick.append(f"Agregar header **{c['label']}** en la configuración del servidor.")
            elif c["label"] == "Content-Security-Policy":
                medium.append("Configurar **Content-Security-Policy** (empezar con `default-src 'self'`).")

    for c in seo_checks:
        if not c["pass"]:
            if c["label"] == "Meta description presente":
                quick.append("Agregar `<meta name=\"description\">` en cada página (max 160 caracteres).")
            elif c["label"] == "Structured data (Schema.org)":
                medium.append("Agregar **JSON-LD** con tipos `Organization`, `WebSite` y específicos del negocio.")
            elif c["label"] == "Hreflang en multilenguaje":
                medium.append("Implementar `hreflang` en versiones de otros idiomas.")
            elif c["label"] == "Sitemap.xml accesible":
                quick.append("Publicar `sitemap.xml` en la raíz y referenciarlo en robots.txt.")
            elif c["label"] == "Canonical configurado":
                medium.append("Agregar `<link rel=\"canonical\">` para evitar contenido duplicado.")

    if (mobile.get("performance") or 100) < 50:
        medium.append("Convertir imágenes a WebP/AVIF y servir tamaños responsive.")
        long_.append("Migrar hosting a CDN/Edge (Cloudflare, Vercel Edge) para reducir TTFB.")
        long_.append("Code splitting + tree-shaking en el bundle JavaScript.")

    if not quick and not medium and not long_:
        quick.append("Sin acciones prioritarias detectadas — el sitio está bien optimizado.")

    return [
        {"phase": "quick",  "label": "Quick wins (1-2 días)",        "items": quick},
        {"phase": "medium", "label": "Mediano plazo (1-2 semanas)",  "items": medium},
        {"phase": "long",   "label": "Largo plazo (1 mes+)",          "items": long_},
    ]


# ──────────────────────────────────────────────────────────────────────
# I/O Supabase
# ──────────────────────────────────────────────────────────────────────

def fetch_lead(sb, lead_id: str) -> dict | None:
    r = sb.table("leads").select("*").eq("id", lead_id).maybe_single().execute()
    return r.data if r else None


def list_pending_leads(sb, limit: int = 20) -> list[dict]:
    r = (sb.table("leads")
           .select("id, name, email, website_url, website_linkedin, message, service_interest, created_at")
           .in_("service_interest", list(LEAD_SERVICE_VALUES))
           .order("created_at", desc=True)
           .limit(limit)
           .execute())
    leads = r.data or []
    if leads:
        ids = [l["id"] for l in leads]
        existing = (sb.table("web_audits")
                    .select("lead_id, status")
                    .in_("lead_id", ids)
                    .execute()).data or []
        done = {a["lead_id"] for a in existing if a["status"] == "done"}
        leads = [l for l in leads if l["id"] not in done]
    return leads


def extract_url_from_lead(lead: dict) -> str | None:
    """Saca la URL del sitio. Prioridad:
    1. website_url (campo nuevo del form, dedicado a auditoría web)
    2. website_linkedin (legacy)
    3. message (fallback con regex)
    """
    # 1. Campo nuevo, dedicado
    site = (lead.get("website_url") or "").strip()
    if site and "." in site:
        return normalize_url(site)

    # 2. Legacy: website_linkedin (si no es linkedin)
    site = (lead.get("website_linkedin") or "").strip()
    if site and "linkedin" not in site.lower() and "." in site:
        return normalize_url(site)

    # 3. Fallback: buscar URL en message
    msg = lead.get("message") or ""
    m = re.search(r"https?://[\w\.-]+(?:/[\w\.\-/?&=%#]*)?", msg)
    if m:
        return normalize_url(m.group(0))

    return None


def upsert_audit(sb, payload: dict, audit_id: str | None = None) -> str:
    payload = {**payload, "updated_at": datetime.now(timezone.utc).isoformat()}
    if audit_id:
        sb.table("web_audits").update(payload).eq("id", audit_id).execute()
        return audit_id
    r = sb.table("web_audits").insert(payload).execute()
    return r.data[0]["id"]


# ──────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────

def build_audit(url: str, api_key: str, verbose: bool = False) -> dict:
    """Pipeline completo. Devuelve dict listo para guardar."""
    url = normalize_url(url)
    if not url:
        raise ValueError("URL inválida")

    print(f"\n→ Auditando: {url}")

    print("\n[1/4] Lighthouse mobile…")
    mobile  = run_lighthouse(url, api_key, "mobile",  verbose=verbose)
    print(f"      perf={mobile.get('performance')} · seo={mobile.get('seo')} · "
          f"a11y={mobile.get('accessibility')} · bp={mobile.get('best_practices')}")

    print("\n[2/4] Lighthouse desktop…")
    time.sleep(2)
    desktop = run_lighthouse(url, api_key, "desktop", verbose=verbose)
    print(f"      perf={desktop.get('performance')} · seo={desktop.get('seo')}")

    print("\n[3/4] HTML + headers…")
    try:
        html, resp_headers = fetch_html(url, verbose=verbose)
    except Exception as e:
        print(f"      ✗ Error al fetch HTML: {e}")
        html, resp_headers = "", {}

    seo_checks      = check_seo(url, html, verbose=verbose) if html else []
    security_checks = check_security(url, resp_headers, verbose=verbose)
    print(f"      SEO {sum(1 for c in seo_checks if c['pass'])}/{len(seo_checks)} · "
          f"Seguridad {sum(1 for c in security_checks if c['pass'])}/{len(security_checks)}")

    print("\n[4/4] Calculando findings y plan…")
    cwv      = build_cwv(mobile)
    findings = build_findings(mobile, desktop, cwv, seo_checks, security_checks)
    gscore   = global_score(mobile)
    resumen  = build_resumen(gscore, findings, mobile)
    plan     = build_recommendations(findings, seo_checks, security_checks, mobile)
    print(f"      global_score={gscore} · {len(findings)} findings · "
          f"{sum(1 for f in findings if f['level']=='high')} altos")

    return {
        "url":                url,
        "hostname":           hostname_of(url),
        "audited_at":         datetime.now(timezone.utc).isoformat(),
        "pages_audited":      1,
        "lh_performance":     mobile.get("performance"),
        "lh_seo":             mobile.get("seo"),
        "lh_accessibility":   mobile.get("accessibility"),
        "lh_best_practices":  mobile.get("best_practices"),
        "global_score":       gscore,
        "cwv":                cwv,
        "seo_checks":         seo_checks,
        "security_checks":    security_checks,
        "findings":           findings,
        "resumen":            resumen,
        "recommendations":    plan,
        "status":             "done",
        "generated_at":       datetime.now(timezone.utc).isoformat(),
    }


def main():
    parser = argparse.ArgumentParser(description="Generador de Auditoría de Sitio Web")
    parser.add_argument("--env",       choices=["test", "prd"], default="test")
    parser.add_argument("--lead-id",   help="UUID de un lead a auditar")
    parser.add_argument("--audit-id",  help="UUID de un web_audit existente (re-ejecuta)")
    parser.add_argument("--url",       help="URL a auditar (override del lead, o sin lead)")
    parser.add_argument("--list",      action="store_true")
    parser.add_argument("--dry-run",   action="store_true")
    parser.add_argument("--verbose",   action="store_true")
    args = parser.parse_args()

    sb = get_client(env=args.env)

    if args.list:
        leads = list_pending_leads(sb)
        if not leads:
            print("Sin leads pendientes de auditoría de sitio web.")
            return
        print(f"\n{len(leads)} lead(s) pendiente(s):\n")
        for l in leads:
            site = l.get("website_linkedin") or l.get("message", "")[:50]
            print(f"  {l['id']}  ·  {l['name']}  ·  {site}")
        return

    # Resolver URL
    url      = args.url
    lead_id  = args.lead_id

    if args.audit_id and not url:
        prev = sb.table("web_audits").select("*").eq("id", args.audit_id).maybe_single().execute()
        if not prev or not prev.data:
            parser.error(f"audit-id {args.audit_id} no encontrado")
        url     = prev.data.get("url")
        lead_id = prev.data.get("lead_id")
    elif lead_id and not url:
        lead = fetch_lead(sb, lead_id)
        if not lead:
            parser.error(f"lead-id {lead_id} no encontrado")
        url = extract_url_from_lead(lead)
        if not url:
            parser.error("No pude extraer una URL del lead (revisá website_linkedin/message). "
                         "Pasala manualmente con --url")

    if not url:
        parser.error("Necesito --url o --lead-id o --audit-id")

    api_key = os.getenv("PAGESPEED_API_KEY")
    if not api_key:
        parser.error("PAGESPEED_API_KEY no configurada en .env")

    # Marcar running
    if not args.dry_run:
        running = {"status": "running", "url": url, "lead_id": lead_id, "hostname": hostname_of(url)}
        audit_id = upsert_audit(sb, running, args.audit_id)
        print(f"\naudit_id: {audit_id}")
    else:
        audit_id = None

    try:
        payload = build_audit(url, api_key, verbose=args.verbose)
        payload["lead_id"] = lead_id
    except Exception as e:
        if not args.dry_run and audit_id:
            upsert_audit(sb, {"status": "error", "error_msg": str(e)}, audit_id)
        raise

    if args.dry_run:
        print("\n────────── DRY-RUN — JSON resultante ──────────")
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str)[:4000])
        print("…")
        return

    final_id = upsert_audit(sb, payload, audit_id)
    print(f"\n✅ Audit guardado: {final_id}")
    print(f"   global_score={payload['global_score']} · "
          f"perf={payload['lh_performance']} · seo={payload['lh_seo']}")
    print(f"   findings={len(payload['findings'])} · "
          f"altos={sum(1 for f in payload['findings'] if f['level']=='high')}")


if __name__ == "__main__":
    main()
