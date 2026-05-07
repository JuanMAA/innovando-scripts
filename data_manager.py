"""
data_manager.py — innovando-scripts
Módulo centralizado para leer y escribir en tabla business_data.
Todos los scorers lo usan en lugar de actualizar businesses directamente.

Uso:
    from data_manager import DataManager

    dm = DataManager(sb, business_id="uuid")

    # Guardar datos
    dm.set("maps", "rating", 4.2, source="google_maps", step="maps_scraper")
    dm.set("social", "instagram_url", "https://instagram.com/hostal", source="web")
    dm.set("lighthouse", "lh_performance", 42, source="pagespeed_api")

    # Guardar múltiples de una vez
    dm.set_many("maps", {
        "rating":      (4.2, "float"),
        "num_reviews": (38,  "int"),
        "has_hours":   (True, "boolean"),
    }, source="google_maps", step="maps_scraper")

    # Leer datos
    rating  = dm.get_float("maps", "rating")
    ig_url  = dm.get_text("social", "instagram_url")
    lh_perf = dm.get_int("lighthouse", "lh_performance")

    # Leer todos los datos de un módulo
    maps_data = dm.get_module("maps")
    # → {"rating": 4.2, "num_reviews": 38, ...}

    # Leer vista consolidada
    full = dm.get_full()
    # → {"maps_data": {...}, "web_data": {...}, ...}
"""

from datetime import datetime, timezone
from typing import Any


# ──────────────────────────────────────────────
# TIPOS DE VALOR
# ──────────────────────────────────────────────

def inferir_tipo(value: Any) -> str:
    """Infiere el tipo de valor automáticamente."""
    if isinstance(value, bool):   return "boolean"
    if isinstance(value, int):    return "int"
    if isinstance(value, float):  return "float"
    if isinstance(value, list):   return "json"
    if isinstance(value, dict):   return "json"
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            return "url"
    return "text"


def serializar(value: Any) -> str:
    """Convierte un valor a string para guardar en business_data."""
    import json
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def deserializar(value: str, value_type: str) -> Any:
    """Convierte un string de business_data a su tipo original."""
    import json
    if value is None:
        return None
    try:
        if value_type == "int":     return int(value)
        if value_type == "float":   return float(value)
        if value_type == "boolean": return value.lower() == "true"
        if value_type == "json":    return json.loads(value)
        return value  # text, url, date
    except Exception:
        return value


# ──────────────────────────────────────────────
# DATA MANAGER
# ──────────────────────────────────────────────

class DataManager:
    """
    Interfaz centralizada para tabla business_data.
    Instanciar una vez por negocio por script.
    """

    def __init__(self, sb, business_id: str):
        self.sb          = sb
        self.business_id = business_id
        self._cache      = {}   # cache local para evitar queries repetidas
        self._dirty      = []   # cambios pendientes de flush


    # ──────────────────────────────────────────
    # ESCRITURA
    # ──────────────────────────────────────────

    def set(
        self,
        module: str,
        key: str,
        value: Any,
        source: str = "sistema",
        step: str   = "sistema",
        verified: bool = False,
    ) -> bool:
        """
        Guarda un dato en business_data.
        Si ya existe el mismo module+key → actualiza (upsert).
        """
        if value is None:
            return False

        tipo  = inferir_tipo(value)
        valor = serializar(value)

        try:
            self.sb.table("business_data").upsert({
                "business_id":   self.business_id,
                "module":        module,
                "key":           key,
                "value":         valor,
                "value_type":    tipo,
                "source":        source,
                "found_at_step": step,
                "verified":      verified,
                "updated_at":    datetime.now(timezone.utc).isoformat(),
            }, on_conflict="business_id,module,key").execute()

            # Actualizar cache local
            if module not in self._cache:
                self._cache[module] = {}
            self._cache[module][key] = value
            return True

        except Exception as e:
            print(f"      ⚠️  DataManager.set error [{module}.{key}]: {e}")
            return False


    def set_many(
        self,
        module: str,
        data: dict,
        source: str = "sistema",
        step: str   = "sistema",
    ) -> int:
        """
        Guarda múltiples datos de un módulo de una vez.
        data = {"key": value} o {"key": (value, "type_hint")}
        Retorna cantidad guardada.
        """
        guardados = 0
        rows = []

        for key, item in data.items():
            if isinstance(item, tuple):
                value, _ = item
            else:
                value = item

            if value is None:
                continue

            tipo  = inferir_tipo(value)
            valor = serializar(value)

            rows.append({
                "business_id":   self.business_id,
                "module":        module,
                "key":           key,
                "value":         valor,
                "value_type":    tipo,
                "source":        source,
                "found_at_step": step,
                "verified":      False,
                "updated_at":    datetime.now(timezone.utc).isoformat(),
            })

            # Actualizar cache
            if module not in self._cache:
                self._cache[module] = {}
            self._cache[module][key] = value

        if rows:
            try:
                self.sb.table("business_data").upsert(
                    rows,
                    on_conflict="business_id,module,key"
                ).execute()
                guardados = len(rows)
            except Exception as e:
                print(f"      ⚠️  DataManager.set_many error [{module}]: {e}")

        return guardados


    # ──────────────────────────────────────────
    # LECTURA
    # ──────────────────────────────────────────

    def _load_module(self, module: str) -> dict:
        """Carga todos los datos de un módulo desde Supabase."""
        if module in self._cache:
            return self._cache[module]

        result = (
            self.sb.table("business_data")
            .select("key, value, value_type")
            .eq("business_id", self.business_id)
            .eq("module", module)
            .execute()
        )

        data = {}
        for row in (result.data or []):
            data[row["key"]] = deserializar(row["value"], row["value_type"])

        self._cache[module] = data
        return data


    def get_module(self, module: str) -> dict:
        """Retorna todos los datos de un módulo como dict."""
        return self._load_module(module)


    def get(self, module: str, key: str, default=None) -> Any:
        """Obtiene un valor por módulo y key."""
        data = self._load_module(module)
        return data.get(key, default)


    def get_text(self, module: str, key: str, default: str = None) -> str | None:
        return self.get(module, key, default)


    def get_int(self, module: str, key: str, default: int = 0) -> int:
        val = self.get(module, key)
        if val is None: return default
        try: return int(val)
        except: return default


    def get_float(self, module: str, key: str, default: float = 0.0) -> float:
        val = self.get(module, key)
        if val is None: return default
        try: return float(val)
        except: return default


    def get_bool(self, module: str, key: str, default: bool = False) -> bool:
        val = self.get(module, key)
        if val is None: return default
        if isinstance(val, bool): return val
        return str(val).lower() == "true"


    def get_json(self, module: str, key: str, default=None) -> Any:
        import json
        val = self.get(module, key)
        if val is None: return default
        if isinstance(val, (list, dict)): return val
        try: return json.loads(val)
        except: return default


    def get_url(self, module: str, key: str) -> str | None:
        return self.get(module, key)


    def get_full(self) -> dict:
        """
        Retorna todos los datos del negocio desde la vista v_business_full.
        Incluye datos de todos los módulos en una sola query.
        """
        try:
            result = (
                self.sb.table("v_business_full")
                .select("*")
                .eq("id", self.business_id)
                .maybeSingle()
                .execute()
            )
            return result.data or {}
        except Exception as e:
            print(f"      ⚠️  DataManager.get_full error: {e}")
            return {}


    def has(self, module: str, key: str) -> bool:
        """Retorna True si existe el dato."""
        return self.get(module, key) is not None


    def get_all_modules(self) -> dict[str, dict]:
        """Retorna todos los datos de todos los módulos."""
        result = (
            self.sb.table("business_data")
            .select("module, key, value, value_type")
            .eq("business_id", self.business_id)
            .execute()
        )

        all_data = {}
        for row in (result.data or []):
            mod = row["module"]
            if mod not in all_data:
                all_data[mod] = {}
            all_data[mod][row["key"]] = deserializar(row["value"], row["value_type"])

        self._cache = all_data
        return all_data


    # ──────────────────────────────────────────
    # UTILIDADES
    # ──────────────────────────────────────────

    def clear_cache(self):
        """Limpia el cache local para forzar re-lectura desde Supabase."""
        self._cache = {}


    def resumen(self) -> str:
        """Resumen de datos disponibles por módulo."""
        all_data = self.get_all_modules()
        partes = []
        for mod, data in sorted(all_data.items()):
            partes.append(f"{mod}: {len(data)} campos")
        return " | ".join(partes) if partes else "sin datos"


# ──────────────────────────────────────────────
# FUNCIONES DE CONVENIENCIA (sin instanciar DataManager)
# ──────────────────────────────────────────────

def set_data(sb, business_id: str, module: str, key: str, value: Any,
             source: str = "sistema", step: str = "sistema") -> bool:
    """Shortcut para guardar un dato sin instanciar DataManager."""
    dm = DataManager(sb, business_id)
    return dm.set(module, key, value, source=source, step=step)


def set_data_bulk(sb, business_id: str, module: str, data: dict,
                  source: str = "sistema", step: str = "sistema") -> int:
    """Shortcut para guardar múltiples datos."""
    dm = DataManager(sb, business_id)
    return dm.set_many(module, data, source=source, step=step)


def get_data(sb, business_id: str, module: str, key: str, default=None) -> Any:
    """Shortcut para leer un dato."""
    dm = DataManager(sb, business_id)
    return dm.get(module, key, default)


def get_business_full(sb, business_id: str) -> dict:
    """Retorna todos los datos del negocio desde v_business_full."""
    dm = DataManager(sb, business_id)
    return dm.get_full()


# ──────────────────────────────────────────────
# KEYS ESTÁNDAR POR MÓDULO
# (referencia para los scripts)
# ──────────────────────────────────────────────

KEYS = {
    "maps": [
        "rating", "num_reviews", "num_photos", "has_hours", "has_description",
        "price_level", "business_status", "open_now", "utc_offset_minutes",
        "review_trend", "review_keywords_pos", "review_keywords_neg",
        "photo_urls", "google_maps_url", "types", "reviews",
    ],
    "web": [
        "website", "ssl", "mobile_friendly", "load_time_ms",
        "has_contact_page", "has_booking_form",
        "instagram_url_found", "facebook_url_found",
    ],
    "lighthouse": [
        "lh_performance", "lh_seo", "lh_accessibility", "lh_best_practices",
        "lh_performance_desktop", "lh_seo_desktop",
        "lh_lcp_ms", "lh_fcp_ms", "lh_tbt_ms", "lh_cls",
        "lh_score", "lh_action", "lh_diagnosis",
    ],
    "social": [
        "instagram_url", "instagram_username", "instagram_followers",
        "instagram_last_post_days", "instagram_engagement",
        "facebook_url", "facebook_username", "facebook_followers",
        "facebook_last_post_days",
        "tiktok_url", "youtube_url", "x_url",
        "tripadvisor_url", "linktree_url",
    ],
    "platform": [
        "booking_url", "booking_rating", "booking_reviews",
        "booking_photos", "booking_price_avg", "booking_superhost",
        "airbnb_url", "airbnb_rating", "airbnb_reviews",
        "airbnb_price_night", "airbnb_superhost",
        "tripadvisor_url", "tripadvisor_rating", "tripadvisor_reviews",
        "tripadvisor_ranking", "tripadvisor_excellence",
        "expedia_url", "expedia_rating", "expedia_reviews",
        "despegar_url", "despegar_rating", "despegar_reviews",
        "platforms_count", "platforms_avg_rating",
    ],
    "huella": [
        "huella_score", "h1_score", "h2_score", "h3_score",
        "h4_score", "h5_score", "h6_score",
        "duplicate_listings", "nap_consistent",
        "negative_mentions", "inactive_social_profiles",
        "problems_count", "problems_easy", "problems_medium", "problems_hard",
    ],
    "contact": [
        "email", "email_source", "whatsapp", "phone",
    ],
}