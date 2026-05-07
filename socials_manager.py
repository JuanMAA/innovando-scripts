"""
socials_manager.py — innovando-scripts
Módulo centralizado para gestionar redes sociales en tabla business_socials.
Todos los scripts lo importan para leer y escribir redes.

Uso:
    from socials_manager import upsert_social, get_socials, get_social_url, NETWORKS

    # Guardar una red
    upsert_social(sb, business_id="uuid", network="instagram",
                  url="https://instagram.com/hotelpepito",
                  source="web")

    # Leer todas las redes de un negocio
    socials = get_socials(sb, business_id="uuid")

    # Obtener URL de una red específica
    ig_url = get_social_url(socials, "instagram")
"""

import re


# ──────────────────────────────────────────────
# REDES SOPORTADAS
# ──────────────────────────────────────────────

NETWORKS = {
    "instagram":   {"dominio": "instagram.com",    "emoji": "📸"},
    "facebook":    {"dominio": "facebook.com",      "emoji": "📘"},
    "tiktok":      {"dominio": "tiktok.com",        "emoji": "🎵"},
    "youtube":     {"dominio": "youtube.com",       "emoji": "▶️"},
    "x":           {"dominio": "x.com",             "emoji": "🐦"},
    "twitter":     {"dominio": "twitter.com",       "emoji": "🐦"},
    "tripadvisor": {"dominio": "tripadvisor.com",   "emoji": "🦉"},
    "whatsapp":    {"dominio": "wa.me",             "emoji": "💬"},
    "linktree":    {"dominio": "linktr.ee",         "emoji": "🌳"},
    "bio_site":    {"dominio": "bio.site",          "emoji": "🔗"},
    "beacons":     {"dominio": "beacons.ai",        "emoji": "🔗"},
    "taplink":     {"dominio": "taplink.cc",        "emoji": "🔗"},
    "campsite":    {"dominio": "campsite.bio",      "emoji": "🔗"},
    "lnkbio":      {"dominio": "lnk.bio",           "emoji": "🔗"},
    "soloto":      {"dominio": "solo.to",           "emoji": "🔗"},
}

# Redes que NO son sitios web reales
NOT_REAL_WEBSITE = {
    "instagram", "facebook", "tiktok", "youtube", "x", "twitter",
    "whatsapp", "linktree", "bio_site", "beacons", "taplink",
    "campsite", "lnkbio", "soloto",
}

# Dominios de agregadores de links
LINK_AGGREGATORS = {
    "linktr.ee", "linktree.com", "bio.site", "beacons.ai",
    "taplink.cc", "campsite.bio", "lnk.bio", "solo.to",
}


# ──────────────────────────────────────────────
# DETECCIÓN AUTOMÁTICA
# ──────────────────────────────────────────────

def detectar_network(url: str) -> str | None:
    """
    Detecta a qué red social pertenece una URL.
    Retorna el name de la red o None si es un sitio web normal.
    """
    if not url:
        return None

    u = url.lower().strip()

    # Orden importa — más específico primero
    detecciones = [
        ("instagram.com",    "instagram"),
        ("facebook.com",     "facebook"),
        ("fb.com",           "facebook"),
        ("tiktok.com",       "tiktok"),
        ("youtube.com",      "youtube"),
        ("youtu.be",         "youtube"),
        ("x.com",            "x"),
        ("twitter.com",      "x"),
        ("tripadvisor.com",  "tripadvisor"),
        ("wa.me",            "whatsapp"),
        ("api.whatsapp.com", "whatsapp"),
        ("linktr.ee",        "linktree"),
        ("linktree.com",     "linktree"),
        ("bio.site",         "bio_site"),
        ("beacons.ai",       "beacons"),
        ("taplink.cc",       "taplink"),
        ("campsite.bio",     "campsite"),
        ("lnk.bio",          "lnkbio"),
        ("solo.to",          "soloto"),
    ]

    for dominio, network in detecciones:
        if dominio in u:
            return network

    return None  # es un sitio web real


def extraer_username(url: str, network: str) -> str | None:
    """Extrae el name de usuario de una URL de red social."""
    patrones = {
        "instagram":   r'instagram\.com/([A-Za-z0-9_.]+)',
        "facebook":    r'facebook\.com/([A-Za-z0-9_./-]+)',
        "tiktok":      r'tiktok\.com/@?([A-Za-z0-9_.]+)',
        "youtube":     r'youtube\.com/(?:@|c/|channel/|user/)?([A-Za-z0-9_.-]+)',
        "x":           r'(?:x|twitter)\.com/([A-Za-z0-9_]+)',
        "tripadvisor": r'tripadvisor\.com/[^/]+/([^/?]+)',
        "linktree":    r'linktr\.ee/([A-Za-z0-9_.]+)',
    }
    patron = patrones.get(network)
    if not patron:
        return None
    match = re.search(patron, url, re.IGNORECASE)
    return match.group(1) if match else None


def extraer_links_de_html(html: str, fuente: str = "web") -> list[dict]:
    """
    Extrae todos los links de redes sociales de un HTML.
    Retorna lista de dicts con network, url, username, source.
    """
    encontrados = []
    vistos = set()

    # Buscar todos los href del HTML
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html, re.IGNORECASE)
    # También buscar URLs en texto plano
    hrefs += re.findall(r'https?://[^\s"\'<>]+', html)

    for href in hrefs:
        href = href.strip()
        if not href.startswith("http"):
            continue

        network = detectar_network(href)
        if not network:
            continue

        # Normalizar URL
        url = href.split("?")[0].rstrip("/")
        if url in vistos:
            continue
        vistos.add(url)

        username = extraer_username(url, network)

        # Filtrar usernames genéricos/inválidos
        if username and network == "instagram":
            blacklist = {"p", "reel", "reels", "stories", "explore", "tv",
                         "accounts", "about", "legal", "privacy", "help"}
            if username.lower() in blacklist:
                continue

        if username and network == "facebook":
            blacklist = {"sharer", "share", "dialog", "plugins", "tr",
                         "photo", "video", "groups", "events", "hashtag",
                         "pages", "watch", "marketplace", "login"}
            if username.lower().split("/")[0] in blacklist:
                continue

        encontrados.append({
            "network":  network,
            "url":      url,
            "username": username,
            "source":   fuente,
        })

    return encontrados


# ──────────────────────────────────────────────
# OPERACIONES SUPABASE
# ──────────────────────────────────────────────

def upsert_social(
    sb,
    business_id: str,
    network: str,
    url: str,
    username: str | None = None,
    source: str = "sistema",
    verified: bool = False,
) -> dict:
    """
    Crea o actualiza una red social de un negocio.
    Si ya existe el network para ese business_id, actualiza la URL.
    """
    if not username:
        username = extraer_username(url, network)

    data = {
        "business_id": business_id,
        "network":     network,
        "url":         url,
        "username":    username,
        "source":      source,
        "verified":    verified,
    }

    result = sb.table("business_socials").upsert(
        data,
        on_conflict="business_id,network"
    ).execute()

    return result.data[0] if result.data else {}


def upsert_socials_bulk(
    sb,
    business_id: str,
    socials: list[dict],
) -> int:
    """
    Guarda múltiples redes de una vez.
    Retorna cantidad guardada.
    """
    guardadas = 0
    for s in socials:
        try:
            upsert_social(
                sb,
                business_id=business_id,
                network=s["network"],
                url=s["url"],
                username=s.get("username"),
                source=s.get("source", "sistema"),
            )
            guardadas += 1
        except Exception:
            pass
    return guardadas


def get_socials(sb, business_id: str) -> list[dict]:
    """Retorna todas las redes sociales de un negocio."""
    result = (
        sb.table("business_socials")
        .select("*")
        .eq("business_id", business_id)
        .execute()
    )
    return result.data or []


def get_social_url(socials: list[dict], network: str) -> str | None:
    """Obtiene la URL de una red específica de la lista."""
    for s in socials:
        if s.get("network") == network:
            return s.get("url")
    return None


def get_social_username(socials: list[dict], network: str) -> str | None:
    """Obtiene el username de una red específica."""
    for s in socials:
        if s.get("network") == network:
            return s.get("username")
    return None


def tiene_red(socials: list[dict], network: str) -> bool:
    """Retorna True si el negocio tiene esa red social."""
    return any(s.get("network") == network for s in socials)


def score_redes(socials: list[dict]) -> int:
    """
    Calcula el score P2d basado en las redes detectadas.
    Score máximo: 10 pts
    """
    score = 0
    redes_activas = {s["network"] for s in socials}

    if "instagram" in redes_activas:  score += 5
    if "facebook"  in redes_activas:  score += 3
    if "tiktok"    in redes_activas:  score += 2
    if "youtube"   in redes_activas:  score += 1
    if "x"         in redes_activas:  score += 1

    return min(score, 10)  # máximo 10 pts


def resumen_redes(socials: list[dict]) -> str:
    """Genera texto resumen de redes para mostrar."""
    if not socials:
        return "Sin redes sociales detectadas"

    partes = []
    for s in socials:
        config = NETWORKS.get(s["network"], {"emoji": "🔗"})
        emoji = config["emoji"]
        username = s.get("username") or s.get("url", "")
        partes.append(f"{emoji} {s['network']}: {username}")

    return " | ".join(partes)