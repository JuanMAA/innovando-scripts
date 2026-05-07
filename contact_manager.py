"""
contact_manager.py — innovando-scripts
Módulo centralizado para gestionar emails y teléfonos en tablas dedicadas.
Soporta múltiples contactos por negocio con trazabilidad completa.

Uso:
    from contact_manager import (
        save_email, save_phone,
        get_emails, get_phones,
        get_primary_email, get_primary_phone,
        set_primary_email, set_primary_phone,
    )

    # Guardar un email encontrado
    save_email(sb, business_id="uuid",
               email="contacto@hostal.cl",
               source="web",
               found_at_step="scorer_web")

    # Guardar un WhatsApp
    save_phone(sb, business_id="uuid",
               phone="+56912345678",
               type="whatsapp",
               source="instagram",
               found_at_step="scorer_redes")

    # Leer todos los emails
    emails = get_emails(sb, business_id="uuid")

    # Obtener el principal
    email = get_primary_email(sb, business_id="uuid")
"""

import re


# ──────────────────────────────────────────────
# VALIDACIONES
# ──────────────────────────────────────────────

EMAIL_BLACKLIST_DOMAINS = {
    "example.com", "ejemplo.com", "test.com", "domain.com",
    "instagram.com", "facebook.com", "google.com", "apple.com",
    "sentry.io", "wixpress.com", "squarespace.com", "wordpress.com",
    "support.google.com", "yoursite.com", "tuempresa.com",
}

EMAIL_REGEX = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE
)

PHONE_REGEX = re.compile(r'^\+?[\d\s\-\(\)]{7,20}$')


def validar_email(email: str) -> str | None:
    """Valida y normaliza un email. Retorna None si no es válido."""
    if not email:
        return None
    email = email.lower().strip().rstrip(".")
    if not EMAIL_REGEX.match(email):
        return None
    if len(email) > 100 or len(email) < 6:
        return None
    dominio = email.split("@")[-1]
    if dominio in EMAIL_BLACKLIST_DOMAINS:
        return None
    if any(email.endswith(ext) for ext in [".png", ".jpg", ".gif", ".svg"]):
        return None
    return email


def validar_phone(phone: str) -> str | None:
    """Valida y normaliza un teléfono. Retorna None si no es válido."""
    if not phone:
        return None
    # Limpiar espacios y caracteres comunes
    clean = re.sub(r'[\s\-\(\)]', '', phone.strip())
    if not clean.startswith('+'):
        clean = '+' + clean
    if len(clean) < 8 or len(clean) > 16:
        return None
    if not re.match(r'^\+[\d]+$', clean):
        return None
    return clean


def detectar_tipo_phone(phone: str, source: str = "") -> str:
    """Detecta el tipo de teléfono según la fuente."""
    if source in ("instagram", "facebook", "linktree"):
        return "whatsapp"
    if "whatsapp" in source.lower():
        return "whatsapp"
    return "phone"


def extraer_emails_de_texto(texto: str) -> list[str]:
    """Extrae todos los emails válidos de un texto."""
    encontrados = EMAIL_REGEX.findall(texto)
    return [e for e in (validar_email(e) for e in encontrados) if e]


def extraer_whatsapp_de_html(html: str) -> list[str]:
    """Extrae todos los números de WhatsApp de un HTML."""
    numeros = []
    patrones = [
        r'wa\.me/(\+?[\d]{7,15})',
        r'api\.whatsapp\.com/send[?&]phone=(\+?[\d]{7,15})',
        r'whatsapp://send[?&]phone=(\+?[\d]{7,15})',
    ]
    for patron in patrones:
        for match in re.finditer(patron, html, re.IGNORECASE):
            num = validar_phone(match.group(1))
            if num and num not in numeros:
                numeros.append(num)
    return numeros


# ──────────────────────────────────────────────
# OPERACIONES SUPABASE — EMAILS
# ──────────────────────────────────────────────

def save_email(
    sb,
    business_id: str,
    email: str,
    source: str = "web",
    found_at_step: str = "scorer_web",
    is_primary: bool = False,
    verified: bool = False,
) -> dict | None:
    """
    Guarda un email en business_emails.
    Si ya existe (mismo business_id + email), no duplica.
    Si is_primary=True, desmarca los demás como primarios primero.
    """
    email = validar_email(email)
    if not email:
        return None

    # Si es primario, desmarcar los demás
    if is_primary:
        sb.table("business_emails").update({"is_primary": False})\
          .eq("business_id", business_id).execute()

    result = sb.table("business_emails").upsert({
        "business_id":   business_id,
        "email":         email,
        "source":        source,
        "found_at_step": found_at_step,
        "is_primary":    is_primary,
        "verified":      verified,
    }, on_conflict="business_id,email").execute()

    return result.data[0] if result.data else None


def save_emails_bulk(
    sb,
    business_id: str,
    emails: list[str],
    source: str,
    found_at_step: str,
) -> int:
    """
    Guarda múltiples emails de una vez.
    El primero de la lista se marca como primario si no hay ninguno aún.
    Retorna cantidad guardada.
    """
    # Verificar si ya tiene un email primario
    existing = get_emails(sb, business_id)
    tiene_primario = any(e.get("is_primary") for e in existing)

    guardados = 0
    for i, email in enumerate(emails):
        email_limpio = validar_email(email)
        if not email_limpio:
            continue

        # El primero es primario si no hay ninguno aún
        is_primary = (i == 0 and not tiene_primario)

        result = save_email(
            sb, business_id, email_limpio,
            source=source,
            found_at_step=found_at_step,
            is_primary=is_primary,
        )
        if result:
            guardados += 1

    return guardados


def get_emails(sb, business_id: str) -> list[dict]:
    """Retorna todos los emails de un negocio, primarios primero."""
    result = (
        sb.table("business_emails")
        .select("*")
        .eq("business_id", business_id)
        .order("is_primary", desc=True)
        .order("found_at", desc=False)
        .execute()
    )
    return result.data or []


def get_primary_email(sb, business_id: str) -> str | None:
    """Retorna el email principal del negocio."""
    emails = get_emails(sb, business_id)
    # Primero buscar el marcado como primario
    for e in emails:
        if e.get("is_primary"):
            return e["email"]
    # Si no hay primario, retornar el primero
    return emails[0]["email"] if emails else None


def set_primary_email(sb, business_id: str, email: str) -> bool:
    """Marca un email como primario y desmarca los demás."""
    # Desmarcar todos
    sb.table("business_emails").update({"is_primary": False})\
      .eq("business_id", business_id).execute()
    # Marcar el nuevo primario
    result = sb.table("business_emails").update({"is_primary": True})\
               .eq("business_id", business_id)\
               .eq("email", email).execute()
    return bool(result.data)


# ──────────────────────────────────────────────
# OPERACIONES SUPABASE — TELÉFONOS
# ──────────────────────────────────────────────

def save_phone(
    sb,
    business_id: str,
    phone: str,
    type: str = "phone",
    source: str = "google_maps",
    found_at_step: str = "maps_scraper",
    is_primary: bool = False,
    verified: bool = False,
) -> dict | None:
    """
    Guarda un teléfono en business_phones.
    Si ya existe no duplica.
    """
    phone = validar_phone(phone)
    if not phone:
        return None

    if is_primary:
        sb.table("business_phones").update({"is_primary": False})\
          .eq("business_id", business_id)\
          .eq("type", type).execute()

    result = sb.table("business_phones").upsert({
        "business_id":   business_id,
        "phone":         phone,
        "type":          type,
        "source":        source,
        "found_at_step": found_at_step,
        "is_primary":    is_primary,
        "verified":      verified,
    }, on_conflict="business_id,phone").execute()

    return result.data[0] if result.data else None


def save_phones_bulk(
    sb,
    business_id: str,
    phones: list[dict],
    found_at_step: str,
) -> int:
    """
    Guarda múltiples teléfonos.
    Cada item: {"phone": ..., "type": ..., "source": ...}
    """
    existing = get_phones(sb, business_id)
    tiene_primario = any(p.get("is_primary") for p in existing)

    guardados = 0
    for i, item in enumerate(phones):
        phone_limpio = validar_phone(item.get("phone", ""))
        if not phone_limpio:
            continue

        is_primary = (i == 0 and not tiene_primario)
        result = save_phone(
            sb,
            business_id=business_id,
            phone=phone_limpio,
            type=item.get("type", "phone"),
            source=item.get("source", "sistema"),
            found_at_step=found_at_step,
            is_primary=is_primary,
        )
        if result:
            guardados += 1

    return guardados


def get_phones(sb, business_id: str) -> list[dict]:
    """Retorna todos los teléfonos de un negocio, primarios primero."""
    result = (
        sb.table("business_phones")
        .select("*")
        .eq("business_id", business_id)
        .order("is_primary", desc=True)
        .order("found_at", desc=False)
        .execute()
    )
    return result.data or []


def get_primary_phone(sb, business_id: str, type: str = "phone") -> str | None:
    """Retorna el teléfono principal de un tipo específico."""
    phones = get_phones(sb, business_id)
    # Buscar primario del tipo solicitado
    for p in phones:
        if p.get("is_primary") and p.get("type") == type:
            return p["phone"]
    # Fallback: cualquier teléfono del tipo
    for p in phones:
        if p.get("type") == type:
            return p["phone"]
    return None


def get_primary_whatsapp(sb, business_id: str) -> str | None:
    """Retorna el WhatsApp principal."""
    return get_primary_phone(sb, business_id, type="whatsapp")


def set_primary_phone(sb, business_id: str, phone: str) -> bool:
    """Marca un teléfono como primario."""
    p = get_phones(sb, business_id)
    tipo = next((x["type"] for x in p if x["phone"] == phone), "phone")
    sb.table("business_phones").update({"is_primary": False})\
      .eq("business_id", business_id)\
      .eq("type", tipo).execute()
    result = sb.table("business_phones").update({"is_primary": True})\
               .eq("business_id", business_id)\
               .eq("phone", phone).execute()
    return bool(result.data)


# ──────────────────────────────────────────────
# RESUMEN DE CONTACTOS
# ──────────────────────────────────────────────

def resumen_contactos(sb, business_id: str) -> dict:
    """
    Retorna resumen completo de contactos de un negocio.
    Útil para mostrar en el dashboard y para outreach.
    """
    emails = get_emails(sb, business_id)
    phones = get_phones(sb, business_id)

    return {
        "email_principal":    get_primary_email(sb, business_id),
        "whatsapp_principal": get_primary_whatsapp(sb, business_id),
        "telefono_principal": get_primary_phone(sb, business_id, "phone"),
        "total_emails":       len(emails),
        "total_phones":       len(phones),
        "emails":             emails,
        "phones":             phones,
        "needs_review":       len(emails) == 0 and len(phones) == 0,
    }


def missing_fields(sb, business_id: str) -> list[str]:
    """Retorna lista de campos de contacto faltantes."""
    emails = get_emails(sb, business_id)
    phones = get_phones(sb, business_id)
    wa     = [p for p in phones if p["type"] == "whatsapp"]
    tel    = [p for p in phones if p["type"] == "phone"]

    faltantes = []
    if not emails: faltantes.append("email")
    if not wa and not tel: faltantes.append("whatsapp_o_telefono")
    return faltantes