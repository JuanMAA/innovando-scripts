"""
scorer_huella.py — innovando-scripts · Etapa 2
Análisis completo de huella digital del negocio.

Módulos:
  H1 · Google Maps          → fichas duplicadas, datos inconsistentes
  H2 · Directorios locales  → Páginas Amarillas, Yelp, Foursquare, Hotfrog
  H3 · Plataformas viaje    → TripAdvisor, Booking, Expedia
  H4 · Redes sociales       → perfiles huérfanos, nombres incorrectos
  H5 · Medios y blogs       → menciones negativas, info incorrecta
  H6 · NAP consistency      → Name/Address/Phone coherente en todos los sitios

Por cada problema → 3 niveles de solución:
  Fácil  → Tutorial DIY gratis
  Media  → Tutorial DIY $3
  Difícil → Servicio Innovando

Uso:
    python scorer_huella.py --env test
    python scorer_huella.py --env test --slug hostal-vista-al-mar --verbose
    python scorer_huella.py --env test --forzar

Requiere:
    pip install playwright requests supabase python-dotenv
    playwright install chromium
"""

import argparse
import asyncio
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from supabase_client import (
    get_client,
    update_business,
    get_business_by_slug,
    now_iso,
)
from contact_manager import get_emails, get_phones
from socials_manager import get_socials, get_social_url
from data_manager import DataManager
from search_client import SearchClient
from api_usage_tracker import APITracker

# ──────────────────────────────────────────────
# CONFIGURACIÓN
# ──────────────────────────────────────────────

TIMEOUT_MS        = 12000
DELAY_ENTRE_LEADS = 2.0

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Directorios locales a verificar
DIRECTORIOS = [
    {"name": "yelp",          "domain": "yelp.com",            "region": ["us", "cl"]},
    {"name": "foursquare",    "domain": "foursquare.com",       "region": ["all"]},
    {"name": "tripadvisor",   "domain": "tripadvisor.com",      "region": ["all"]},
    {"name": "páginas_amarillas", "domain": "paginasamarillas.cl", "region": ["cl"]},
    {"name": "zoominfo",      "domain": "zoominfo.com",         "region": ["all"]},
    {"name": "hotfrog",       "domain": "hotfrog.com",          "region": ["all"]},
]

# Plataformas de viaje a verificar
PLATAFORMAS_VIAJE = [
    "booking.com",
    "airbnb.com",
    "tripadvisor.com",
    "expedia.com",
    "despegar.com",
]


# ──────────────────────────────────────────────
# PROBLEMA — estructura estándar
# ──────────────────────────────────────────────

def problema(
    modulo: str,
    tipo: str,
    descripcion: str,
    impacto: str,
    dificultad: str,
    solucion_diy: str | None,
    solucion_innovando: str | None,
    precio_diy: float = 0,
    precio_innovando: float | None = None,
    url_encontrada: str | None = None,
) -> dict:
    return {
        "modulo":              modulo,
        "tipo":                tipo,
        "descripcion":         descripcion,
        "impacto":             impacto,
        "difficulty":          dificultad,  # easy | medium | hard
        "solucion_diy":        solucion_diy,
        "precio_diy":          precio_diy,
        "solucion_innovando":  solucion_innovando,
        "precio_innovando":    precio_innovando,
        "url":                 url_encontrada,
    }


# ──────────────────────────────────────────────
# H1 · GOOGLE MAPS — fichas duplicadas y datos
# ──────────────────────────────────────────────

async def analizar_google_maps(page, business: dict, api_key: str, verbose: bool) -> dict:
    """Detecta fichas duplicadas y problemas en la ficha de Google Maps."""
    problemas = []
    score     = 30  # score máximo H1

    name    = business.get("name", "")
    city    = business.get("city", "")
    address = business.get("address", "")

    # ── Detectar fichas duplicadas via Google Places Text Search ──
    try:
        resp = requests.get(
            "https://maps.googleapis.com/maps/api/place/textsearch/json",
            params={
                "query":    f"{name} {city}",
                "key":      api_key,
                "language": "es",
            },
            timeout=10,
        )
        data = resp.json()
        results = data.get("results", [])
        our_place_id = business.get("place_id", "")

        fichas_duplicadas = [
            r for r in results
            if r.get("place_id") != our_place_id
            and name.lower()[:10] in r.get("name", "").lower()
        ]

        if fichas_duplicadas:
            score -= 15
            problemas.append(problema(
                modulo="h1_google_maps",
                tipo="duplicate_listing",
                descripcion=f"Se detectaron {len(fichas_duplicadas)} ficha(s) duplicada(s) de {name} en Google Maps.",
                impacto="Las reseñas se dividen entre fichas — menor visibilidad y confusión para el cliente.",
                dificultad="medium",
                solucion_diy="Reportar la ficha duplicada en Google Maps Business Profile → 'Sugerir una edición' → 'Este lugar está cerrado o no existe'.",
                solucion_innovando="Gestionamos la fusión o eliminación de fichas duplicadas por vos.",
                precio_diy=3,
                precio_innovando=35000,
                url_encontrada=f"https://www.google.com/maps/search/{name.replace(' ', '+')}+{city.replace(' ', '+')}",
            ))

    except Exception as e:
        if verbose: print(f"      ⚠️  Error detectando duplicados: {e}")

    # ── Verificar consistencia de datos básicos ──
    rating      = business.get("rating", 0) or 0
    num_reviews = business.get("num_reviews", 0) or 0
    num_photos  = business.get("num_photos", 0) or 0
    has_hours   = business.get("has_hours", False)
    has_desc    = business.get("has_description", False)

    if num_photos < 5:
        score -= 5
        problemas.append(problema(
            modulo="h1_google_maps",
            tipo="insufficient_photos",
            descripcion=f"Solo {num_photos} foto(s) en Google Maps (recomendado: mínimo 10).",
            impacto="Las fichas con 10+ fotos reciben 520% más llamadas.",
            dificultad="easy",
            solucion_diy="Agregar fotos desde Google Maps → tu ficha → 'Agregar fotos'. Subir mínimo 10 fotos de alta calidad.",
            solucion_innovando=None,
            precio_diy=0,
        ))

    if not has_hours:
        score -= 5
        problemas.append(problema(
            modulo="h1_google_maps",
            tipo="missing_hours",
            descripcion="Sin horarios publicados en Google Maps.",
            impacto="Los viajeros no saben cuándo está abierto — muchos eligen otro negocio.",
            dificultad="easy",
            solucion_diy="Google Business Profile → 'Editar perfil' → 'Horarios'. Completar todos los días.",
            solucion_innovando=None,
            precio_diy=0,
        ))

    if not has_desc:
        score -= 5
        problemas.append(problema(
            modulo="h1_google_maps",
            tipo="missing_description",
            descripcion="Sin descripción editorial en Google Maps.",
            impacto="No comunica qué hace especial al negocio — menor tasa de conversión.",
            dificultad="easy",
            solucion_diy="Google Business Profile → 'Editar perfil' → 'Descripción'. Escribir 200-300 palabras sobre el negocio.",
            solucion_innovando="Redactamos la descripción optimizada para SEO local.",
            precio_diy=0,
            precio_innovando=15000,
        ))

    if num_reviews < 10:
        score -= 5
        problemas.append(problema(
            modulo="h1_google_maps",
            tipo="insufficient_reviews",
            descripcion=f"Solo {num_reviews} reseña(s) en Google Maps (recomendado: mínimo 20).",
            impacto="Menos de 10 reseñas genera desconfianza. Los negocios con 20+ convierten 3x más.",
            dificultad="medium",
            solucion_diy="Pedir reseñas a clientes satisfechos via WhatsApp con link directo a la ficha.",
            solucion_innovando="Creamos una campaña de captación de reseñas con seguimiento automatizado.",
            precio_diy=3,
            precio_innovando=49000,
        ))

    return {"score": max(0, score), "problemas": problemas}


# ──────────────────────────────────────────────
# H2 · DIRECTORIOS LOCALES
# ──────────────────────────────────────────────

async def analizar_directorios(page, business: dict, sc: SearchClient, verbose: bool) -> dict:
    """Verifica presencia y consistencia en directorios locales."""
    problemas_detectados = []
    score    = 20
    name     = business.get("name", "")
    city     = business.get("city", "")
    phone    = business.get("phone", "")
    address  = business.get("address", "")

    encontrados    = []
    no_encontrados = []
    inconsistentes = []

    for directorio in DIRECTORIOS:
        domain = directorio["domain"]
        dname  = directorio["name"]

        # Buscar via SearchClient (CSE → Brave automático)
        url_encontrada = sc.buscar_en_sitio(name, city, domain)

        if url_encontrada:
            encontrados.append({"directorio": dname, "url": url_encontrada})

            # Verificar consistencia NAP si encontramos la ficha
            try:
                await page.goto(url_encontrada, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
                await page.wait_for_timeout(1500)
                texto = await page.inner_text("body")

                # Verificar que el teléfono coincide
                if phone and phone.replace(" ", "").replace("-", "")[-8:] not in texto.replace(" ", "").replace("-", ""):
                    inconsistentes.append({
                        "directorio": dname,
                        "campo": "phone",
                        "url": url_encontrada,
                    })
            except Exception:
                pass
        else:
            no_encontrados.append(dname)

    # Evaluar resultados
    if len(no_encontrados) >= 3:
        score -= 10
        problemas_detectados.append(problema(
            modulo="h2_directorios",
            tipo="missing_directory_listings",
            descripcion=f"No aparece en {len(no_encontrados)} directorios importantes: {', '.join(no_encontrados[:3])}.",
            impacto="Los directorios locales mejoran el SEO local y la visibilidad en búsquedas.",
            dificultad="medium",
            solucion_diy="Crear perfiles en cada directorio con los mismos datos (NAP consistency).",
            solucion_innovando="Creamos y optimizamos tu presencia en los principales directorios.",
            precio_diy=3,
            precio_innovando=49000,
        ))

    if inconsistentes:
        score -= 10
        problemas_detectados.append(problema(
            modulo="h2_directorios",
            tipo="nap_inconsistency",
            descripcion=f"Datos inconsistentes (teléfono/dirección) en {len(inconsistentes)} directorio(s): {', '.join(d['directorio'] for d in inconsistentes)}.",
            impacto="El NAP inconsistente confunde a Google y reduce el ranking en búsquedas locales.",
            dificultad="medium",
            solucion_diy="Actualizar los datos en cada directorio para que coincidan exactamente con Google Maps.",
            solucion_innovando="Auditamos y corregimos el NAP en todos los directorios por vos.",
            precio_diy=3,
            precio_innovando=49000,
        ))

    return {
        "score":          max(0, score),
        "problemas":      problemas_detectados,
        "encontrados":    encontrados,
        "no_encontrados": no_encontrados,
        "inconsistentes": inconsistentes,
    }


# ──────────────────────────────────────────────
# H3 · PLATAFORMAS DE VIAJE
# ──────────────────────────────────────────────

async def analizar_plataformas(page, business: dict, sb, verbose: bool) -> dict:
    """
    Verifica presencia y calidad en plataformas de viaje.
    Usa datos de business_data (módulo platform) si scorer_plataformas ya corrió.
    Fallback: búsqueda Google básica para detectar ausencia.
    """
    problemas_detectados = []
    score = 20
    name  = business.get("name", "")
    city  = business.get("city", "")
    bid   = business.get("id")

    # ── Leer datos de scorer_plataformas si ya corrió ────────
    plataformas_encontradas = []
    plataformas_faltantes   = []

    if bid:
        dm = DataManager(sb, bid)
        platforms_count = dm.get_int("platform", "platforms_count", default=-1)

        if platforms_count >= 0:
            # Datos reales disponibles — usar directamente
            for plat_id, nombre_plat in [
                ("booking",     "Booking.com"),
                ("airbnb",      "Airbnb"),
                ("tripadvisor", "TripAdvisor"),
                ("expedia",     "Expedia"),
                ("despegar",    "Despegar"),
            ]:
                url = dm.get_text("platform", f"{plat_id}_url")
                if url:
                    plataformas_encontradas.append(nombre_plat)
                else:
                    plataformas_faltantes.append(nombre_plat)

            if verbose:
                print(f"   📊 Plataformas (datos reales): {platforms_count}/5 encontradas")

            # Score basado en datos reales
            pts_perdidos = len(plataformas_faltantes) * 2
            score = max(0, score - pts_perdidos)

            if plataformas_faltantes:
                problemas_detectados.append(problema(
                    modulo="h3_plataformas",
                    tipo="missing_travel_platforms",
                    descripcion=f"Sin presencia en: {', '.join(plataformas_faltantes[:3])}.",
                    impacto="Booking y TripAdvisor concentran el 65% de las búsquedas de alojamiento.",
                    dificultad="medium",
                    solucion_diy="Crear perfil gratuito en cada plataforma (30-60 min por plataforma).",
                    solucion_innovando="Setup completo en todas las plataformas + optimización de perfil.",
                    precio_diy=3,
                    precio_innovando=90000,
                ))

            return {"score": score, "problemas": problemas_detectados,
                    "encontradas": plataformas_encontradas, "faltantes": plataformas_faltantes}

    # ── Fallback: búsqueda Google básica (scorer_plataformas no corrió) ──
    sin_presencia = []
    for plataforma in ["booking.com", "tripadvisor.com"]:
        try:
            await page.goto(
                f"https://www.google.com/search?q=site:{plataforma}+{name.replace(' ', '+')}+{city.replace(' ', '+')}",
                timeout=TIMEOUT_MS,
                wait_until="domcontentloaded"
            )
            await page.wait_for_timeout(1500)
            links = await page.eval_on_selector_all(
                f'a[href*="{plataforma}"]',
                'elements => elements.map(e => e.href)'
            )
            if not links:
                sin_presencia.append(plataforma)
        except Exception:
            pass

    if sin_presencia:
        score -= 10
        problemas_detectados.append(problema(
            modulo="h3_plataformas",
            tipo="missing_travel_platforms",
            descripcion=f"Sin presencia verificada en: {', '.join(sin_presencia)}.",
            impacto="Booking y TripAdvisor concentran el 65% de las búsquedas de alojamiento.",
            dificultad="medium",
            solucion_diy="Crear perfil gratuito en cada plataforma (30-60 min por plataforma).",
            solucion_innovando="Setup completo en todas las plataformas + optimización de perfil.",
            precio_diy=3,
            precio_innovando=90000,
        ))

    return {"score": max(0, score), "problemas": problemas_detectados}


# ──────────────────────────────────────────────
# H4 · REDES SOCIALES — perfiles huérfanos
# ──────────────────────────────────────────────

async def analizar_redes_huella(page, business: dict, sb, verbose: bool) -> dict:
    """Detecta perfiles huérfanos, inactivos o con nombre incorrecto."""
    problemas_detectados = []
    score   = 15
    name    = business.get("name", "")
    socials = get_socials(sb, business["id"])

    if not socials:
        score -= 10
        problemas_detectados.append(problema(
            modulo="h4_redes",
            tipo="no_social_presence",
            descripcion=f"{name} no tiene presencia detectada en redes sociales.",
            impacto="El 78% de los viajeros menores de 40 años busca alojamiento en redes antes de reservar.",
            dificultad="medium",
            solucion_diy="Crear perfil en Instagram con el nombre del negocio y publicar 3 fotos iniciales.",
            solucion_innovando="Creamos y configuramos tus perfiles de redes sociales.",
            precio_diy=0,
            precio_innovando=79000,
        ))
        return {"score": max(0, score), "problemas": problemas_detectados}

    # Verificar actividad reciente en cada red
    for social in socials:
        network = social.get("network")
        url     = social.get("url")

        if network not in ("instagram", "facebook"):
            continue

        try:
            await page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
            texto = await page.inner_text("body")

            # Detectar si la página existe
            if any(x in texto.lower() for x in ["page not found", "lo sentimos", "this account", "no existe"]):
                score -= 5
                problemas_detectados.append(problema(
                    modulo="h4_redes",
                    tipo="dead_social_profile",
                    descripcion=f"El perfil de {network} ({url}) ya no existe o fue eliminado.",
                    impacto="Links rotos dañan la credibilidad del negocio.",
                    dificultad="easy",
                    solucion_diy="Crear un nuevo perfil con el nombre correcto del negocio.",
                    solucion_innovando="Creamos y configuramos el nuevo perfil.",
                    precio_diy=0,
                    precio_innovando=49000,
                    url_encontrada=url,
                ))
                continue

            # Detectar inactividad — buscar fechas de publicación
            meses_inactivos = _detectar_inactividad(texto)
            if meses_inactivos and meses_inactivos >= 6:
                score -= 3
                problemas_detectados.append(problema(
                    modulo="h4_redes",
                    tipo="inactive_social_profile",
                    descripcion=f"El perfil de {network} lleva ~{meses_inactivos} meses sin publicaciones.",
                    impacto="Los perfiles inactivos reducen el alcance orgánico y la confianza del cliente.",
                    dificultad="easy",
                    solucion_diy="Publicar al menos 2 veces por semana con fotos del negocio y la zona.",
                    solucion_innovando="Gestionamos tus redes sociales con publicaciones semanales.",
                    precio_diy=0,
                    precio_innovando=79000,
                    url_encontrada=url,
                ))

        except Exception as e:
            if verbose: print(f"      ⚠️  Error analizando {network}: {e}")

    return {"score": max(0, score), "problemas": problemas_detectados}


def _detectar_inactividad(texto: str) -> int | None:
    """Estima meses de inactividad desde el texto de la página."""
    patrones = [
        r'hace (\d+) a[ñn]os?',
        r'(\d+) years? ago',
        r'hace (\d+) meses?',
        r'(\d+) months? ago',
    ]
    for patron in patrones:
        match = re.search(patron, texto, re.IGNORECASE)
        if match:
            n = int(match.group(1))
            if 'año' in patron or 'year' in patron:
                return n * 12
            return n
    return None


# ──────────────────────────────────────────────
# H5 · MEDIOS Y BLOGS — menciones negativas
# ──────────────────────────────────────────────

async def analizar_medios(page, business: dict, sc: SearchClient, verbose: bool) -> dict:
    """Busca menciones negativas en medios y blogs."""
    problemas_detectados = []
    score  = 10
    name   = business.get("name", "")
    city   = business.get("city", "")

    menciones_negativas = []
    menciones_positivas = []

    # Palabras negativas en contexto de turismo
    palabras_negativas = [
        "estafa", "fraude", "denuncia", "terrible", "pésimo",
        "horrible", "scam", "fraud", "awful", "worst", "disgusting",
        "robo", "engaño", "evitar", "avoid", "warning"
    ]

    # Brave es ideal para esto: búsqueda abierta sin operadores site:
    queries = [
        f'"{name}" {city} opiniones',
        f'"{name}" {city}',
    ]
    exclude = ["google.com", "booking.com", "tripadvisor.com", "airbnb.com"]

    for query in queries[:2]:
        try:
            resultados = sc.buscar_menciones(query, num=10, exclude_domains=exclude)
            for r in resultados:
                snippet = (r.get("snippet") or "").lower()
                title   = (r.get("title") or "").lower()
                texto   = snippet + " " + title
                link    = r.get("url", "")
                if any(w in texto for w in palabras_negativas):
                    menciones_negativas.append({"url": link, "title": r.get("title", "")})
                else:
                    menciones_positivas.append(link)
        except Exception as e:
            if verbose: print(f"      ⚠️  Error buscando menciones: {e}")

    if menciones_negativas:
        score -= 10
        urls = [m["url"] for m in menciones_negativas[:3]]
        problemas_detectados.append(problema(
            modulo="h5_medios",
            tipo="negative_media_mentions",
            descripcion=f"Se detectaron {len(menciones_negativas)} mención(es) negativa(s) en medios o blogs.",
            impacto="Las menciones negativas aparecen cuando alguien busca el negocio en Google.",
            dificultad="hard",
            solucion_diy="Contactar al autor del contenido para solicitar corrección o eliminación.",
            solucion_innovando="Gestionamos solicitudes de eliminación y estrategia de contenido positivo para desplazar menciones negativas.",
            precio_diy=3,
            precio_innovando=99000,
            url_encontrada=urls[0] if urls else None,
        ))

    return {
        "score":                max(0, score),
        "problemas":            problemas_detectados,
        "menciones_negativas":  menciones_negativas,
        "menciones_positivas":  len(menciones_positivas),
    }


# ──────────────────────────────────────────────
# H6 · NAP CONSISTENCY
# ──────────────────────────────────────────────

async def analizar_nap(page, business: dict, sb, verbose: bool) -> dict:
    """Verifica consistencia de Name/Address/Phone en todos los sitios."""
    problemas_detectados = []
    score = 5

    name    = business.get("name", "")
    address = business.get("address", "")
    phones  = get_phones(sb, business["id"])
    primary_phone = phones[0]["phone"] if phones else ""

    # Recopilar todos los contactos encontrados
    all_phones = [p["phone"] for p in phones]
    emails_db  = get_emails(sb, business["id"])
    all_emails = [e["email"] for e in emails_db]

    # Detectar variaciones en el nombre del negocio (abreviaciones comunes)
    name_variations = set()
    name_words = name.lower().split()
    if len(name_words) > 2:
        name_variations.add(" ".join(name_words[:2]))  # primeras 2 palabras
        name_variations.add(name_words[0])             # solo primera palabra

    inconsistencias = []

    if len(all_phones) > 1:
        score -= 2
        inconsistencias.append(f"{len(all_phones)} teléfonos distintos encontrados")

    if len(all_emails) > 3:
        score -= 1
        inconsistencias.append(f"{len(all_emails)} emails distintos encontrados")

    if inconsistencias:
        problemas_detectados.append(problema(
            modulo="h6_nap",
            tipo="nap_inconsistency",
            descripcion=f"Datos de contacto inconsistentes: {'; '.join(inconsistencias)}.",
            impacto="El NAP inconsistente reduce el ranking SEO local y confunde a los clientes.",
            dificultad="medium",
            solucion_diy="Unificar todos los datos de contacto: usar siempre el mismo nombre, teléfono y dirección en todos los sitios.",
            solucion_innovando="Auditamos y corregimos el NAP en todos los sitios donde aparece el negocio.",
            precio_diy=3,
            precio_innovando=49000,
        ))

    return {"score": max(0, score), "problemas": problemas_detectados}


# ──────────────────────────────────────────────
# SCORE TOTAL Y NOTA
# ──────────────────────────────────────────────

def calcular_score_huella(resultados: dict) -> int:
    """Score total de huella digital 0-100."""
    return (
        resultados.get("h1", {}).get("score", 0) +  # 30 pts
        resultados.get("h2", {}).get("score", 0) +  # 20 pts
        resultados.get("h3", {}).get("score", 0) +  # 20 pts
        resultados.get("h4", {}).get("score", 0) +  # 15 pts
        resultados.get("h5", {}).get("score", 0) +  # 10 pts
        resultados.get("h6", {}).get("score", 0)    # 5 pts
    )


def generar_nota_huella(name: str, score: int, resultados: dict) -> str:
    """Genera nota personalizada del informe de huella digital."""
    todos_problemas = []
    for mod in resultados.values():
        todos_problemas.extend(mod.get("problemas", []))

    total_problemas = len(todos_problemas)
    hard_count      = sum(1 for p in todos_problemas if p["difficulty"] == "hard")
    med_count       = sum(1 for p in todos_problemas if p["difficulty"] == "medium")

    if score >= 70:
        return (
            f"{name} tiene una huella digital sólida ({score}/100). "
            "Hay algunas optimizaciones menores para maximizar la visibilidad online."
        )
    if score >= 40:
        return (
            f"{name} tiene una huella digital parcial ({score}/100) con "
            f"{total_problemas} problema(s) detectado(s). "
            f"{med_count} requieren trabajo manual y {hard_count} requieren gestión profesional."
        )
    return (
        f"{name} tiene una huella digital deficiente ({score}/100). "
        f"Se detectaron {total_problemas} problema(s) que afectan directamente "
        "la visibilidad y reputación online del negocio."
    )


def generar_plan_accion(resultados: dict) -> list[dict]:
    """
    Genera plan de acción priorizado con 3 niveles:
    - Inmediato (easy, gratis) → hacer hoy
    - Corto plazo (medium, $3) → esta semana
    - Profesional (hard) → contratar Innovando
    """
    todos_problemas = []
    for mod in resultados.values():
        todos_problemas.extend(mod.get("problemas", []))

    inmediatos    = [p for p in todos_problemas if p["difficulty"] == "easy"]
    corto_plazo   = [p for p in todos_problemas if p["difficulty"] == "medium"]
    profesionales = [p for p in todos_problemas if p["difficulty"] == "hard"]

    return {
        "inmediato":    inmediatos,
        "corto_plazo":  corto_plazo,
        "profesional":  profesionales,
        "total_diy_cost": sum(p.get("precio_diy", 0) for p in inmediatos + corto_plazo),
        "total_innovando_cost": sum(
            p.get("precio_innovando", 0) or 0
            for p in todos_problemas
            if p.get("precio_innovando")
        ),
    }


# ──────────────────────────────────────────────
# PIPELINE PRINCIPAL
# ──────────────────────────────────────────────

async def run(env: str, max_leads: int | None, verbose: bool, slug: str | None, forzar: bool):
    load_dotenv(f".env.{env}")

    api_key   = os.getenv("GOOGLE_PLACES_API_KEY")
    cse_key   = os.getenv("GOOGLE_CSE_KEY")
    cse_id    = os.getenv("GOOGLE_CSE_ID")
    brave_key = os.getenv("BRAVE_SEARCH_KEY")

    sb      = get_client(env=env)
    tracker = APITracker(env=env, sb=sb, script="scorer_huella")
    sc      = SearchClient(cse_key=cse_key, cse_id=cse_id,
                           brave_key=brave_key, tracker=tracker)

    print(f"\n{sc.estado()}")

    # Obtener leads a analizar
    if slug:
        business = get_business_by_slug(sb, slug)
        leads = [business] if business else []
    else:
        result = sb.table("businesses").select("*").execute()
        leads  = result.data or []
        if not forzar:
            leads = [l for l in leads if not l.get("lh_score")]

    if max_leads:
        leads = leads[:max_leads]

    total = len(leads)
    print(f"\n🔍 scorer_huella | {total} negocios | ambiente '{env}'")
    print(f"   Módulos: Maps · Directorios · Plataformas · Redes · Medios · NAP\n")

    analizados = 0
    errores    = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            user_agent=USER_AGENT,
            locale="es-CL",
            extra_http_headers={"Accept-Language": "es-CL,es;q=0.9"},
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = await context.new_page()

        for i, business in enumerate(leads, 1):
            name = business.get("name", f"Lead {i}")
            print(f"\n[{i}/{total}] {name}")

            try:
                resultados = {}

                # H1 — Google Maps
                print(f"   🗺️  H1 · Google Maps...")
                resultados["h1"] = await analizar_google_maps(page, business, api_key, verbose)

                # H2 — Directorios
                print(f"   📋 H2 · Directorios...")
                resultados["h2"] = await analizar_directorios(page, business, sc, verbose)

                # H3 — Plataformas de viaje
                print(f"   ✈️  H3 · Plataformas...")
                resultados["h3"] = await analizar_plataformas(page, business, sb, verbose)

                # H4 — Redes sociales
                print(f"   📱 H4 · Redes sociales...")
                resultados["h4"] = await analizar_redes_huella(page, business, sb, verbose)

                # H5 — Medios y blogs
                print(f"   📰 H5 · Medios y blogs...")
                resultados["h5"] = await analizar_medios(page, business, sc, verbose)

                # H6 — NAP consistency
                print(f"   📌 H6 · NAP consistency...")
                resultados["h6"] = await analizar_nap(page, business, sb, verbose)

                # Score y nota
                score_huella = calcular_score_huella(resultados)
                nota_huella  = generar_nota_huella(name, score_huella, resultados)
                plan_accion  = generar_plan_accion(resultados)

                # Consolidar todos los problemas
                todos_problemas = []
                for mod in resultados.values():
                    todos_problemas.extend(mod.get("problemas", []))

                # Display
                print(f"   📊 Score huella: {score_huella}/100")
                print(f"   🔴 Problemas detectados: {len(todos_problemas)}")
                print(f"      Easy (DIY gratis):    {len(plan_accion['inmediato'])}")
                print(f"      Medium (DIY $3):      {len(plan_accion['corto_plazo'])}")
                print(f"      Hard (Innovando):     {len(plan_accion['profesional'])}")

                # Guardar en business_data
                dm = DataManager(sb, business["id"])
                dm.set_many("huella", {
                    "huella_score":         score_huella,
                    "h1_score":             resultados["h1"]["score"],
                    "h2_score":             resultados["h2"]["score"],
                    "h3_score":             resultados["h3"]["score"],
                    "h4_score":             resultados["h4"]["score"],
                    "h5_score":             resultados["h5"]["score"],
                    "h6_score":             resultados["h6"]["score"],
                    "problems_count":       len(todos_problemas),
                    "problems_easy":        len(plan_accion["inmediato"]),
                    "problems_medium":      len(plan_accion["corto_plazo"]),
                    "problems_hard":        len(plan_accion["profesional"]),
                    "negative_mentions":    len(resultados["h5"].get("menciones_negativas", [])),
                    "nap_consistent":       len(resultados["h6"]["problemas"]) == 0,
                    "duplicate_listings":   any(
                        p["tipo"] == "duplicate_listing"
                        for p in resultados["h1"]["problemas"]
                    ),
                }, source="scorer_huella", step="scorer_huella")

                # Guardar en tabla reports (módulo huella)
                latest = sb.table("reports")\
                    .select("id")\
                    .eq("business_id", business["id"])\
                    .order("generated_at", desc=True)\
                    .limit(1)\
                    .execute()

                if latest.data:
                    sb.table("reports").update({
                        "modulo_huella": {
                            "score":       score_huella,
                            "nota":        nota_huella,
                            "nota_edited": False,
                            "datos": {
                                "problemas":      todos_problemas,
                                "plan_accion":    plan_accion,
                                "scores_modulos": {
                                    "h1_google_maps":  resultados["h1"]["score"],
                                    "h2_directorios":  resultados["h2"]["score"],
                                    "h3_plataformas":  resultados["h3"]["score"],
                                    "h4_redes":        resultados["h4"]["score"],
                                    "h5_medios":       resultados["h5"]["score"],
                                    "h6_nap":          resultados["h6"]["score"],
                                },
                            }
                        }
                    }).eq("id", latest.data[0]["id"]).execute()

                analizados += 1

            except Exception as e:
                print(f"   ❌ Error: {e}")
                errores += 1

            await asyncio.sleep(DELAY_ENTRE_LEADS)

        await browser.close()

    # Resumen final
    print(f"\n{'='*55}")
    print(f"✅ scorer_huella completado")
    print(f"   Analizados: {analizados} | Errores: {errores}")
    print(f"{'='*55}")

    # Top oportunidades de venta
    result = sb.table("businesses")\
        .select("name, lh_score, lh_diagnosis")\
        .not_.is_("lh_score", "null")\
        .order("lh_score", desc=False)\
        .limit(5)\
        .execute()

    if result.data:
        print(f"\n💰 Top oportunidades de limpieza de huella:")
        for b in result.data:
            print(f"   [{b['lh_score']}/100] {b['name']}")

    print(f"\n➡️  Siguiente: python report_builder.py --env {env} --all")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="innovando-scripts · Scorer Huella Digital")
    parser.add_argument("--env",     required=True, choices=["test", "prd"])
    parser.add_argument("--slug",    default=None)
    parser.add_argument("--max",     type=int, default=None)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--forzar",  action="store_true")
    args = parser.parse_args()

    asyncio.run(run(args.env, args.max, args.verbose, args.slug, args.forzar))