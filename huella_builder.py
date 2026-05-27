"""
huella_builder.py — innovando-scripts

Genera un informe de **Auditoría Huella Digital** a partir de un lead capturado
por el formulario del landing (servicio `auditoria-huella-digital`).

Pipeline v1 (MVP):
  1. Lee datos del sujeto desde la tabla `leads`.
  2. Chequea brechas de seguridad contra HaveIBeenPwned (HIBP).
  3. Busca exposición pública con Brave Search (data brokers, archive.org,
     directorios, redes públicas).
  4. Calcula scores (exposure / privacy / risk_level) por reglas.
  5. Arma plan de acción priorizado por reglas.
  6. Guarda todo en la tabla `huella_audits`.

Uso:
    # Listar leads pendientes
    python huella_builder.py --env test --list

    # Generar para un lead específico
    python huella_builder.py --env test --lead-id <uuid>

    # Re-procesar audit existente
    python huella_builder.py --env test --audit-id <uuid> --rebuild

    # Dry-run (no guarda en BD, solo imprime)
    python huella_builder.py --env test --lead-id <uuid> --dry-run --verbose

Variables en .env.test / .env.prd:
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY
    HIBP_API_KEY                  (opcional — sin ella se omiten brechas)
    BRAVE_SEARCH_KEY              (opcional — sin ella se omite búsqueda pública)
    GOOGLE_CSE_KEY                (opcional — fallback de Brave)
    GOOGLE_CSE_ID                 (opcional — fallback de Brave)
"""

import argparse
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests

from supabase_client import get_client
from search_client   import SearchClient
from api_usage_tracker import APITracker


# ──────────────────────────────────────────────────────────────────────
# CONSTANTES
# ──────────────────────────────────────────────────────────────────────

HIBP_BASE = "https://haveibeenpwned.com/api/v3"
HIBP_RATE_DELAY_S = 6  # HIBP requiere ≥1.5s entre requests; subimos margen.

# Dominios reconocidos como "data brokers" (alto riesgo si aparecen).
DATA_BROKER_DOMAINS = {
    "truecaller.com", "spokeo.com", "beenverified.com", "pipl.com",
    "fastpeoplesearch.com", "whitepages.com", "intelius.com",
    "zoominfo.com", "rocketreach.co", "hunter.io",
    "rapportive.com", "voilanorbert.com",
}

# Dominios de directorios públicos (riesgo medio).
DIRECTORY_DOMAINS = {
    "amarillas.cl", "paginasamarillas.com", "yellowpages.com",
    "linkedin.com",  # perfiles públicos
    "guiadelocal.cl", "hotfrog.cl", "yelp.com", "foursquare.com",
}

# Plataformas que merecen revisión de privacidad.
SOCIAL_PLATFORMS = ["instagram", "facebook", "linkedin", "twitter", "tiktok",
                    "youtube", "reddit", "github"]


# ──────────────────────────────────────────────────────────────────────
# CONTEXT
# ──────────────────────────────────────────────────────────────────────

@dataclass
class AuditContext:
    """Datos del sujeto a auditar."""
    subject_name:  str
    subject_type:  str = "persona"           # 'persona' | 'empresa'
    email_main:    str | None = None
    emails_extra:  list[str]  = field(default_factory=list)
    socials:       dict[str, str] = field(default_factory=dict)  # {network: url}
    domains:       list[str]  = field(default_factory=list)
    phone:         str | None = None

    def all_emails(self) -> list[str]:
        out = []
        if self.email_main: out.append(self.email_main)
        for e in self.emails_extra:
            if e and e not in out:
                out.append(e)
        return out


# ──────────────────────────────────────────────────────────────────────
# 1 · HIBP — Brechas de seguridad
# ──────────────────────────────────────────────────────────────────────

def _hibp_severity(data_classes: list[str]) -> str:
    """Clasifica severidad por tipo de datos expuestos."""
    high_signals = {"Passwords", "Credit cards", "Social security numbers",
                    "Government issued IDs", "Bank account numbers"}
    med_signals  = {"Phone numbers", "Physical addresses", "Geographic locations",
                    "Dates of birth", "Email messages"}
    cls = set(data_classes or [])
    if cls & high_signals: return "alta"
    if cls & med_signals:  return "media"
    return "baja"


def check_breaches(emails: list[str], hibp_key: str | None,
                   verbose: bool = False) -> list[dict]:
    """Para cada email, consulta HIBP y retorna lista de brechas detectadas."""
    if not hibp_key:
        if verbose: print("  ⚠ HIBP_API_KEY no configurada — omitiendo brechas.")
        return []
    if not emails:
        return []

    out: list[dict] = []
    headers = {
        "hibp-api-key": hibp_key,
        "user-agent":   "innovando-huella-builder/1.0",
    }

    for email in emails:
        if verbose: print(f"  🔎 HIBP · {email}")
        try:
            r = requests.get(
                f"{HIBP_BASE}/breachedaccount/{email}",
                params={"truncateResponse": "false"},
                headers=headers,
                timeout=15,
            )
            if r.status_code == 404:
                if verbose: print("    ✓ Sin brechas")
            elif r.status_code == 200:
                breaches = r.json()
                for b in breaches:
                    out.append({
                        "source":   b.get("Title") or b.get("Name"),
                        "domain":   b.get("Domain"),
                        "date":     b.get("BreachDate"),
                        "data":     ", ".join(b.get("DataClasses", []))[:200],
                        "severity": _hibp_severity(b.get("DataClasses")),
                        "verified": bool(b.get("IsVerified")),
                        "email":    email,
                    })
                if verbose: print(f"    ⚠ {len(breaches)} brecha(s)")
            elif r.status_code == 429:
                if verbose: print("    ⏳ Rate limit, durmiendo 30s")
                time.sleep(30)
            else:
                if verbose: print(f"    ✗ HTTP {r.status_code} — {r.text[:120]}")
        except Exception as e:
            if verbose: print(f"    ✗ Error: {e}")
        time.sleep(HIBP_RATE_DELAY_S)

    # Ordenar por severidad (alta primero) y fecha
    out.sort(key=lambda x: (
        {"alta": 0, "media": 1, "baja": 2}.get(x["severity"], 3),
        x.get("date") or "",
    ), reverse=False)
    return out


# ──────────────────────────────────────────────────────────────────────
# 2 · Brave Search — Exposición pública
# ──────────────────────────────────────────────────────────────────────

def _classify_url(url: str, snippet: str) -> tuple[str, str]:
    """Devuelve (type, risk) según el dominio del resultado."""
    host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0].lower()
    if any(b in host for b in DATA_BROKER_DOMAINS):
        return ("Data broker", "alto")
    if "archive.org" in host or "web.archive.org" in host:
        return ("Wayback Machine", "medio")
    if any(d in host for d in DIRECTORY_DOMAINS):
        return ("Directorio público", "medio")
    if any(s in host for s in SOCIAL_PLATFORMS):
        return ("Red social pública", "bajo")
    return ("Resultado público", "bajo")


def check_public_search(ctx: AuditContext, search_client: SearchClient | None,
                         verbose: bool = False) -> list[dict]:
    """Busca exposición pública del sujeto en internet."""
    if not search_client:
        if verbose: print("  ⚠ Brave Search no configurado — omitiendo búsqueda pública.")
        return []

    queries: list[str] = []

    # Nombre del sujeto (entrecomillado para match exacto)
    if ctx.subject_name:
        queries.append(f'"{ctx.subject_name}"')

    # Emails — directos a data brokers
    for email in ctx.all_emails()[:3]:  # cap 3 para no explotar cuota
        queries.append(f'"{email}"')

    # Teléfono
    if ctx.phone:
        queries.append(f'"{ctx.phone}"')

    # Wayback explícito
    if ctx.subject_name:
        queries.append(f'site:web.archive.org "{ctx.subject_name}"')

    out: list[dict] = []
    seen_urls: set[str] = set()

    for q in queries:
        if verbose: print(f"  🔎 Brave · {q}")
        try:
            results = search_client.buscar_menciones(q, num=10)
            for r in results or []:
                url = r.get("url", "").strip()
                if not url or url in seen_urls: continue
                seen_urls.add(url)
                tipo, risk = _classify_url(url, r.get("snippet", ""))
                out.append({
                    "url":   url,
                    "type":  tipo,
                    "text":  (r.get("snippet") or r.get("title") or "")[:240],
                    "risk":  risk,
                })
        except Exception as e:
            if verbose: print(f"    ✗ Error: {e}")
        time.sleep(1)

    # Ordenar por riesgo
    out.sort(key=lambda x: {"alto": 0, "medio": 1, "bajo": 2}.get(x["risk"], 3))
    return out[:20]  # cap a 20 hallazgos


# ──────────────────────────────────────────────────────────────────────
# 3 · Reglas — Scores, resumen y plan de acción
# ──────────────────────────────────────────────────────────────────────

def _exposure_score(breaches: list[dict], public_search: list[dict],
                    socials: dict[str, str], domains: list[str]) -> int:
    """0-100. Más alto = más expuesto = peor.

    Pesos (sumas, max 100):
      - Brechas:        alta=15  media=8  baja=3  (cap 50)
      - Búsqueda pub:   alto=10  medio=5  bajo=2  (cap 35)
      - Cada red pública conocida: +2 (cap 10)
      - Cada dominio público con WHOIS: +1 (cap 5)
    """
    score = 0

    # Brechas
    sub = 0
    for b in breaches:
        sub += {"alta": 15, "media": 8, "baja": 3}.get(b.get("severity"), 0)
    score += min(50, sub)

    # Search público
    sub = 0
    for r in public_search:
        sub += {"alto": 10, "medio": 5, "bajo": 2}.get(r.get("risk"), 0)
    score += min(35, sub)

    # Redes públicas
    score += min(10, len(socials) * 2)

    # Dominios
    score += min(5, len(domains))

    return min(100, score)


def _privacy_score(exposure: int, socials: dict[str, str]) -> int:
    """Inverso aproximado: privacy = 100 - exposure, ajustado por redes."""
    score = 100 - exposure
    # Penalización adicional si tiene >5 redes (sobreexposición)
    if len(socials) > 5:
        score -= 5
    return max(0, min(100, score))


def _risk_level(exposure: int) -> str:
    if exposure >= 70: return "alto"
    if exposure >= 40: return "medio"
    return "bajo"


def _resumen(ctx: AuditContext, breaches: list[dict],
             public_search: list[dict], exposure: int) -> str:
    """Resumen ejecutivo de 1-2 párrafos por reglas."""
    parts: list[str] = []
    riesgo = _risk_level(exposure)

    parts.append(
        f"Tu huella digital tiene un **nivel de exposición {riesgo}** "
        f"(score {exposure}/100)."
    )

    if breaches:
        altas = sum(1 for b in breaches if b["severity"] == "alta")
        if altas:
            parts.append(
                f"Detectamos **{len(breaches)} brecha(s)** de seguridad, "
                f"{altas} de severidad alta. Es prioritario actualizar contraseñas "
                f"y activar 2FA en los servicios afectados."
            )
        else:
            parts.append(
                f"Detectamos **{len(breaches)} brecha(s)** de seguridad, "
                f"sin filtraciones críticas. Aun así, recomendamos rotar contraseñas."
            )
    else:
        parts.append("No detectamos filtraciones recientes en bases públicas.")

    altos = [r for r in public_search if r["risk"] == "alto"]
    if altos:
        parts.append(
            f"Encontramos **{len(altos)} resultados de alto riesgo** en internet "
            f"(data brokers o sitios sensibles). Solicitar baja de esos perfiles "
            f"reduce significativamente tu exposición."
        )
    elif public_search:
        parts.append(
            f"Tu información aparece en {len(public_search)} sitios públicos, "
            f"pero sin riesgo crítico inmediato."
        )

    return " ".join(parts)


def _recommendations(ctx: AuditContext, breaches: list[dict],
                     public_search: list[dict]) -> list[dict]:
    """Plan de acción priorizado en 3 fases."""
    rapidas: list[str] = []
    semana:  list[str] = []
    mes:     list[str] = []

    # Brechas → 2FA + cambio de contraseñas
    if breaches:
        for b in breaches[:5]:
            rapidas.append(
                f"Cambiar contraseña y activar 2FA en **{b['source']}** "
                f"(filtración de {b['date'] or 'fecha desconocida'})."
            )
        rapidas.append("Usar un gestor de contraseñas (1Password, Bitwarden) y "
                       "no reutilizar credenciales entre servicios.")

    # Redes
    if ctx.socials:
        for net in ctx.socials.keys():
            semana.append(
                f"Revisar configuración de privacidad en **{net}**: "
                f"qué información es pública y limitar lo necesario."
            )

    # Data brokers
    altos = [r for r in public_search if r["risk"] == "alto"]
    for r in altos[:5]:
        host = re.sub(r"^https?://(www\.)?", "", r["url"]).split("/")[0]
        semana.append(
            f"Solicitar baja del perfil en **{host}** (data broker)."
        )

    # Wayback
    wayback = [r for r in public_search if r["type"] == "Wayback Machine"]
    if wayback:
        mes.append("Pedir a Archive.org la baja de páginas antiguas con "
                   "información sensible (formulario \"Remove from Wayback\").")

    # Dominios
    if ctx.domains:
        mes.append("Activar WHOIS Privacy en tus dominios para ocultar "
                   "datos de contacto personales.")

    # Recomendaciones generales si quedan vacías
    if not rapidas:
        rapidas.append("Activar 2FA en cuentas críticas (Gmail, banco, redes).")
    if not semana:
        semana.append("Auditar configuración de privacidad en cada red social pública.")
    if not mes:
        mes.append("Suscribirse a alertas de filtraciones (haveibeenpwned.com/NotifyMe).")

    return [
        {"phase": "rapidas", "label": "Rápidas (< 1 hora)",    "items": rapidas},
        {"phase": "semana",  "label": "Esta semana",            "items": semana},
        {"phase": "mes",     "label": "Este mes",               "items": mes},
    ]


# ──────────────────────────────────────────────────────────────────────
# 4 · I/O Supabase
# ──────────────────────────────────────────────────────────────────────

SUBJECT_TYPES_BY_BIZ = {
    "tecnologia": "empresa", "ecommerce": "empresa", "servicios": "empresa",
    "finanzas":   "empresa", "agencia":   "empresa",
    "personal":   "persona",
}

LEAD_SERVICE_VALUES = (
    "auditoria-huella-digital",      # ES (slug que usa el landing)
    "digital-footprint-audit",       # EN
    "auditoria-pegada-digital",      # PT
    "audit-empreinte-numerique",     # FR
)


def _parse_emails_extra(raw: str | None) -> list[str]:
    """Parsea el textarea emails_extra: separa por coma o salto de línea."""
    if not raw:
        return []
    parts = re.split(r"[,\n;]+", raw)
    return [p.strip() for p in parts if p.strip() and "@" in p]


def _parse_domains(raw: str | None) -> list[str]:
    """Parsea el textarea domains: separa por coma/espacio/salto y limpia."""
    if not raw:
        return []
    parts = re.split(r"[,\s;]+", raw)
    out = []
    for p in parts:
        p = p.strip().lower()
        if not p:
            continue
        # quitar protocolo y path
        p = re.sub(r"^https?://(www\.)?", "", p).split("/")[0]
        if "." in p and p not in out:
            out.append(p)
    return out


def lead_to_context(lead: dict) -> AuditContext:
    """Construye AuditContext desde una fila de la tabla leads."""
    socials: dict[str, str] = {}
    if (v := lead.get("instagram")):        socials["instagram"] = v
    if (v := lead.get("facebook")):         socials["facebook"]  = v
    if (v := lead.get("website_linkedin")):
        v_lower = v.lower()
        if "linkedin" in v_lower:
            socials["linkedin"] = v
        else:
            # se trata de un sitio propio → va a dominios
            pass

    # Dominios: prioridad al campo nuevo `domains`, fallback a website_linkedin/website_url
    domains: list[str] = _parse_domains(lead.get("domains"))
    for src_field in ("website_url", "website_linkedin"):
        site = lead.get(src_field)
        if site and "linkedin" not in (site or "").lower():
            d = re.sub(r"^https?://(www\.)?", "", site).split("/")[0]
            if "." in d and d.lower() not in domains:
                domains.append(d.lower())

    # Emails extra (campo nuevo del form)
    emails_extra = _parse_emails_extra(lead.get("emails_extra"))

    # Tipo de sujeto
    subject_type = SUBJECT_TYPES_BY_BIZ.get(
        (lead.get("business_type") or "").lower(),
        "persona",
    )

    return AuditContext(
        subject_name = lead.get("name") or "Sin nombre",
        subject_type = subject_type,
        email_main   = lead.get("email"),
        emails_extra = emails_extra,
        socials      = socials,
        domains      = domains,
        phone        = lead.get("phone"),
    )


def fetch_lead(sb, lead_id: str) -> dict | None:
    r = sb.table("leads").select("*").eq("id", lead_id).maybe_single().execute()
    return r.data if r else None


def list_pending_leads(sb, limit: int = 20) -> list[dict]:
    """Lista leads de huella digital sin audit done todavía."""
    r = (sb.table("leads")
           .select("id, name, email, service_interest, status, created_at")
           .in_("service_interest", list(LEAD_SERVICE_VALUES))
           .order("created_at", desc=True)
           .limit(limit)
           .execute())
    leads = r.data or []
    # Filtrar los que ya tienen audit done
    if leads:
        ids = [l["id"] for l in leads]
        existing = (sb.table("huella_audits")
                    .select("lead_id, status")
                    .in_("lead_id", ids)
                    .execute()).data or []
        done_ids = {a["lead_id"] for a in existing if a["status"] == "done"}
        leads = [l for l in leads if l["id"] not in done_ids]
    return leads


def upsert_audit(sb, payload: dict, audit_id: str | None = None) -> str:
    """Inserta o actualiza la fila en huella_audits. Devuelve el id."""
    payload = {**payload, "updated_at": datetime.now(timezone.utc).isoformat()}
    if audit_id:
        sb.table("huella_audits").update(payload).eq("id", audit_id).execute()
        return audit_id
    r = sb.table("huella_audits").insert(payload).execute()
    return r.data[0]["id"]


# ──────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────

def build_audit(ctx: AuditContext, hibp_key: str | None,
                search_client: SearchClient | None,
                verbose: bool = False) -> dict:
    """Pipeline completo. Devuelve dict listo para guardar en BD."""
    print(f"\n→ Auditando: {ctx.subject_name} ({ctx.subject_type})")
    print(f"  Email:    {ctx.email_main or '—'}")
    print(f"  Redes:    {list(ctx.socials.keys()) or '—'}")
    print(f"  Dominios: {ctx.domains or '—'}")

    print("\n[1/3] Chequeando brechas (HIBP)…")
    breaches = check_breaches(ctx.all_emails(), hibp_key, verbose=verbose)
    print(f"      {len(breaches)} brecha(s) detectada(s)")

    print("\n[2/3] Buscando exposición pública (Brave)…")
    public_search = check_public_search(ctx, search_client, verbose=verbose)
    print(f"      {len(public_search)} resultado(s) públicos")

    print("\n[3/3] Calculando scores y plan de acción…")
    exposure = _exposure_score(breaches, public_search, ctx.socials, ctx.domains)
    privacy  = _privacy_score(exposure, ctx.socials)
    risk     = _risk_level(exposure)
    resumen  = _resumen(ctx, breaches, public_search, exposure)
    recs     = _recommendations(ctx, breaches, public_search)
    print(f"      exposure_score={exposure}  privacy_score={privacy}  risk={risk}")

    return {
        "subject_type":      ctx.subject_type,
        "subject_name":      ctx.subject_name,
        "email_main":        ctx.email_main,
        "emails_extra":      ctx.emails_extra,
        "socials":           ctx.socials,
        "domains":           ctx.domains,
        "phone":             ctx.phone,
        "exposure_score":    exposure,
        "privacy_score":     privacy,
        "risk_level":        risk,
        "resumen":           resumen,
        "breaches":          breaches,
        "metadata_findings": [],   # v1: vacío (no procesamos archivos)
        "social_privacy":    [],   # v1: vacío (no scrapeamos redes)
        "public_search":     public_search,
        "domains_data":      [],   # v1: vacío (no consultamos WHOIS aún)
        "recommendations":   recs,
        "status":            "done",
        "generated_at":      datetime.now(timezone.utc).isoformat(),
    }


def main():
    parser = argparse.ArgumentParser(description="Generador de Auditoría Huella Digital")
    parser.add_argument("--env",       choices=["test", "prd"], default="test")
    parser.add_argument("--lead-id",   help="UUID de un lead a auditar")
    parser.add_argument("--audit-id",  help="UUID de un huella_audit existente (re-ejecuta)")
    parser.add_argument("--list",      action="store_true", help="Lista leads pendientes y sale")
    parser.add_argument("--dry-run",   action="store_true", help="No guarda en BD")
    parser.add_argument("--verbose",   action="store_true")
    args = parser.parse_args()

    sb = get_client(env=args.env)

    if args.list:
        leads = list_pending_leads(sb)
        if not leads:
            print("Sin leads pendientes de auditoría huella digital.")
            return
        print(f"\n{len(leads)} lead(s) pendiente(s):\n")
        for l in leads:
            print(f"  {l['id']}  ·  {l['name']}  ·  {l['email'] or '—'}  ·  {l['created_at'][:10]}")
        return

    if not args.lead_id and not args.audit_id:
        parser.error("Tenés que pasar --lead-id, --audit-id o --list")

    # ── Cargar contexto ──
    if args.audit_id:
        prev = sb.table("huella_audits").select("*").eq("id", args.audit_id).maybe_single().execute()
        if not prev or not prev.data:
            parser.error(f"audit-id {args.audit_id} no encontrado")
        d = prev.data
        ctx = AuditContext(
            subject_name = d.get("subject_name") or "—",
            subject_type = d.get("subject_type") or "persona",
            email_main   = d.get("email_main"),
            emails_extra = d.get("emails_extra") or [],
            socials      = d.get("socials") or {},
            domains      = d.get("domains") or [],
            phone        = d.get("phone"),
        )
        lead_id = d.get("lead_id")
    else:
        lead = fetch_lead(sb, args.lead_id)
        if not lead:
            parser.error(f"lead-id {args.lead_id} no encontrado")
        ctx = lead_to_context(lead)
        lead_id = args.lead_id

    # ── APIs ──
    hibp_key  = os.getenv("HIBP_API_KEY")
    brave_key = os.getenv("BRAVE_SEARCH_KEY") or os.getenv("BRAVE_API_KEY")
    cse_key   = os.getenv("GOOGLE_CSE_KEY")   or os.getenv("GOOGLE_CSE_API_KEY")
    cse_id    = os.getenv("GOOGLE_CSE_ID")

    tracker = APITracker(sb)
    search_client = None
    if brave_key or (cse_key and cse_id):
        search_client = SearchClient(cse_key, cse_id, brave_key, tracker)
    elif args.verbose:
        print("⚠ Sin BRAVE_API_KEY ni GOOGLE_CSE — omitiendo búsqueda pública.")

    # ── Marcar como running ──
    if not args.dry_run:
        running = {"status": "running", "lead_id": lead_id, **(
            {} if args.audit_id else {"subject_name": ctx.subject_name}
        )}
        audit_id = upsert_audit(sb, running, args.audit_id)
        print(f"\naudit_id: {audit_id}")
    else:
        audit_id = None

    # ── Pipeline ──
    try:
        payload = build_audit(ctx, hibp_key, search_client, verbose=args.verbose)
        payload["lead_id"] = lead_id
    except Exception as e:
        if not args.dry_run and audit_id:
            upsert_audit(sb, {"status": "error", "error_msg": str(e)}, audit_id)
        raise

    # ── Guardar ──
    if args.dry_run:
        print("\n────────── DRY-RUN — JSON resultante ──────────")
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str)[:4000])
        print("…")
        return

    final_id = upsert_audit(sb, payload, audit_id)
    print(f"\n✅ Audit guardado: {final_id}")
    print(f"   exposure={payload['exposure_score']} · privacy={payload['privacy_score']} · risk={payload['risk_level']}")
    print(f"   breaches={len(payload['breaches'])} · public_search={len(payload['public_search'])}")


if __name__ == "__main__":
    main()
