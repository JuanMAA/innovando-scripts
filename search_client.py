"""
search_client.py — innovando-scripts
Cliente unificado de búsqueda web con distribución inteligente entre APIs.

Distribución por tipo de búsqueda:
  Google CSE → búsquedas con operador site: (plataformas OTA, directorios)
               Más preciso para búsquedas estructuradas.
               Límite: 100/día gratis · $0.005/búsqueda después

  Brave      → búsquedas abiertas (menciones en medios, negativos, ChatGPT)
               Más apto para texto libre sin operadores.
               Límite: 2000/mes gratis (plan free)

Reglas de fallback:
  Si Google CSE está al límite → intentar Brave para site: queries
  Si Brave no está disponible  → Playwright headless como último recurso

Uso:
    from search_client import SearchClient

    sc = SearchClient(cse_key, cse_id, brave_key, tracker)

    # Búsqueda en sitio específico → Google CSE
    url = sc.buscar_en_sitio("Hostal Vista", "Ancud", "booking.com")

    # Búsqueda abierta → Brave
    resultados = sc.buscar_menciones("Hostal Vista Ancud opiniones negativas")
"""

import time
import requests
from typing import Optional


# ──────────────────────────────────────────────
# CONFIGURACIÓN
# ──────────────────────────────────────────────

GOOGLE_CSE_URL = "https://www.googleapis.com/customsearch/v1"
BRAVE_URL      = "https://api.search.brave.com/res/v1/web/search"

# Dominios genéricos que no son perfiles de negocio
URL_GENERICAS = [
    "/search", "/s/", "/hotel-search", "/hotels/", "/rooms/",
    "/Tourism-", "/Restaurants-", "/Hotels-",
    "expedia.com/Hotel-Search", "despegar.com/hoteles/",
    "airbnb.com/s/", "airbnb.com/rooms",
    "booking.com/searchresults",
]


# ──────────────────────────────────────────────
# CLIENTE UNIFICADO
# ──────────────────────────────────────────────

class SearchClient:
    """
    Distribuye búsquedas entre Google CSE y Brave según el tipo de query.
    Registra uso en APITracker si se proporciona.
    """

    def __init__(
        self,
        cse_key: str | None = None,
        cse_id:  str | None = None,
        brave_key: str | None = None,
        tracker=None,
    ):
        self.cse_key   = cse_key
        self.cse_id    = cse_id
        self.brave_key = brave_key
        self.tracker   = tracker

    # ──────────────────────────────────────────
    # BÚSQUEDA EN SITIO ESPECÍFICO (site:dominio)
    # Destino primario: Google CSE
    # Fallback: Brave con site: operator
    # ──────────────────────────────────────────

    def buscar_en_sitio(
        self,
        nombre: str,
        ciudad: str,
        dominio: str,
        num: int = 3,
    ) -> str | None:
        """
        Busca el perfil de un negocio en un dominio específico.
        Usa Google CSE primero (mejor para site:), Brave como fallback.
        Retorna la primera URL válida encontrada.
        """
        queries = [
            f'site:{dominio} "{nombre}" "{ciudad}"',
            f'site:{dominio} "{nombre}"',
        ]

        # ── Intento 1: Google CSE ──────────────
        if self.cse_key and self.cse_id:
            if not self.tracker or self.tracker.puede_usar("google_cse", len(queries)):
                for query in queries:
                    url = self._google_cse(query, num)
                    if url and not self._es_generica(url):
                        return url
            else:
                print(f"   ⚠️  Google CSE: límite alcanzado — usando Brave")

        # ── Fallback: Brave ────────────────────
        if self.brave_key:
            for query in queries:
                resultados = self._brave(query, num)
                for r in resultados:
                    url = r.get("url", "")
                    if dominio in url and not self._es_generica(url):
                        return url

        return None

    # ──────────────────────────────────────────
    # BÚSQUEDA ABIERTA (menciones, medios, texto libre)
    # Destino primario: Brave
    # Fallback: Google CSE
    # ──────────────────────────────────────────

    def buscar_menciones(
        self,
        query: str,
        num: int = 10,
        exclude_domains: list[str] | None = None,
    ) -> list[dict]:
        """
        Búsqueda abierta para menciones, noticias, negativos.
        Retorna lista de {url, title, snippet}.
        Destino primario: Brave (mejor para texto libre, más cuota).
        """
        q = query
        if exclude_domains:
            for d in exclude_domains:
                q += f" -site:{d}"

        # ── Intento 1: Brave ───────────────────
        if self.brave_key:
            if not self.tracker or self.tracker.puede_usar("brave", 1):
                resultados = self._brave(q, num)
                if resultados:
                    return resultados

        # ── Fallback: Google CSE ───────────────
        if self.cse_key and self.cse_id:
            if not self.tracker or self.tracker.puede_usar("google_cse", 1):
                url = self._google_cse_raw(q, num)
                return url or []

        return []

    # ──────────────────────────────────────────
    # BÚSQUEDA DE PRESENCIA EN MÚLTIPLES DOMINIOS
    # Para verificar NAP consistency
    # ──────────────────────────────────────────

    def buscar_en_varios_dominios(
        self,
        nombre: str,
        ciudad: str,
        dominios: list[str],
    ) -> dict[str, str | None]:
        """
        Busca el negocio en múltiples dominios de una vez.
        Distribuye entre Google CSE y Brave para minimizar uso de CSE.
        Retorna {dominio: url_encontrada}.
        """
        resultados = {}

        # Separar en dos grupos: primeros con CSE, resto con Brave
        mitad = len(dominios) // 2
        dominios_cse   = dominios[:mitad] if self.cse_key else []
        dominios_brave = dominios[mitad:] if self.brave_key else dominios

        for dominio in dominios_cse:
            url = self.buscar_en_sitio(nombre, ciudad, dominio)
            resultados[dominio] = url
            time.sleep(0.3)

        for dominio in dominios_brave:
            query = f'site:{dominio} "{nombre}" "{ciudad}"'
            brave_res = self._brave(query, num=3)
            url = None
            for r in brave_res:
                candidate = r.get("url", "")
                if dominio in candidate and not self._es_generica(candidate):
                    url = candidate
                    break
            resultados[dominio] = url
            time.sleep(0.2)

        return resultados

    # ──────────────────────────────────────────
    # MÉTODOS INTERNOS
    # ──────────────────────────────────────────

    def _google_cse(self, query: str, num: int = 3) -> str | None:
        """Busca en Google CSE y retorna la primera URL relevante."""
        try:
            resp = requests.get(
                GOOGLE_CSE_URL,
                params={"key": self.cse_key, "cx": self.cse_id,
                        "q": query, "num": min(num, 10)},
                timeout=10,
            )
            if self.tracker:
                self.tracker.track("google_cse", used=1)
            items = resp.json().get("items", [])
            return items[0].get("link") if items else None
        except Exception:
            return None

    def _google_cse_raw(self, query: str, num: int = 10) -> list[dict]:
        """Busca en Google CSE y retorna lista de {url, title, snippet}."""
        try:
            resp = requests.get(
                GOOGLE_CSE_URL,
                params={"key": self.cse_key, "cx": self.cse_id,
                        "q": query, "num": min(num, 10)},
                timeout=10,
            )
            if self.tracker:
                self.tracker.track("google_cse", used=1)
            items = resp.json().get("items", [])
            return [
                {"url": i.get("link", ""),
                 "title": i.get("title", ""),
                 "snippet": i.get("snippet", "")}
                for i in items
            ]
        except Exception:
            return []

    def _brave(self, query: str, num: int = 10) -> list[dict]:
        """Busca en Brave Search y retorna lista de {url, title, snippet}."""
        try:
            resp = requests.get(
                BRAVE_URL,
                headers={
                    "Accept":              "application/json",
                    "Accept-Encoding":     "gzip",
                    "X-Subscription-Token": self.brave_key,
                },
                params={"q": query, "count": min(num, 20), "country": "CL",
                        "search_lang": "es", "ui_lang": "es-CL"},
                timeout=10,
            )
            if self.tracker:
                self.tracker.track("brave", used=1)
            results = resp.json().get("web", {}).get("results", [])
            return [
                {"url": r.get("url", ""),
                 "title": r.get("title", ""),
                 "snippet": r.get("description", "")}
                for r in results
            ]
        except Exception:
            return []

    def _es_generica(self, url: str) -> bool:
        """Detecta si una URL es una página genérica (listado, búsqueda)."""
        url_lower = url.lower()
        return any(g.lower() in url_lower for g in URL_GENERICAS)

    # ──────────────────────────────────────────
    # DIAGNÓSTICO
    # ──────────────────────────────────────────

    def estado(self) -> str:
        """Muestra qué APIs están disponibles."""
        lineas = []
        if self.cse_key and self.cse_id:
            restante = "?"
            if self.tracker:
                from api_usage_tracker import API_CONFIG
                limite = API_CONFIG["google_cse"]["limite_diario"]
                usado  = self.tracker.uso_hoy("google_cse")
                restante = max(0, limite - usado)
            lineas.append(f"🔍 Google CSE: ✅ disponible ({restante} restantes hoy)")
        else:
            lineas.append("🔍 Google CSE: ❌ no configurado (GOOGLE_CSE_KEY + GOOGLE_CSE_ID)")

        if self.brave_key:
            restante = "?"
            if self.tracker:
                from api_usage_tracker import API_CONFIG
                limite = API_CONFIG["brave"]["limite_mensual"]
                usado  = self.tracker.uso_mes("brave")
                restante = max(0, limite - usado)
            lineas.append(f"🦁 Brave:      ✅ disponible ({restante} restantes este mes)")
        else:
            lineas.append("🦁 Brave:      ❌ no configurado (BRAVE_SEARCH_KEY)")

        return "\n".join(lineas)
