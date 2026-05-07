"""
report_builder.py — innovando-scripts · Etapa 1
Construye el snapshot completo de un análisis y lo guarda en tabla reports.
Genera notes automáticas personalizadas por negocio.

Uso:
    python report_builder.py --env test --slug hostal-vista-al-mar
    python report_builder.py --env test --all   # genera para todos sin reporte
    python report_builder.py --env test --all --max 15

Requiere:
    pip install supabase python-dotenv
"""

import argparse
from datetime import datetime, timezone

from socials_manager import get_socials, get_social_url, score_redes, resumen_redes
from contact_manager import resumen_contactos, get_primary_email, get_primary_whatsapp
from data_manager import DataManager
from supabase_client import (
    get_client,
    get_leads_sin_reporte,
    get_business_by_slug,
    insert_report,
    update_business,
    now_iso,
)


# ──────────────────────────────────────────────
# CÁLCULO DE SCORES
# ──────────────────────────────────────────────

def calcular_score_p2b(business: dict) -> int:
    """Score P2b basado en si tiene sitio web."""
    if not business.get("website"):
        return 0
    # Score básico — scorer_web enriquece esto en Etapa 2
    return 8


def calcular_score_p2d(socials: list) -> int:
    """Score P2d basado en presencia en redes."""
    score = 0
    if get_social_url(socials, "instagram"): score += 5
    if get_social_url(socials, "facebook"):  score += 5
    return score


def calcular_score_total(business: dict, socials: list) -> int:
    """Score total 0-100."""
    return (
        (business.get("score_p2a") or 0) +
        calcular_score_p2b(business) +
        (business.get("score_p2c") or 0) +
        calcular_score_p2d(socials) +
        0  # score_p2f y score_huella: Etapa 2 y 3
    )


# ──────────────────────────────────────────────
# GENERACIÓN DE NOTAS
# ──────────────────────────────────────────────

def generar_general_note(business: dict, score_total: int) -> str:
    """Genera la nota general del reporte."""
    name = business.get("name", "Tu negocio")
    city = business.get("city", "tu city")

    if score_total <= 40:
        nivel = "por debajo del promedio"
        urgencia = "Hay oportunidades concretas de mejora que pueden impactar directamente las reservas."
    elif score_total <= 70:
        nivel = "en un nivel intermedio"
        urgencia = "Con algunos ajustes puntuales podés mejorar significativamente tu visibilidad."
    else:
        nivel = "por encima del promedio"
        urgencia = "Hay algunas optimizaciones menores que pueden maximizar tu alcance."

    sitio = ""
    if not business.get("website"):
        sitio = f" Sin sitio web propio, {name} no aparece en búsquedas de Google ni en ChatGPT."

    return (
        f"{name} tiene una presencia digital {nivel} "
        f"para negocios turísticos en {city}.{sitio} "
        f"{urgencia}"
    )


def generar_nota_p2a(business: dict) -> str:
    """Nota para módulo P2a - Ficha Google Maps."""
    name = business.get("name", "El negocio")
    num_fotos = business.get("num_photos") or 0
    has_hours = business.get("has_hours", False)
    tiene_desc = business.get("has_description", False)
    num_resenas = business.get("num_reviews") or 0
    rating = business.get("rating") or 0

    partes = []

    if num_fotos < 5:
        partes.append(
            f"Solo tiene {num_fotos} foto{'s' if num_fotos != 1 else ''} en Maps — "
            "las fichas con 10+ fotos reciben 520% más llamadas."
        )
    if not has_hours:
        partes.append("No tiene horarios publicados — Maps lo muestra como 'horario no disponible'.")
    if not tiene_desc:
        partes.append("Sin descripción editorial — no comunica qué hace especial al negocio.")
    if num_resenas < 10:
        partes.append(
            f"Con {num_resenas} reseña{'s' if num_resenas != 1 else ''}, "
            "los viajeros nuevos tienen poca referencia para decidir."
        )

    if not partes:
        return f"La ficha de Google Maps de {name} está bien configurada."

    return f"{name}: " + " ".join(partes)


def generar_nota_p2b(business: dict) -> str:
    """Nota para módulo P2b - Sitio web."""
    name = business.get("name", "El negocio")
    tiene_web = bool(business.get("website"))

    if not tiene_web:
        return (
            f"{name} no tiene sitio web propio. "
            "Sin sitio web, el negocio no aparece en búsquedas de Google "
            "ni en resultados de ChatGPT cuando alguien busca alojamiento en la zona."
        )
    return (
        f"{name} tiene sitio web. "
        "Se recomienda verificar que cargue correctamente en móvil y tenga SSL activo."
    )


def generar_nota_p2c(business: dict) -> str:
    """Nota para módulo P2c - Reputación."""
    name = business.get("name", "El negocio")
    num_resenas = business.get("num_reviews") or 0
    rating = business.get("rating") or 0

    if num_resenas == 0:
        return (
            f"{name} aún no tiene reseñas en Google Maps. "
            "Sin ratinges, el algoritmo de Maps reduce la visibilidad del negocio."
        )
    if num_resenas < 10:
        return (
            f"Con {num_resenas} reseña{'s' if num_resenas != 1 else ''} y {rating:.1f}★, "
            f"{name} necesita más opiniones para generar confianza. "
            "Los negocios con 20+ reseñas convierten 3x más visitas en reservas."
        )
    if rating < 4.0:
        return (
            f"{name} tiene {num_resenas} reseñas con promedio {rating:.1f}★. "
            "Una calificación bajo 4.0 afecta el posicionamiento en Maps y la tasa de conversión."
        )
    return (
        f"{name} tiene {num_resenas} reseñas con {rating:.1f}★. "
        "Buena reputación — mantener respondiendo a nuevas reseñas."
    )


def generar_nota_p2d(business: dict, socials: list = None, **kwargs) -> str:
    """Nota para módulo P2d - Redes sociales."""
    if socials is None:
        socials = kwargs.get("socials", [])
    name = business.get("name", "El negocio")
    tiene_ig = bool(get_social_url(socials, "instagram"))
    tiene_fb = bool(get_social_url(socials, "facebook"))
    tiene_tiktok = bool(get_social_url(socials, "tiktok"))

    if not tiene_ig and not tiene_fb:
        return (
            f"{name} no tiene presencia detectada en Instagram ni Facebook. "
            "El 78% de los viajeros menores de 40 años busca alojamiento en redes antes de reservar."
        )
    if not tiene_ig:
        return (
            f"{name} tiene Facebook pero no Instagram. "
            "Instagram es la red donde los viajeros descubren destinos turísticos."
        )
    if not tiene_fb:
        return (
            f"{name} tiene Instagram pero no Facebook. "
            "Facebook sigue siendo relevante para el segmento de turistas mayores de 40 años."
        )
    return f"{name} tiene presencia en Instagram y Facebook."


def generar_nota_p2f(business: dict) -> str:
    """Nota para módulo P2f - Plataformas (básico en Etapa 1)."""
    name = business.get("name", "El negocio")
    return (
        f"El análisis de platforms (Booking, Airbnb, TripAdvisor) "
        f"para {name} estará disponible en el próximo análisis completo."
    )


def generar_executive_summary(business: dict, score_total: int, **kwargs) -> str:
    """Genera el resumen ejecutivo del reporte."""
    name = business.get("name", "El negocio")
    city = business.get("city", "")
    category = business.get("category", "negocio")

    puntos_fuertes = []
    puntos_debiles = []

    socials = kwargs.get("socials", [])
    if business.get("rating", 0) >= 4.0:
        puntos_fuertes.append("buena calificación en Google")
    if (business.get("num_reviews") or 0) >= 10:
        puntos_fuertes.append(f"{business['num_reviews']} reseñas en Maps")
    if business.get("website"):
        puntos_fuertes.append("tiene sitio web")
    if get_social_url(socials, "instagram"):
        puntos_fuertes.append("presente en Instagram")

    if not business.get("website"):
        puntos_debiles.append("sin sitio web")
    if (business.get("num_photos") or 0) < 5:
        puntos_debiles.append("pocas fotos en Maps")
    if (business.get("num_reviews") or 0) < 10:
        puntos_debiles.append("pocas reseñas")
    if not get_social_url(socials, "instagram"):
        puntos_debiles.append("sin presencia en Instagram")

    resumen = f"Análisis de presencia digital de {name} ({category} en {city}). "
    resumen += f"Score total: {score_total}/100. "

    if puntos_fuertes:
        resumen += f"Puntos fuertes: {', '.join(puntos_fuertes)}. "
    if puntos_debiles:
        resumen += f"Oportunidades de mejora: {', '.join(puntos_debiles)}."

    return resumen


# ──────────────────────────────────────────────
# CONSTRUCCIÓN DEL REPORTE
# ──────────────────────────────────────────────

def _get_social_url(socials: list, network: str) -> str | None:
    """Helper para obtener URL de una red."""
    return get_social_url(socials, network)


def build_report(sb, business: dict, tipo: str = "auto") -> dict:
    """
    Construye el snapshot completo y lo guarda en tabla reports.
    Actualiza latest_report_id en businesses.
    """
    socials        = get_socials(sb, business["id"])
    score_p2a      = business.get("score_p2a") or 0
    score_p2b      = calcular_score_p2b(business)
    score_rep      = business.get("score_p2c") or 0
    score_p2d      = calcular_score_p2d(socials)
    score_total    = calcular_score_total(business, socials)

    general_note   = generar_general_note(business, score_total)
    resumen        = generar_executive_summary(business, score_total, socials=socials)

    report_data = {
        "business_id":       business["id"],
        "type":              tipo,
        "generated_at":       now_iso(),
        "generated_by":      "sistema",
        "score_total":       score_total,
        "score_p2a":        score_p2a,
        "score_p2b":         score_p2b,
        "score_p2c":  score_rep,
        "score_p2d":       score_p2d,
        "score_p2e":         0,
        "score_p2f": 0,
        "score_huella":      0,
        "executive_summary": resumen,
        "general_note":      general_note,
        "general_note_edited": False,
        "is_public":        True,
        "version":           1,

        # Módulos con snapshot de datos
        "modulo_p2a": {
            "score": score_p2a,
            "nota": generar_nota_p2a(business),
            "nota_editada": False,
            "datos": {
                "num_photos":         business.get("num_photos") or 0,
                "has_hours":    business.get("has_hours", False),
                "has_description": business.get("has_description", False),
                "rating":      business.get("rating") or 0,
                "num_reviews":       business.get("num_reviews") or 0,
                "phone":          business.get("phone"),
            }
        },
        "modulo_p2b": {
            "score": score_p2b,
            "nota": generar_nota_p2b(business),
            "nota_editada": False,
            "datos": {
                "website":                business.get("website"),
                "lh_performance": business.get("lh_performance"),
                "lh_seo":         business.get("lh_seo"),
                "lighthouse_accessibility":business.get("lh_accessibility"),
                "lighthouse_best_practices":business.get("lh_best_practices"),
                "lh_lcp_ms":      business.get("lh_lcp_ms"),
                "lh_fcp_ms":      business.get("lh_fcp_ms"),
                "lh_cls":         business.get("lh_cls"),
                "lh_score": business.get("lh_score"),
                "lh_action":      business.get("lh_action"),
                "lh_diagnosis": business.get("lh_diagnosis"),
                "lh_date":       business.get("lh_date"),
            }
        },
        "modulo_p2c": {
            "score": score_rep,
            "nota": generar_nota_p2c(business),
            "nota_editada": False,
            "datos": {
                "rating": business.get("rating") or 0,
                "num_reviews":  business.get("num_reviews") or 0,
            }
        },
        "modulo_p2d": {
            "score": score_p2d,
            "nota": generar_nota_p2d(business, socials=socials),
            "nota_editada": False,
            "datos": {
                "instagram_url": get_social_url(socials, "instagram"),
                "facebook_url":  get_social_url(socials, "facebook"),
            }
        },
        "modulo_p2e": {
            "score": 0,
            "nota": "Análisis SEO disponible próximamente.",
            "nota_editada": False,
            "datos": {}
        },
        "modulo_p2f": {
            "score": 0,
            "nota": generar_nota_p2f(business),
            "nota_editada": False,
            "datos": {}
        },
        "modulo_huella": {
            "score": 0,
            "nota": "Análisis de huella digital disponible próximamente.",
            "nota_editada": False,
            "datos": {}
        },
    }

    # Guardar reporte en Supabase
    report = insert_report(sb, report_data)

    if report.get("id"):
        update_business(sb, business["place_id"], {
            "latest_report_id": report["id"],
            "status":           "analyzed",
            "score_total":      score_total,
            "score_p2b":        score_p2b,
            "score_p2d":        score_p2d,
        })

    # Sincronizar campo de referencia rápida en businesses
    primary_email = get_primary_email(sb, business["id"])
    primary_wa    = get_primary_whatsapp(sb, business["id"])
    if primary_email or primary_wa:
        update_business(sb, business["place_id"], {
            "email":    primary_email,
            "whatsapp": primary_wa,
        })

    return report


# ──────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="innovando-scripts · Report Builder")
    parser.add_argument("--env",  required=True, choices=["test", "prd"])
    parser.add_argument("--slug", default=None, help="Generar reporte para un negocio específico")
    parser.add_argument("--all",  action="store_true", help="Generar para todos los leads sin reporte")
    parser.add_argument("--max",  type=int, default=None)
    args = parser.parse_args()

    if not args.slug and not args.all:
        print("❌ Especificá --slug NOMBRE o --all")
        return

    sb = get_client(env=args.env)

    if args.slug:
        business = get_business_by_slug(sb, args.slug)
        if not business:
            print(f"❌ No se encontró negocio con slug '{args.slug}'")
            return
        leads = [business]
    else:
        leads = get_leads_sin_reporte(sb)

    if args.max:
        leads = leads[:args.max]

    total = len(leads)
    print(f"\n📄 Generando {total} reportes | environment '{args.env}'...")

    generados = 0
    errores = 0

    for i, lead in enumerate(leads, 1):
        name = lead.get("name", f"Lead {i}")
        print(f"[{i}/{total}] {name}...", end=" ")

        try:
            report = build_report(sb, lead)
            score = report.get("score_total", 0)
            print(f"✅ Score: {score}/100")
            generados += 1
        except Exception as e:
            print(f"❌ Error: {e}")
            errores += 1

    print(f"\n{'='*55}")
    print(f"✅ Reportes generados: {generados} | ❌ Errores: {errores}")
    print(f"{'='*55}")
    print(f"\n➡️  Siguiente paso: python outreach_email.py --env {args.env} --modo preview")


if __name__ == "__main__":
    main()