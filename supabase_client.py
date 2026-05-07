"""
supabase_client.py — innovando-scripts
Cliente Supabase compartido con soporte de ambientes test/prd.

Uso:
    from supabase_client import get_client
    sb = get_client(env="test")
"""

import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from supabase import create_client, Client


# ──────────────────────────────────────────────
# CARGA DE VARIABLES DE ENTORNO
# ──────────────────────────────────────────────

def _load_env(env: str):
    """Carga el archivo .env correspondiente al ambiente."""
    env_file = f".env.{env}"
    if not os.path.exists(env_file):
        raise FileNotFoundError(
            f"No se encontró {env_file}. "
            f"Copiá .env.example a {env_file} y completá las credenciales."
        )
    load_dotenv(env_file, override=True)


def get_client(env: str = "test") -> Client:
    """Retorna cliente Supabase para el ambiente indicado."""
    _load_env(env)
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise ValueError(
            f"SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY no definidos en .env.{env}"
        )
    return create_client(url, key)


def now_iso() -> str:
    """Retorna timestamp ISO 8601 en UTC."""
    return datetime.now(timezone.utc).isoformat()


# ──────────────────────────────────────────────
# BUSINESSES
# ──────────────────────────────────────────────

def upsert_business(sb: Client, data: dict) -> dict:
    """Crea o actualiza un negocio por place_id."""
    data["updated_at"] = now_iso()
    result = sb.table("businesses").upsert(
        data,
        on_conflict="place_id"
    ).execute()
    return result.data[0] if result.data else {}


def update_business(sb: Client, place_id: str, fields: dict) -> dict:
    """Actualiza campos específicos de un negocio."""
    fields["updated_at"] = now_iso()
    result = sb.table("businesses").update(fields).eq("place_id", place_id).execute()
    return result.data[0] if result.data else {}


def get_business_by_place_id(sb: Client, place_id: str) -> dict | None:
    """Obtiene un negocio por place_id."""
    result = sb.table("businesses").select("*").eq("place_id", place_id).single().execute()
    return result.data


def get_business_by_slug(sb: Client, slug: str) -> dict | None:
    """Obtiene un negocio por slug."""
    result = sb.table("businesses").select("*").eq("slug", slug).maybeSingle().execute()
    return result.data


def get_leads_sin_email(sb: Client) -> list[dict]:
    """Leads sin email — candidatos para scorer_web y scorer_redes."""
    result = sb.table("businesses").select("*").is_("email", "null").execute()
    return result.data or []


def get_leads_sin_reporte(sb: Client) -> list[dict]:
    """Leads sin reporte generado aún."""
    result = sb.table("businesses").select("*").is_("latest_report_id", "null").execute()
    return result.data or []


def get_leads_sin_contactar(sb: Client) -> list[dict]:
    """Leads con reporte y email pero sin outreach enviado."""
    result = (
        sb.table("businesses")
        .select("*")
        .not_.is_("latest_report_id", "null")
        .not_.is_("email", "null")
        .eq("estado", "analizado")
        .execute()
    )
    return result.data or []


def get_leads_por_estado(sb: Client, estado: str) -> list[dict]:
    """Leads filtrados por estado del pipeline."""
    result = sb.table("businesses").select("*").eq("status", estado).execute()
    return result.data or []


def get_leads_requieren_relleno(sb: Client) -> list[dict]:
    """Leads con flag needs_review = true."""
    result = sb.table("businesses").select("*").eq("needs_review", True).execute()
    return result.data or []


# ──────────────────────────────────────────────
# REPORTS
# ──────────────────────────────────────────────

def insert_report(sb: Client, data: dict) -> dict:
    """Inserta un nuevo reporte."""
    result = sb.table("reports").insert(data).execute()
    return result.data[0] if result.data else {}


def get_latest_report(sb: Client, business_id: str) -> dict | None:
    """Obtiene el último reporte de un negocio."""
    result = (
        sb.table("reports")
        .select("*")
        .eq("business_id", business_id)
        .order("generado_at", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def get_all_reports(sb: Client, business_id: str) -> list[dict]:
    """Obtiene todos los reportes de un negocio ordenados por fecha."""
    result = (
        sb.table("reports")
        .select("*")
        .eq("business_id", business_id)
        .order("generado_at", desc=True)
        .execute()
    )
    return result.data or []


def update_report_nota(sb: Client, report_id: str, campo: str, nota: str) -> dict:
    """Actualiza una nota de módulo en un reporte y marca como editada."""
    fields = {
        campo: nota,
        f"{campo}_editada": True
    }
    result = sb.table("reports").update(fields).eq("id", report_id).execute()
    return result.data[0] if result.data else {}


# ──────────────────────────────────────────────
# OUTREACH LOGS
# ──────────────────────────────────────────────

def registrar_outreach(
    sb: Client,
    business_id: str,
    tipo: str,
    estado: str,
    metadata: dict = {}
) -> dict:
    """Registra un envío de email o WhatsApp."""
    result = sb.table("outreach_logs").insert({
        "business_id": business_id,
        "tipo": tipo,
        "estado": estado,
        "enviado_at": now_iso(),
        "metadata": metadata
    }).execute()
    return result.data[0] if result.data else {}


def get_outreach_by_business(sb: Client, business_id: str) -> list[dict]:
    """Historial de outreach de un negocio."""
    result = (
        sb.table("outreach_logs")
        .select("*")
        .eq("business_id", business_id)
        .order("enviado_at", desc=True)
        .execute()
    )
    return result.data or []


# ──────────────────────────────────────────────
# CITIES
# ──────────────────────────────────────────────

def update_city(sb: Client, nombre: str, pais: str, fields: dict) -> dict:
    """Actualiza stats de una ciudad."""
    fields["updated_at"] = now_iso()
    result = (
        sb.table("cities")
        .update(fields)
        .eq("name", nombre)
        .eq("country", pais)
        .execute()
    )
    return result.data[0] if result.data else {}


def get_city(sb: Client, nombre: str, pais: str) -> dict | None:
    """Obtiene datos de una ciudad."""
    result = (
        sb.table("cities")
        .select("*")
        .eq("name", nombre)
        .eq("country", pais)
        .maybeSingle()
        .execute()
    )
    return result.data


def upsert_city(sb: Client, nombre: str, pais: str, fields: dict = {}) -> dict:
    """Crea o actualiza una ciudad."""
    data = {"name": nombre, "country": pais, **fields, "updated_at": now_iso()}
    result = sb.table("cities").upsert(data, on_conflict="name,country").execute()
    return result.data[0] if result.data else {}


# ──────────────────────────────────────────────
# COUNTRY PRICING
# ──────────────────────────────────────────────

def get_precio(
    sb: Client,
    pais: str,
    servicio: str,
    categoria: str = "todos"
) -> dict | None:
    """
    Busca el precio configurado para país + servicio.
    Intenta primero con la categoría específica, luego con 'todos'.
    Retorna None si no hay configuración (usar precio base USD).
    """
    # Buscar precio específico por categoría
    result = (
        sb.table("country_pricing")
        .select("price, currency, price_display")
        .eq("country", pais)
        .eq("service", servicio)
        .eq("category", categoria)
        .eq("active", True)
        .maybeSingle()
        .execute()
    )
    if result.data:
        return result.data

    # Fallback a 'todos'
    result = (
        sb.table("country_pricing")
        .select("price, currency, price_display")
        .eq("country", pais)
        .eq("service", servicio)
        .eq("category", "todos")
        .eq("active", True)
        .maybeSingle()
        .execute()
    )
    return result.data