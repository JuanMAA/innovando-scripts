"""
scorer_web.py — innovando-scripts · Etapa 1
Analiza el sitio web de cada lead y extrae email y WhatsApp.
Usa Playwright para manejar sitios con JavaScript.

Uso:
    python scorer_web.py --env test
    python scorer_web.py --env test --max 10 --verbose
    python scorer_web.py --env test --slug hostal-vista-al-mar

Requiere:
    pip install playwright supabase python-dotenv
    playwright install chromium
"""

import argparse
import asyncio
import re
import time

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from data_manager import DataManager
from contact_manager import (
    save_email, save_emails_bulk,
    save_phone, save_phones_bulk,
    extraer_emails_de_texto, extraer_whatsapp_de_html
)
from socials_manager import (
    detectar_network, extraer_links_de_html,
    upsert_social, upsert_socials_bulk, NOT_REAL_WEBSITE
)
from supabase_client import (
    get_client, update_business,
    get_leads_sin_email, get_business_by_slug
)


# ──────────────────────────────────────────────
# CONFIGURACIÓN
# ──────────────────────────────────────────────

TIMEOUT_MS      = 12000
TIMEOUT_FAST_MS = 6000
DELAY_ENTRE_LEADS = 1.0

SUBPAGINAS = [
    "/contacto", "/contact", "/contactenos",
    "/about", "/nosotros", "/quienes-somos",
    "/reservas", "/reservations", "/info",
]

EMAIL_REGEX = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE
)

WHATSAPP_REGEX = re.compile(
    r'(?:wa\.me|api\.whatsapp\.com/send[?]phone=|whatsapp://send[?]phone=)'
    r'[/]?(\+?[\d\s\-\(\)]{7,15})',
    re.IGNORECASE
)

EMAIL_BLACKLIST = {
    "example.com", "ejemplo.com", "correo.com", "tuempresa.com",
    "yoursite.com", "domain.com", "test.com", "email.com",
    "sentry.io", "wixpress.com", "squarespace.com", "wix.com",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
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


def limpiar_whatsapp(numero: str) -> str | None:
    """Limpia y normaliza un número de WhatsApp."""
    numero = re.sub(r'[\s\-\(\)]', '', numero)
    if not numero.startswith('+'):
        numero = '+' + numero
    if len(numero) < 8 or len(numero) > 16:
        return None
    return numero


def extraer_emails(texto: str) -> list[str]:
    encontrados = EMAIL_REGEX.findall(texto)
    return [e for e in (limpiar_email(e) for e in encontrados) if e]


def extraer_whatsapp(html: str) -> str | None:
    match = WHATSAPP_REGEX.search(html)
    if match:
        return limpiar_whatsapp(match.group(1))
    # Buscar también links wa.me/NUMERO
    wa_match = re.search(r'wa\.me/(\+?[\d]{7,15})', html)
    if wa_match:
        return limpiar_whatsapp(wa_match.group(1))
    return None


def normalizar_url(url: str) -> str:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


# ──────────────────────────────────────────────
# ANÁLISIS DE SITIO WEB
# ──────────────────────────────────────────────

async def analizar_pagina(page, url: str, timeout: int = TIMEOUT_MS) -> dict:
    """Visita una URL y extrae emails, WhatsApp y métricas."""
    resultado = {
        "emails": [],
        "whatsapp": None,
        "instagram_url": None,
        "facebook_url": None,
        "ssl": url.startswith("https://"),
        "mobile": False,
        "velocidad_ms": None,
        "carga_ok": False,
    }

    try:
        t0 = time.time()
        resp = await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
        resultado["velocidad_ms"] = int((time.time() - t0) * 1000)
        resultado["carga_ok"] = resp is not None and resp.status < 400
    except PlaywrightTimeout:
        return resultado
    except Exception:
        return resultado

    if not resultado["carga_ok"]:
        return resultado

    try:
        html  = await page.content()
        texto = await page.inner_text("body")
    except Exception:
        return resultado

    # Emails
    emails_texto = extraer_emails(texto)
    emails_mailto = [
        limpiar_email(e) for e in
        re.findall(r'mailto:([^\s"\'<>]+)', html, re.IGNORECASE)
        if limpiar_email(e)
    ]
    resultado["emails"] = list(dict.fromkeys(emails_texto + emails_mailto))

    # WhatsApp
    resultado["whatsapp"] = extraer_whatsapp(html)

    # Mobile-friendly
    resultado["mobile"] = bool(
        re.search(r'<meta[^>]+name=["\']viewport["\']', html, re.IGNORECASE)
    )

    return resultado


async def buscar_en_subpaginas(page, base_url: str, verbose: bool) -> tuple[list, str | None]:
    """Visita subpáginas de contacto buscando email y WhatsApp."""
    emails = []
    whatsapp = None
    base = base_url.rstrip("/")

    for subpagina in SUBPAGINAS:
        url_sub = base + subpagina
        try:
            resp = await page.goto(url_sub, timeout=TIMEOUT_FAST_MS, wait_until="domcontentloaded")
            if resp and resp.status < 400:
                html  = await page.content()
                texto = await page.inner_text("body")
                sub_emails = extraer_emails(texto)
                sub_wa = extraer_whatsapp(html)

                if sub_emails:
                    if verbose:
                        print(f"      📧 Emails en {subpagina}: {sub_emails}")
                    emails.extend(sub_emails)

                if sub_wa and not whatsapp:
                    if verbose:
                        print(f"      💬 WhatsApp en {subpagina}: {sub_wa}")
                    whatsapp = sub_wa

                if emails and whatsapp:
                    break
        except Exception:
            pass

    return emails, whatsapp


async def buscar_en_google(page, name: str, city: str, verbose: bool) -> list[str]:
    """Último recurso: busca email en Google."""
    emails = []
    query = f"{name} {city} email contacto"
    url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
    try:
        await page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        texto = await page.inner_text("body")
        emails = extraer_emails(texto)
        if verbose and emails:
            print(f"      📧 Email en Google: {emails}")
    except Exception:
        pass
    return emails


# ──────────────────────────────────────────────
# PROCESAMIENTO DE UN LEAD
# ──────────────────────────────────────────────


async def extraer_links_linktree(page, url: str, verbose: bool) -> dict:
    """
    Visita un perfil de Linktree (u otro agregador de links) y extrae:
    - Instagram, Facebook, WhatsApp, email
    Retorna un dict con los campos encontrados.
    """
    resultado = {}
    try:
        await page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        html  = await page.content()
        texto = await page.inner_text("body")

        # Instagram
        ig = re.search(r'https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]+)/?', html)
        if ig:
            username = ig.group(1)
            blacklist = {'p', 'reel', 'reels', 'stories', 'explore'}
            if username.lower() not in blacklist:
                resultado["instagram_url"] = f"https://www.instagram.com/{username}/"
                if verbose: print(f"      📸 Instagram en Linktree: @{username}")

        # Facebook
        fb = re.search(r'https?://(?:www\.)?facebook\.com/([A-Za-z0-9_./-]+)/?', html)
        if fb:
            path = fb.group(1).strip('/')
            blacklist = {'sharer', 'share', 'dialog', 'plugins', 'tr'}
            if path.split('/')[0].lower() not in blacklist:
                resultado["facebook_url"] = f"https://www.facebook.com/{path}"
                if verbose: print(f"      📘 Facebook en Linktree: {path}")

        # WhatsApp
        wa = re.search(r'wa\.me/(\+?[\d]{7,15})', html)
        if wa:
            num = wa.group(1)
            if not num.startswith('+'): num = '+' + num
            resultado["whatsapp"] = num
            resultado["whatsapp_source"] = "linktree"
            if verbose: print(f"      💬 WhatsApp en Linktree: {num}")

        # Email
        emails = extraer_emails(texto)
        if emails:
            resultado["email"] = emails[0]
            resultado["email_source"] = "linktree"
            if verbose: print(f"      📧 Email en Linktree: {emails[0]}")

        # Guardar también la URL de Linktree como referencia
        resultado["linktree_url"] = url

    except Exception as e:
        if verbose: print(f"      ⚠️  Error analizando Linktree: {e}")

    return resultado

def detectar_red_social_como_web(website: str) -> tuple[str | None, str | None]:
    """
    Detecta si el campo website es en realidad una red social.
    Retorna (red, url_limpia) o (None, None) si es un sitio web real.

    Casos detectados:
    - instagram.com/usuario     → ("instagram", url)
    - facebook.com/pagina       → ("facebook", url)
    - linktr.ee/usuario         → ("linktree", url)  # no cuenta como sitio web
    - wa.me/numero              → ("whatsapp", url)
    """
    if not website:
        return None, None

    w = website.lower().strip()

    if "instagram.com" in w:
        return "instagram", website
    if "facebook.com" in w:
        return "facebook", website
    if "wa.me" in w or "api.whatsapp.com" in w:
        return "whatsapp", website
    # Agregadores de links — visitar y extraer redes internas
    agregadores = [
        "linktr.ee", "linktree.com",   # Linktree
        "bio.link",                     # Bio.link
        "beacons.ai",                   # Beacons
        "solo.to",                      # Solo.to
        "bento.me",                     # Bento
        "campsite.bio",                 # Campsite
        "taplink.cc",                   # Taplink
        "allmylinks.com",               # All My Links
    ]
    if any(ag in w for ag in agregadores):
        return "linktree", website

    return None, None


async def procesar_lead(page, lead: dict, verbose: bool) -> dict:
    """Analiza el sitio web de un lead y extrae contacto."""
    website = (lead.get("website") or "").strip()

    updates = {
        "email": None,
        "email_source": None,
        "whatsapp": None,
        "whatsapp_source": None,
        "needs_review": True,
        "missing_fields": [],
    }

    # ── Detectar si el "sitio web" es una red social ──────────
    red_detectada = detectar_network(website)
    url_red = website if red_detectada else None

    if red_detectada and red_detectada in NOT_REAL_WEBSITE:
        if verbose:
            print(f"   ⚠️  '{website}' es {red_detectada}, no un sitio web real")

        # Guardar como red social y limpiar website
        if red_detectada == "instagram":
            updates["instagram_url"] = url_red
            print(f"   📸 Registrado como Instagram: {url_red}")
        elif red_detectada == "facebook":
            updates["facebook_url"] = url_red
            print(f"   📘 Registrado como Facebook: {url_red}")
        elif red_detectada == "whatsapp":
            # Extraer número del link wa.me
            import re as _re
            wa_match = _re.search(r'wa\.me/(\+?[\d]{7,15})', url_red)
            if wa_match:
                updates["whatsapp"] = wa_match.group(1)
                updates["whatsapp_source"] = "web"
                print(f"   💬 WhatsApp extraído: {updates['whatsapp']}")

        # Si es Linktree u otro agregador → visitarlo y extraer links de redes
        if red_detectada == "linktree":
            print(f"   🔗 Linktree detectado — extrayendo links...")
            try:
                await page.goto(url_red, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
                await page.wait_for_timeout(2000)
                html  = await page.content()
                texto = await page.inner_text("body")

                # Extraer Instagram
                ig_match = re.search(
                    r'https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]+)/?',
                    html
                )
                if ig_match:
                    username = ig_match.group(1)
                    if username not in ("p","reel","stories","explore","tv","share"):
                        updates["instagram_url"] = f"https://www.instagram.com/{username}/"
                        print(f"   📸 Instagram en Linktree: {updates['instagram_url']}")

                # Extraer Facebook
                fb_match = re.search(
                    r'https?://(?:www\.)?facebook\.com/([A-Za-z0-9_./-]+)/?',
                    html
                )
                if fb_match:
                    path = fb_match.group(1).rstrip("/")
                    blacklist = {"sharer","share","dialog","plugins","tr"}
                    if path.split("/")[0].lower() not in blacklist:
                        updates["facebook_url"] = f"https://www.facebook.com/{path}"
                        print(f"   📘 Facebook en Linktree: {updates['facebook_url']}")

                # Extraer email y WhatsApp del Linktree también
                emails = extraer_emails(texto)
                if emails:
                    updates["email"] = emails[0]
                    updates["email_source"] = "linktree"
                    print(f"   📧 Email en Linktree: {emails[0]}")

                wa = extraer_whatsapp(html)
                if wa:
                    updates["whatsapp"] = wa
                    updates["whatsapp_source"] = "linktree"
                    print(f"   💬 WhatsApp en Linktree: {wa}")

            except Exception as e:
                if verbose:
                    print(f"   ⚠️  Error visitando Linktree: {e}")

        # Marcar que NO tiene sitio web propio real
        updates["website"] = None
        website = ""

    tiene_web = bool(website)
    todos_emails = []
    todos_whatsapps = []
    whatsapp_encontrado = None
    analisis = {}

    # ── Estrategia 1: Sitio web propio ──────────
    if tiene_web:
        url = normalizar_url(website)
        if verbose:
            print(f"   🌐 Analizando: {url}")

        analisis = await analizar_pagina(page, url)
        todos_emails.extend(analisis["emails"])
        if analisis["whatsapp"]:
            whatsapp_encontrado = analisis["whatsapp"]
            updates["whatsapp_source"] = "web"
        if analisis.get("instagram_url"):
            updates["instagram_url"] = analisis["instagram_url"]
            if verbose:
                print(f"      📸 Instagram encontrado: {analisis['instagram_url']}")
        if analisis.get("facebook_url"):
            updates["facebook_url"] = analisis["facebook_url"]
            if verbose:
                print(f"      📘 Facebook encontrado: {analisis['facebook_url']}")

        if not todos_emails:
            if verbose:
                print(f"   🔍 Buscando en subpáginas...")
            sub_emails, sub_wa = await buscar_en_subpaginas(page, url, verbose)
            todos_emails.extend(sub_emails)
            if sub_wa and not whatsapp_encontrado:
                whatsapp_encontrado = sub_wa
                updates["whatsapp_source"] = "web"

    # ── Estrategia 2: Google Search ─────────────
    if not todos_emails:
        if verbose:
            print(f"   🔍 Buscando en Google...")
        google_emails = await buscar_en_google(
            page,
            lead.get("name", ""),
            lead.get("city", ""),
            verbose
        )
        if google_emails:
            todos_emails.extend(google_emails)
            updates["email_source"] = "google"

    # Consolidar resultados
    if todos_emails:
        updates["email"] = todos_emails[0]
        if not updates.get("email_source"):
            updates["email_source"] = "web"

    if whatsapp_encontrado:
        updates["whatsapp"] = whatsapp_encontrado

    # Determinar campos faltantes
    missing_fields = []
    if not updates["email"]:
        missing_fields.append("email")
    if not updates["whatsapp"] and not lead.get("phone"):
        missing_fields.append("whatsapp")

    updates["missing_fields"] = missing_fields
    updates["needs_review"] = len(missing_fields) > 0

    # Pasar datos web al llamador
    updates["_ssl"]          = analisis.get("ssl")
    updates["_mobile"]       = analisis.get("mobile")
    updates["_load_time_ms"] = analisis.get("velocidad_ms")

    return updates


# ──────────────────────────────────────────────
# PIPELINE PRINCIPAL
# ──────────────────────────────────────────────

async def run(env: str, max_leads: int | None, verbose: bool, slug: str | None):
    sb = get_client(env=env)

    # Obtener leads a procesar
    if slug:
        lead = get_business_by_slug(sb, slug)
        leads = [lead] if lead else []
    else:
        leads = get_leads_sin_email(sb)

    if max_leads:
        leads = leads[:max_leads]

    total = len(leads)
    print(f"\n🔍 Procesando {total} leads en environment '{env}'...")

    con_email = 0
    con_whatsapp = 0
    requieren_relleno = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 800},
            locale="es-CL",
        )
        page = await context.new_page()

        # Bloquear recursos innecesarios para mayor velocidad
        await page.route(
            "**/*.{png,jpg,jpeg,gif,svg,webp,ico,woff,woff2,ttf,eot}",
            lambda r: r.abort()
        )

        for i, lead in enumerate(leads, 1):
            name = lead.get("name", f"Lead {i}")
            print(f"\n[{i}/{total}] {name}")

            try:
                updates = await procesar_lead(page, lead, verbose)

                # Extraer redes pendientes antes de guardar en businesses
                pending_socials = updates.pop("_pending_socials", [])

                # Guardar redes en business_socials
                if pending_socials:
                    guardadas = upsert_socials_bulk(sb, lead["id"], pending_socials)
                    if verbose:
                        for s in pending_socials:
                            print(f"   ✅ {s['network']}: {s['url']}")

                # Extraer datos web internos antes de limpiar
                ssl          = updates.pop("_ssl", None)
                mobile       = updates.pop("_mobile", None)
                load_time_ms = updates.pop("_load_time_ms", None)

                # Guardar datos web en business_data
                if lead.get("id"):
                    dm = DataManager(sb, lead["id"])
                    dm.set_many("web", {
                        "website":         updates.get("website") or lead.get("website"),
                        "ssl":             ssl,
                        "mobile_friendly": mobile,
                        "load_time_ms":    load_time_ms,
                    }, source="scorer_web", step="scorer_web")
                    if updates.get("instagram_url"):
                        dm.set("social", "instagram_url_found", updates["instagram_url"], source="web", step="scorer_web")
                    if updates.get("facebook_url"):
                        dm.set("social", "facebook_url_found", updates["facebook_url"], source="web", step="scorer_web")

                # Limpiar campos que no van en businesses
                updates.pop("instagram_url", None)
                updates.pop("facebook_url", None)
                updates.pop("web_linktree", None)
                updates.pop("linktree_url", None)

                update_business(sb, lead["place_id"], updates)

                email = updates.get("email", "")
                wa    = updates.get("whatsapp", "")

                if email:
                    con_email += 1
                    print(f"   ✅ Email: {email} ({updates.get('email_source', '')})")
                else:
                    print(f"   ❌ Sin email")

                if wa:
                    con_whatsapp += 1
                    print(f"   💬 WhatsApp: {wa}")

                if updates.get("needs_review"):
                    requieren_relleno += 1
                    print(f"   ⚠️  Requiere relleno manual: {updates.get('missing_fields', [])}")

            except Exception as e:
                print(f"   💥 Error: {e}")

            await asyncio.sleep(DELAY_ENTRE_LEADS)

        await browser.close()

    print(f"\n{'='*55}")
    print(f"✅ scorer_web completado")
    print(f"   Procesados:         {total}")
    print(f"   Con email:          {con_email} ({int(con_email/total*100) if total else 0}%)")
    print(f"   Con WhatsApp:       {con_whatsapp}")
    print(f"   Requieren relleno:  {requieren_relleno} → completar en dashboard")
    print(f"{'='*55}")
    print(f"\n➡️  Siguiente paso: python scorer_redes.py --env {env}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="innovando-scripts · Scorer Web")
    parser.add_argument("--env",     required=True, choices=["test", "prd"])
    parser.add_argument("--max",     type=int, default=None)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--slug",    default=None)
    args = parser.parse_args()

    asyncio.run(run(args.env, args.max, args.verbose, args.slug))