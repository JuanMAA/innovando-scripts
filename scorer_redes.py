"""
scorer_redes.py — innovando-scripts · Etapa 1
Detecta perfiles de Instagram y Facebook usando estrategias en cascada:

  1. Sitio web propio   → links extraídos por scorer_web (sin límites)
  2. Google CSE API     → 100 búsquedas/día gratis
  3. Brave Search API   → 2000 búsquedas/mes gratis
  4. Playwright directo → fallback sin API

Uso:
    python scorer_redes.py --env test --verbose
    python scorer_redes.py --env test --slug hostal-vista-al-mar
    python scorer_redes.py --env test --max 10

Requiere:
    pip install playwright requests supabase python-dotenv
    playwright install chromium
"""

import argparse
import asyncio
import os
import re
import time

import requests
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from dotenv import load_dotenv

from api_usage_tracker import APITracker
from data_manager import DataManager
from contact_manager import (
    save_email, save_phone,
    get_primary_email, get_primary_whatsapp
)
from socials_manager import (
    upsert_social, get_socials, get_social_url, NETWORKS
)
from supabase_client import (
    get_client,
    update_business,
    get_leads_por_estado,
    get_business_by_slug,
)

# ──────────────────────────────────────────────
# CONFIGURACIÓN
# ──────────────────────────────────────────────

TIMEOUT_MS       = 15000
DELAY_ENTRE_LEADS = 2.0

EMAIL_REGEX = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE
)

EMAIL_BLACKLIST = {
    "example.com", "ejemplo.com", "instagram.com", "facebook.com",
    "sentry.io", "wixpress.com", "domain.com", "test.com",
    "support.google.com", "google.com", "apple.com",
}

WHATSAPP_REGEX = re.compile(
    r'(?:wa\.me|api\.whatsapp\.com/send[?&]phone=)'
    r'[/]?(\+?[\d]{7,15})',
    re.IGNORECASE
)

IG_USERNAME_BLACKLIST = {
    'p', 'reel', 'reels', 'stories', 'explore', 'tv',
    'accounts', 'about', 'legal', 'privacy', 'help',
    'press', 'api', 'oauth', 'static',
}

FB_PATH_BLACKLIST = {
    'sharer', 'share', 'dialog', 'plugins', 'tr',
    'photo', 'video', 'groups', 'events', 'hashtag',
    'pages', 'watch', 'marketplace', 'login', 'help',
}

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# ──────────────────────────────────────────────
# UTILS
# ──────────────────────────────────────────────

def limpiar_email(email: str) -> str | None:
    email = email.lower().strip().rstrip(".")
    dominio = email.split("@")[-1]
    if dominio in EMAIL_BLACKLIST:
        return None
    if len(email) > 100 or len(email) < 6:
        return None
    if any(email.endswith(ext) for ext in [".png", ".jpg", ".gif", ".svg"]):
        return None
    return email


def extraer_emails(texto: str) -> list[str]:
    encontrados = EMAIL_REGEX.findall(texto)
    return [e for e in (limpiar_email(e) for e in encontrados) if e]


def extraer_whatsapp(html: str) -> str | None:
    match = WHATSAPP_REGEX.search(html)
    if match:
        num = re.sub(r'[\s\-\(\)]', '', match.group(1))
        if not num.startswith('+'): num = '+' + num
        if 8 <= len(num) <= 16:
            return num
    wa = re.search(r'wa\.me/(\+?[\d]{7,15})', html)
    if wa:
        num = wa.group(1)
        if not num.startswith('+'): num = '+' + num
        return num
    return None


def normalizar_name(name: str) -> str:
    return re.sub(r'[^\w\s]', ' ', name).strip()


PALABRAS_GENERICAS = {
    'hostal', 'hotel', 'cabañas', 'cabanas', 'restaurant', 'restaurante',
    'hosteria', 'hostería', 'lodge', 'resort', 'apart', 'apart-hotel',
    'complejo', 'turistico', 'turístico', 'ecolodge', 'eco', 'y', 'de',
    'del', 'la', 'el', 'los', 'las', 'en', 'boutique', 'hospedaje',
}

def palabras_clave(name: str) -> set[str]:
    """Extrae palabras significativas del nombre para validar resultados."""
    palabras = re.sub(r'[^\w\s]', ' ', name.lower()).split()
    return {p for p in palabras if len(p) > 3 and p not in PALABRAS_GENERICAS}


def username_es_relevante(url: str, name: str) -> bool:
    """
    Verifica que el username de la URL comparte al menos una palabra clave
    con el nombre del negocio.
    """
    match = re.search(r'(?:instagram|facebook)\.com/([A-Za-z0-9_./-]+)', url)
    if not match:
        return False
    username = match.group(1).lower().replace('_', '').replace('.', '').replace('-', '')
    claves = palabras_clave(name)
    return any(c in username for c in claves)


def ig_url_valida(url: str) -> str | None:
    match = re.search(r'instagram\.com/([a-zA-Z0-9_.]{2,})', url)
    if match:
        username = match.group(1)
        if username.lower() not in IG_USERNAME_BLACKLIST:
            return f"https://www.instagram.com/{username}/"
    return None


def fb_url_valida(url: str) -> str | None:
    match = re.search(r'facebook\.com/([a-zA-Z0-9_./-]{2,})', url)
    if match:
        path = match.group(1).strip('/')
        if path.split('/')[0].lower() not in FB_PATH_BLACKLIST:
            return f"https://www.facebook.com/{path}"
    return None


# ──────────────────────────────────────────────
# ESTRATEGIA 1: SITIO WEB (scorer_web ya lo extrajo)
# ──────────────────────────────────────────────

def obtener_desde_sitio_web(lead: dict) -> tuple[str | None, str | None]:
    """Lee URLs de redes ya guardadas por scorer_web."""
    return lead.get("instagram_url"), lead.get("facebook_url")


# ──────────────────────────────────────────────
# ESTRATEGIA 2: GOOGLE CUSTOM SEARCH API
# ──────────────────────────────────────────────

# Contador de búsquedas Google CSE (límite: 100/día)
_google_cse_count = 0
GOOGLE_CSE_DAILY_LIMIT = 95  # margen de seguridad

def buscar_google_cse(
    name: str,
    city: str,
    red: str,
    cse_key: str,
    cse_id: str,
    verbose: bool
) -> str | None:
    """Busca perfil en Google Custom Search API."""
    global _google_cse_count

    if _google_cse_count >= GOOGLE_CSE_DAILY_LIMIT:
        if verbose:
            print(f"      ⚠️  Google CSE: límite diario alcanzado ({_google_cse_count})")
        return None

    name_limpio = normalizar_name(name)
    dominio = "instagram.com" if red == "instagram" else "facebook.com"

    queries = [
        f'site:{dominio} "{name_limpio}" {city} Chile',
        f'site:{dominio} {name_limpio} {city} Chile',
    ]

    for query in queries:
        try:
            resp = requests.get(
                "https://www.googleapis.com/customsearch/v1",
                params={
                    "key": cse_key,
                    "cx":  cse_id,
                    "q":   query,
                    "num": 5,
                    "hl":  "es",
                    "gl":  "cl",
                },
                timeout=10,
            )
            _google_cse_count += 1

            if resp.status_code == 429:
                if verbose: print(f"      ⚠️  Google CSE: rate limit")
                return None

            resp.raise_for_status()
            items = resp.json().get("items", [])

            for item in items:
                link = item.get("link", "")
                if red == "instagram":
                    url = ig_url_valida(link)
                else:
                    url = fb_url_valida(link)
                if not url:
                    continue
                if not username_es_relevante(url, name):
                    if verbose: print(f"      ⚠️  Descartado (username no coincide): {url}")
                    continue
                if verbose:
                    print(f"      🔍 Google CSE ({_google_cse_count}/100): {url}")
                return url

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                if verbose: print(f"      ⚠️  Google CSE agotado")
                return None
        except Exception as e:
            if verbose: print(f"      ⚠️  Error Google CSE: {e}")

    return None


# ──────────────────────────────────────────────
# ESTRATEGIA 3: BRAVE SEARCH API
# ──────────────────────────────────────────────

def buscar_brave(
    name: str,
    city: str,
    red: str,
    brave_key: str,
    verbose: bool
) -> str | None:
    """Busca perfil usando Brave Search API (2000/mes gratis)."""
    name_limpio = normalizar_name(name)
    dominio = "instagram.com" if red == "instagram" else "facebook.com"

    # Intentar primero con nombre entre comillas (más específico)
    queries = [
        f'site:{dominio} "{name_limpio}" {city} Chile',
        f'site:{dominio} {name_limpio} {city} Chile',
    ]

    for query in queries:
        try:
            resp = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={
                    "q":           query,
                    "count":       5,
                    "search_lang": "es",
                    "country":     "cl",
                },
                headers={
                    "Accept":               "application/json",
                    "Accept-Encoding":      "gzip",
                    "X-Subscription-Token": brave_key,
                },
                timeout=10,
            )

            if resp.status_code == 429:
                if verbose: print(f"      ⚠️  Brave Search: rate limit")
                return None

            resp.raise_for_status()
            results = resp.json().get("web", {}).get("results", [])

            for r in results:
                link = r.get("url", "")
                if red == "instagram":
                    url = ig_url_valida(link)
                else:
                    url = fb_url_valida(link)
                if not url:
                    continue
                if not username_es_relevante(url, name):
                    if verbose: print(f"      ⚠️  Descartado (username no coincide): {url}")
                    continue
                if verbose: print(f"      🦁 Brave ({query[:50]}…): {url}")
                return url

        except Exception as e:
            if verbose: print(f"      ⚠️  Error Brave Search: {e}")

    return None


# ──────────────────────────────────────────────
# ESTRATEGIA 4: PLAYWRIGHT DIRECTO
# ──────────────────────────────────────────────

async def buscar_playwright(
    page,
    name: str,
    city: str,
    red: str,
    verbose: bool
) -> str | None:
    """Último recurso: búsqueda directa en Instagram o Facebook con Playwright."""
    name_limpio = normalizar_name(name)

    if red == "instagram":
        # Búsqueda en Instagram explore
        query = name_limpio.replace(' ', '%20')
        url = f"https://www.instagram.com/explore/search/keyword/?q={query}"
        try:
            await page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            links = await page.eval_on_selector_all(
                'a[href^="/"]',
                'elements => elements.map(e => e.href)'
            )
            for link in links:
                url_valida = ig_url_valida(link)
                if url_valida:
                    if verbose: print(f"      🎭 Playwright Instagram: {url_valida}")
                    return url_valida
        except Exception as e:
            if verbose: print(f"      ⚠️  Playwright Instagram error: {e}")

    else:
        # Búsqueda en Facebook
        query = name_limpio.replace(' ', '+')
        url = f"https://www.facebook.com/search/pages/?q={query}"
        try:
            await page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            links = await page.eval_on_selector_all(
                'a[href*="facebook.com"]',
                'elements => elements.map(e => e.href)'
            )
            for link in links:
                url_valida = fb_url_valida(link)
                if url_valida:
                    if verbose: print(f"      🎭 Playwright Facebook: {url_valida}")
                    return url_valida
        except Exception as e:
            if verbose: print(f"      ⚠️  Playwright Facebook error: {e}")

    return None


# ──────────────────────────────────────────────
# VISITAR PERFIL Y EXTRAER DATOS
# ──────────────────────────────────────────────

async def extraer_datos_perfil(page, url: str, red: str, verbose: bool) -> dict:
    """Visita un perfil y extrae email y WhatsApp."""
    resultado = {"email": None, "whatsapp": None}
    try:
        await page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)
        html  = await page.content()
        texto = await page.inner_text("body")

        emails = extraer_emails(texto)
        if emails:
            resultado["email"] = emails[0]
            if verbose: print(f"      📧 Email en {red}: {emails[0]}")

        wa = extraer_whatsapp(html)
        if wa:
            resultado["whatsapp"] = wa
            if verbose: print(f"      💬 WhatsApp en {red}: {wa}")

    except Exception as e:
        if verbose: print(f"      ⚠️  Error visitando {red}: {e}")
    return resultado


# ──────────────────────────────────────────────
# BÚSQUEDA CON CASCADA
# ──────────────────────────────────────────────

async def buscar_red(
    page,
    lead: dict,
    red: str,
    cse_key: str | None,
    cse_id: str | None,
    brave_key: str | None,
    verbose: bool,
    sb=None,
) -> str | None:
    """
    Busca el perfil de una red social usando las 4 estrategias en cascada.
    Retorna la URL del perfil o None.
    """
    name = lead.get("name", "")
    city = lead.get("city", "")
    campo  = "instagram_url" if red == "instagram" else "facebook_url"

    # 1. Sitio web — leer de business_socials
    socials = get_socials(sb, lead["id"]) if sb else []
    url = get_social_url(socials, red)
    if url:
        if verbose: print(f"      ✅ {red} desde sitio web: {url}")
        return url

    # 2. Google CSE
    if cse_key and cse_id:
        url = buscar_google_cse(name, city, red, cse_key, cse_id, verbose)
        if url:
            return url
        if verbose: print(f"      — Google CSE: no encontrado")

    # 3. Brave Search
    if brave_key:
        url = buscar_brave(name, city, red, brave_key, verbose)
        if url:
            return url
        if verbose: print(f"      — Brave: no encontrado")

    # 4. Playwright directo
    if verbose: print(f"      🎭 Intentando Playwright directo...")
    url = await buscar_playwright(page, name, city, red, verbose)
    return url


# ──────────────────────────────────────────────
# PROCESAMIENTO DE UN LEAD
# ──────────────────────────────────────────────

async def procesar_lead(
    page,
    lead: dict,
    cse_key: str | None,
    cse_id: str | None,
    brave_key: str | None,
    verbose: bool,
) -> dict:
    updates = {}
    name  = lead.get("name", "")

    # ── Instagram ─────────────────────────────────
    print(f"   📸 Instagram...")
    ig_url = await buscar_red(page, lead, "instagram", cse_key, cse_id, brave_key, verbose)

    if ig_url:
        updates["instagram_url"] = ig_url
        ig_data = await extraer_datos_perfil(page, ig_url, "Instagram", verbose)
        if ig_data.get("email") and not lead.get("email"):
            updates["email"]        = ig_data["email"]
            updates["email_source"] = "instagram"
        if ig_data.get("whatsapp") and not lead.get("whatsapp"):
            updates["whatsapp"]        = ig_data["whatsapp"]
            updates["whatsapp_source"] = "instagram"
    else:
        print(f"   📸 Instagram: no encontrado")

    # ── Facebook ──────────────────────────────────
    print(f"   📘 Facebook...")
    fb_url = await buscar_red(page, lead, "facebook", cse_key, cse_id, brave_key, verbose)

    if fb_url:
        updates["facebook_url"] = fb_url
        fb_data = await extraer_datos_perfil(page, fb_url, "Facebook", verbose)
        if fb_data.get("email") and not lead.get("email") and not updates.get("email"):
            updates["email"]        = fb_data["email"]
            updates["email_source"] = "facebook"
        if fb_data.get("whatsapp") and not lead.get("whatsapp") and not updates.get("whatsapp"):
            updates["whatsapp"]        = fb_data["whatsapp"]
            updates["whatsapp_source"] = "facebook"
    else:
        print(f"   📘 Facebook: no encontrado")

    # ── missing_fields ────────────────────────────
    email_ok    = updates.get("email")    or lead.get("email")
    whatsapp_ok = updates.get("whatsapp") or lead.get("whatsapp") or lead.get("phone")
    missing     = []
    if not email_ok:    missing.append("email")
    if not whatsapp_ok: missing.append("whatsapp")

    updates["missing_fields"] = missing
    updates["needs_review"]   = len(missing) > 0

    return updates


# ──────────────────────────────────────────────
# PIPELINE PRINCIPAL
# ──────────────────────────────────────────────

async def run(env: str, max_leads: int | None, verbose: bool, slug: str | None):
    load_dotenv(f".env.{env}")

    cse_key   = os.getenv("GOOGLE_CSE_KEY")
    cse_id    = os.getenv("GOOGLE_CSE_ID")
    brave_key = os.getenv("BRAVE_SEARCH_KEY")

    sb = get_client(env=env)

    # Todos los leads — enriquecer redes independiente de si tienen email
    if slug:
        lead = get_business_by_slug(sb, slug)
        leads = [lead] if lead else []
    else:
        todos = []
        for estado in ["new", "analyzed", "contacted"]:
            todos += get_leads_por_estado(sb, estado)
        vistos = set()
        leads  = []
        for l in todos:
            if l["place_id"] not in vistos:
                vistos.add(l["place_id"])
                leads.append(l)

    if max_leads:
        leads = leads[:max_leads]

    total = len(leads)
    print(f"\n📱 scorer_redes | {total} leads | environment '{env}'")
    print(f"   Estrategias: sitio web → Google CSE → Brave → Playwright")
    print(f"   Google CSE:  {'✅ configurado' if cse_key and cse_id else '❌ no configurado'}")
    print(f"   Brave:       {'✅ configurado' if brave_key else '❌ no configurado'}\n")

    tracker = APITracker(env=env)
    con_ig = con_fb = emails_nuevos = wa_nuevos = pendientes = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 800},
            locale="es-CL",
            extra_http_headers={"Accept-Language": "es-CL,es;q=0.9"},
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = await context.new_page()

        for i, lead in enumerate(leads, 1):
            name = lead.get("name", f"Lead {i}")
            print(f"[{i}/{total}] {name}")

            try:
                updates = await procesar_lead(
                    page, lead, cse_key, cse_id, brave_key, verbose
                )

                # Guardar redes en business_data y business_socials
                dm = DataManager(sb, lead["id"]) if lead.get("id") else None
                socials_guardadas = updates.pop("_socials", [])
                for s in socials_guardadas:
                    upsert_social(sb, lead["id"],
                                  network=s["network"], url=s["url"],
                                  source=s.get("source", "cse"))

                # Guardar emails y teléfonos en tablas dedicadas
                if updates.get("email"):
                    save_email(sb, lead["id"],
                               email=updates["email"],
                               source=updates.get("email_source", "redes"),
                               found_at_step="scorer_redes")
                if updates.get("whatsapp"):
                    save_phone(sb, lead["id"],
                               phone=updates["whatsapp"],
                               type="whatsapp",
                               source=updates.get("whatsapp_source", "redes"),
                               found_at_step="scorer_redes")

                # Actualizar needs_review en businesses
                from contact_manager import missing_fields as _missing
                missing = _missing(sb, lead["id"])
                update_business(sb, lead["place_id"], {
                    "needs_review": len(missing) > 0,
                    "missing_fields": missing,
                })

                if updates.get("instagram_url"):
                    con_ig += 1
                    upsert_social(sb, lead["id"], "instagram", updates["instagram_url"], source="cse")
                    if dm: dm.set("social", "instagram_url", updates["instagram_url"], source="cse", step="scorer_redes")
                    print(f"   ✅ Instagram: {updates['instagram_url']}")
                if updates.get("facebook_url"):
                    con_fb += 1
                    upsert_social(sb, lead["id"], "facebook", updates["facebook_url"], source="cse")
                    if dm: dm.set("social", "facebook_url", updates["facebook_url"], source="cse", step="scorer_redes")
                    print(f"   ✅ Facebook:  {updates['facebook_url']}")
                if updates.get("email"):
                    emails_nuevos += 1
                    print(f"   📧 Email: {updates['email']} ({updates.get('email_source','')})")
                if updates.get("whatsapp"):
                    wa_nuevos += 1
                    print(f"   💬 WhatsApp: {updates['whatsapp']}")
                if updates.get("needs_review"):
                    pendientes += 1

            except Exception as e:
                print(f"   💥 Error: {e}")

            # Track API usage
            tracker.track("google_cse", used=_google_cse_count - getattr(tracker, "_last_cse", 0))
            tracker._last_cse = _google_cse_count
            await asyncio.sleep(DELAY_ENTRE_LEADS)

        await browser.close()

    tracker.resumen('scorer_redes.py')

    print(f"\n{'='*55}")
    print(f"✅ scorer_redes completado")
    print(f"   Instagram:      {con_ig}/{total}")
    print(f"   Facebook:       {con_fb}/{total}")
    print(f"   Emails nuevos:  {emails_nuevos}")
    print(f"   WhatsApp nuevos:{wa_nuevos}")
    print(f"   Pendientes:     {pendientes} → completar en dashboard")
    print(f"   Google CSE uso: {_google_cse_count}/100 búsquedas hoy")
    print(f"{'='*55}")
    print(f"\n➡️  Siguiente: python3 scorer_lighthouse.py --env {env} --verbose")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="innovando-scripts · Scorer Redes")
    parser.add_argument("--env",     required=True, choices=["test", "prd"])
    parser.add_argument("--max",     type=int, default=None)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--slug",    default=None)
    args = parser.parse_args()

    asyncio.run(run(args.env, args.max, args.verbose, args.slug))