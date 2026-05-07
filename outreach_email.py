"""
outreach_email.py — innovando-scripts · Etapa 1
Genera y envía emails personalizados de outreach via SendGrid.
Solo procesa leads con reporte generado + email disponible.

Uso:
    # Revisar emails antes de enviar (genera HTML por lead)
    python outreach_email.py --env test --modo preview

    # Enviar primeros 5 para testear
    python outreach_email.py --env test --max 5

    # Enviar a todos los leads listos
    python outreach_email.py --env test

    # Solo una city
    python outreach_email.py --env prd --city "Ancud, Chile"

Requiere:
    pip install sendgrid supabase python-dotenv
"""

import argparse
import os
from datetime import datetime, timezone

import sendgrid
from sendgrid.helpers.mail import Mail, Email, To, Content

from supabase_client import (
    get_client,
    get_leads_sin_contactar,
    get_latest_report,
    registrar_outreach,
    get_precio,
    now_iso,
)


# ──────────────────────────────────────────────
# CONFIGURACIÓN
# ──────────────────────────────────────────────

REPORTES_URL = os.getenv("REPORTES_URL", "https://reportes.innovando.cl")


# ──────────────────────────────────────────────
# TEMPLATE DEL EMAIL
# ──────────────────────────────────────────────

def generar_asunto(business: dict, report: dict) -> str:
    score = report.get("score_total", 0)
    name = business.get("name", "tu negocio")
    return f"Tu presencia digital en Google: {score}/100 — {name}"


def generar_html(
    business: dict,
    report: dict,
    precio: dict,
    firma_name: str,
    firma_cargo: str,
    firma_whatsapp: str,
) -> str:
    """Genera el HTML del email personalizado."""
    name      = business.get("name", "tu negocio")
    score       = report.get("score_total", 0)
    diagnostico = business.get("sales_diagnosis", "")
    city      = business.get("city", "tu city")
    category   = (business.get("category") or "negocio").lower()
    slug        = business.get("slug", "")
    reporte_url = f"{REPORTES_URL}/{slug}"
    precio_txt  = precio.get("price_display", "$20 USD")

    # Color del score
    if score <= 40:
        score_color = "#E24B4A"
        score_label = "Crítica"
    elif score <= 70:
        score_color = "#E3A008"
        score_label = "Regular"
    else:
        score_color = "#0E9F6E"
        score_label = "Buena"

    # Intro personalizada por categoría
    intro_map = {
        "hotel":       f"Estuve revisando hoteles en {city} en Google Maps y me detuve en <b>{name}</b>.",
        "hostal":      f"Estuve analizando hostales en {city} y revisé la ficha de <b>{name}</b> en Google Maps.",
        "restaurante": f"Estuve analizando restaurantes en {city} y revisé la ficha de <b>{name}</b> en Google Maps.",
        "cabaña":      f"Estuve revisando cabañas turísticas en {city} y encontré la ficha de <b>{name}</b>.",
    }
    intro = intro_map.get(category, f"Estuve analizando negocios turísticos en {city} y revisé la ficha de <b>{name}</b>.")

    # Checklist de estado
    tiene_tel  = "✅" if business.get("phone")      else "❌"
    tiene_web  = "✅" if business.get("website") else "❌"
    tiene_ig   = "✅" if business.get("instagram_url")  else "❌"
    tiene_res  = "✅" if (business.get("num_reviews") or 0) >= 10 else "❌"

    # Bloque de diagnóstico
    diag_html = ""
    if diagnostico:
        diag_html = f"""
        <tr>
          <td style="padding:12px 24px;">
            <table width="100%" cellpadding="12" cellspacing="0"
                   style="background:#FEF3C7;border-left:4px solid #F59E0B;border-radius:4px;">
              <tr>
                <td style="font-family:Arial,sans-serif;font-size:14px;color:#92400E;">
                  ⚠️ <b>Punto crítico:</b> {diagnostico}
                </td>
              </tr>
            </table>
          </td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#F3F4F6;font-family:Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" bgcolor="#F3F4F6">
<tr><td align="center" style="padding:24px 16px;">

  <table width="600" cellpadding="0" cellspacing="0"
         style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1);">

    <!-- HEADER -->
    <tr>
      <td style="background:#1A56DB;padding:20px 24px;">
        <p style="margin:0;color:#fff;font-size:18px;font-weight:bold;">Innovando</p>
        <p style="margin:4px 0 0;color:#BFDBFE;font-size:12px;">Auditoría de Presencia Digital Turística</p>
      </td>
    </tr>

    <!-- SALUDO -->
    <tr><td style="padding:24px 24px 8px;">
      <p style="margin:0;font-size:15px;color:#111928;">Hola, ¿cómo están en <b>{name}</b>?</p>
    </td></tr>

    <!-- INTRO -->
    <tr><td style="padding:8px 24px;">
      <p style="margin:0;font-size:14px;color:#374151;line-height:1.6;">{intro}</p>
    </td></tr>

    <!-- SCORE CARD -->
    <tr><td style="padding:16px 24px;">
      <table width="100%" cellpadding="0" cellspacing="0"
             style="background:#F9FAFB;border:1px solid #E5E7EB;border-radius:8px;overflow:hidden;">
        <tr>
          <td style="padding:20px;text-align:center;border-right:1px solid #E5E7EB;" width="40%">
            <p style="margin:0;font-size:48px;font-weight:bold;color:{score_color};line-height:1;">{score}</p>
            <p style="margin:4px 0 0;font-size:13px;color:#6B7280;">de 100 puntos</p>
            <p style="margin:8px 0 0;font-size:11px;font-weight:bold;color:{score_color};
                      text-transform:uppercase;letter-spacing:.05em;">
              Presencia {score_label}
            </p>
          </td>
          <td style="padding:16px 20px;" width="60%">
            <p style="margin:0 0 8px;font-size:12px;font-weight:bold;color:#111928;
                      text-transform:uppercase;letter-spacing:.05em;">Estado actual</p>
            <p style="margin:0 0 6px;font-size:12px;color:#374151;">{tiene_tel} Teléfono en Maps</p>
            <p style="margin:0 0 6px;font-size:12px;color:#374151;">{tiene_web} Sitio web propio</p>
            <p style="margin:0 0 6px;font-size:12px;color:#374151;">{tiene_ig} Instagram</p>
            <p style="margin:0;font-size:12px;color:#374151;">{tiene_res} 10+ reseñas en Google</p>
          </td>
        </tr>
      </table>
    </td></tr>

    {diag_html}

    <!-- CUERPO -->
    <tr><td style="padding:8px 24px 16px;">
      <p style="margin:0 0 12px;font-size:14px;color:#374151;line-height:1.6;">
        Un puntaje de <b style="color:{score_color};">{score}/100</b> indica que hay oportunidades
        concretas para mejorar la visibilidad de <b>{name}</b>.
        Preparamos un diagnóstico completo con el desglose módulo por módulo
        y un plan de acción priorizado.
      </p>
    </td></tr>

    <!-- CTA -->
    <tr><td style="padding:8px 24px 24px;text-align:center;">
      <table cellpadding="0" cellspacing="0" style="margin:0 auto;">
        <tr>
          <td style="background:#1A56DB;border-radius:6px;padding:12px 28px;">
            <a href="{reporte_url}"
               style="color:#fff;font-size:14px;font-weight:bold;text-decoration:none;">
              → Ver mi diagnóstico completo — {precio_txt}
            </a>
          </td>
        </tr>
      </table>
      <p style="margin:12px 0 0;font-size:12px;color:#6B7280;">
        O respondé este email y te lo enviamos directamente.
      </p>
    </td></tr>

    <!-- SEPARADOR -->
    <tr><td style="padding:0 24px;">
      <hr style="border:none;border-top:1px solid #E5E7EB;margin:0;">
    </td></tr>

    <!-- FIRMA -->
    <tr><td style="padding:16px 24px 24px;">
      <p style="margin:0;font-size:13px;color:#374151;">
        <b>{firma_name}</b><br>
        <span style="color:#6B7280;font-size:12px;">{firma_cargo}</span>
      </p>
      <p style="margin:8px 0 0;font-size:12px;color:#6B7280;">
        📱 WhatsApp: {firma_whatsapp}
      </p>
      <p style="margin:4px 0 0;font-size:11px;color:#9CA3AF;">
        Si no querés recibir más emails, respondé con "REMOVER".
      </p>
    </td></tr>

  </table>
</td></tr>
</table>
</body>
</html>"""


def generar_texto_plano(business: dict, report: dict, precio: dict, firma_name: str, firma_whatsapp: str) -> str:
    """Versión plain text del email."""
    name      = business.get("name", "tu negocio")
    score       = report.get("score_total", 0)
    diagnostico = business.get("sales_diagnosis", "")
    slug        = business.get("slug", "")
    reporte_url = f"{REPORTES_URL}/{slug}"
    precio_txt  = precio.get("price_display", "$20 USD")

    return f"""Hola, ¿cómo están en {name}?

Analizamos tu presencia digital y encontramos:

RESULTADO: {score}/100 puntos

{('⚠️ ' + diagnostico) if diagnostico else ''}

Hay oportunidades concretas para mejorar la visibilidad de {name}.

Ver diagnóstico completo ({precio_txt}):
→ {reporte_url}

---
{firma_name}
WhatsApp: {firma_whatsapp}

Para darse de baja, responda "REMOVER".
"""


# ──────────────────────────────────────────────
# ENVÍO
# ──────────────────────────────────────────────

def enviar_email(
    sg_client,
    from_email: str,
    from_name: str,
    to_email: str,
    asunto: str,
    html: str,
    texto: str,
) -> bool:
    """Envía un email via SendGrid."""
    message = Mail(
        from_email=Email(from_email, from_name),
        to_emails=To(to_email),
        subject=asunto,
        html_content=Content("text/html", html),
        plain_text_content=Content("text/plain", texto),
    )
    response = sg_client.send(message)
    return 200 <= response.status_code < 300


# ──────────────────────────────────────────────
# PIPELINE PRINCIPAL
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="innovando-scripts · Outreach Email")
    parser.add_argument("--env",    required=True, choices=["test", "prd"])
    parser.add_argument("--modo",   default="send", choices=["send", "preview"])
    parser.add_argument("--max",    type=int, default=None)
    parser.add_argument("--city", default=None)
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv(f".env.{args.env}")
    from api_usage_tracker import APITracker
    tracker = APITracker(env=args.env)
    from contact_manager import get_primary_email

    # Credenciales
    sg_api_key      = os.getenv("SENDGRID_API_KEY")
    email_from      = os.getenv("EMAIL_FROM", "hola@innovando.cl")
    email_from_name = os.getenv("EMAIL_FROM_NAME", "Innovando")
    firma_name    = os.getenv("FIRMA_NOMBRE", "El equipo de Innovando")
    firma_cargo     = os.getenv("FIRMA_CARGO", "Especialista en presencia digital turística")
    firma_whatsapp  = os.getenv("FIRMA_WHATSAPP", "")
    reportes_url    = os.getenv("REPORTES_URL", "https://reportes.innovando.cl")

    global REPORTES_URL
    REPORTES_URL = reportes_url

    sb = get_client(env=args.env)

    # Obtener leads listos para contactar
    leads = get_leads_sin_contactar(sb)

    if args.city:
        city_name = args.city.split(",")[0].strip()
        leads = [l for l in leads if l.get("city") == city_name]

    if args.max:
        leads = leads[:args.max]

    total = len(leads)
    print(f"\n📧 {total} leads para outreach | modo '{args.modo}' | environment '{args.env}'")

    if args.modo == "preview":
        print("\n" + "="*55)
        print("MODO PREVIEW — Ningún email será enviado")
        print("="*55)

    sg_client = sendgrid.SendGridAPIClient(api_key=sg_api_key) if args.modo == "send" else None

    enviados = 0
    errores  = 0

    for i, lead in enumerate(leads, 1):
        name   = lead.get("name", f"Lead {i}")
        email_to = lead.get("email", "")

        if not email_to or "@" not in str(email_to):
            print(f"[{i}/{total}] {name} — sin email válido, saltando")
            continue

        print(f"[{i}/{total}] {name} → {email_to}")

        # Obtener último reporte
        report = get_latest_report(sb, lead["id"])
        if not report:
            print(f"   ⚠️  Sin reporte generado, saltando")
            continue

        # Obtener precio en moneda local
        precio_data = get_precio(
            sb,
            country=lead.get("country", "Chile"),
            service="reporte_completo",
            category=lead.get("category", "todos")
        ) or {"precio": 20, "moneda": "USD", "price_display": "$20 USD"}

        if es_cerrado:
            nombre_neg = lead.get("name", "")
            asunto = f"Tu negocio aparece como cerrado en Google Maps — {nombre_neg}"
        else:
            asunto = generar_asunto(lead, report)
        html   = generar_html(lead, report, precio_data, firma_name, firma_cargo, firma_whatsapp)
        texto  = generar_texto_plano(lead, report, precio_data, firma_name, firma_whatsapp)

        if args.modo == "preview":
            # Guardar HTML para revisar en navegador
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:40]
            path = f"preview_{safe}.html"
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"   📄 Asunto: {asunto}")
            print(f"   💾 HTML: {path}")
            continue

        # Enviar
        try:
            ok = enviar_email(sg_client, email_from, email_from_name, email_to, asunto, html, texto)
            if ok:
                registrar_outreach(sb, lead["id"], "email_teaser", "enviado", {
                    "subject": asunto,
                    "email_to": email_to,
                })
                # Actualizar estado del lead
                from supabase_client import update_business
                update_business(sb, lead["place_id"], {"status": "contactado"})
                print(f"   ✅ Enviado")
                enviados += 1
                tracker.track("sendgrid", used=1)
            else:
                print(f"   ❌ Error al enviar")
                errores += 1
        except Exception as e:
            print(f"   ❌ Error: {e}")
            errores += 1

    if args.modo == "send":
        tracker.resumen("outreach_email.py")
        print(f"\n{'='*55}")
        print(f"✅ Enviados: {enviados} | ❌ Errores: {errores}")
        print(f"{'='*55}")
    else:
        print(f"\n✅ Preview completo. Revisá los archivos HTML generados.")
        print(f"   Si todo se ve bien → python outreach_email.py --env {args.env} --max 5")


if __name__ == "__main__":
    main()