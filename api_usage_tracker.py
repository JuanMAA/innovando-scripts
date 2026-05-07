"""
api_usage_tracker.py — innovando-scripts
Registra el uso de APIs en cada iteración y muestra resumen al final.
Guarda el historial en un archivo local .api_usage.json por environment.

Uso:
    from api_usage_tracker import APITracker
    tracker = APITracker(env="test")

    # Registrar uso
    tracker.track("google_cse", used=2)
    tracker.track("google_places", used=15)
    tracker.track("pagespeed", used=6)  # 2 por lead (mobile + desktop)
    tracker.track("brave", used=0)

    # Mostrar resumen al final del script
    tracker.resumen()
"""

import json
import os
from datetime import datetime, timezone, date


# ──────────────────────────────────────────────
# LÍMITES Y COSTOS POR API
# ──────────────────────────────────────────────

API_CONFIG = {
    "google_places": {
        "name":       "Google Places API",
        "limite_diario": None,          # sin límite estricto
        "limite_mensual": None,
        "credito_free":  200,           # $200 USD/mes de crédito
        "costo_por_uso": 0.017,         # $0.017 por búsqueda Text Search
        "unidad":        "búsquedas",
        "emoji":         "🗺️",
    },
    "pagespeed": {
        "name":        "PageSpeed Insights API",
        "limite_diario":  25000,        # 25.000/día gratis
        "limite_mensual": None,
        "credito_free":   None,
        "costo_por_uso":  0,            # gratis
        "unidad":         "análisis",
        "emoji":          "🔦",
    },
    "google_cse": {
        "name":        "Google Custom Search API",
        "limite_diario":  100,          # 100/día gratis
        "limite_mensual": None,
        "credito_free":   None,
        "costo_por_uso":  0.005,        # $0.005 por búsqueda después del límite
        "unidad":         "búsquedas",
        "emoji":          "🔍",
    },
    "brave": {
        "name":        "Brave Search API",
        "limite_diario":  None,
        "limite_mensual": 2000,         # 2000/mes gratis
        "credito_free":   None,
        "costo_por_uso":  0,            # gratis en plan free
        "unidad":         "búsquedas",
        "emoji":          "🦁",
    },
    "sendgrid": {
        "name":        "SendGrid",
        "limite_diario":  100,          # 100/día en plan free
        "limite_mensual": None,
        "credito_free":   None,
        "costo_por_uso":  0,
        "unidad":         "emails",
        "emoji":          "📧",
    },
}


# ──────────────────────────────────────────────
# TRACKER
# ──────────────────────────────────────────────

class APITracker:
    """
    Registra el uso de APIs en la sesión actual.
    Persiste en .api_usage_{env}.json para acumular el historial diario.
    """

    def __init__(self, env: str = "test"):
        self.env       = env
        self.filepath  = f".api_usage_{env}.json"
        self.hoy       = str(date.today())
        self.session   = {}   # uso en esta sesión
        self.data      = self._cargar()

    def _cargar(self) -> dict:
        """Carga el historial guardado."""
        if os.path.exists(self.filepath):
            try:
                return json.load(open(self.filepath))
            except Exception:
                pass
        return {}

    def _guardar(self):
        """Persiste el historial."""
        json.dump(self.data, open(self.filepath, "w"), indent=2)

    def track(self, api: str, used: int):
        """
        Registra el uso de una API.
        api:  clave de API_CONFIG (ej: "google_cse")
        used: cantidad usada en esta llamada
        """
        if used <= 0:
            return

        # Acumular en sesión actual
        self.session[api] = self.session.get(api, 0) + used

        # Acumular en historial del día
        if self.hoy not in self.data:
            self.data[self.hoy] = {}
        if api not in self.data[self.hoy]:
            self.data[self.hoy][api] = 0
        self.data[self.hoy][api] += used

        self._guardar()

    def uso_hoy(self, api: str) -> int:
        """Total usado hoy para una API."""
        return self.data.get(self.hoy, {}).get(api, 0)

    def uso_mes(self, api: str) -> int:
        """Total usado este mes para una API."""
        mes_actual = self.hoy[:7]  # "2025-04"
        total = 0
        for dia, apis in self.data.items():
            if dia.startswith(mes_actual):
                total += apis.get(api, 0)
        return total

    def resumen(self, script_name: str = ""):
        """
        Muestra el resumen de uso al final de un script.
        Incluye: usado en sesión, usado hoy, límite, % restante.
        """
        if not self.session:
            return

        titulo = f"📊 Uso de APIs"
        if script_name:
            titulo += f" — {script_name}"
        print(f"\n{'='*55}")
        print(titulo)
        print(f"{'='*55}")

        for api, usado_sesion in self.session.items():
            config = API_CONFIG.get(api, {
                "name": api,
                "limite_diario": None,
                "limite_mensual": None,
                "costo_por_uso": 0,
                "unidad": "llamadas",
                "emoji": "📡",
                "credito_free": None,
            })

            emoji  = config["emoji"]
            name = config["name"]
            unidad = config["unidad"]
            usado_hoy = self.uso_hoy(api)
            usado_mes = self.uso_mes(api)

            print(f"\n{emoji}  {name}")
            print(f"   Esta sesión: {usado_sesion} {unidad}")
            print(f"   Hoy total:   {usado_hoy} {unidad}")

            # Límite diario
            if config["limite_diario"]:
                restante = config["limite_diario"] - usado_hoy
                pct_usado = round(usado_hoy / config["limite_diario"] * 100)
                barra = _barra_progreso(pct_usado)
                estado = _semaforo_uso(pct_usado)
                print(f"   Límite día:  {usado_hoy}/{config['limite_diario']} {barra} {estado}")
                print(f"   Restante:    {max(0, restante)} {unidad} hoy")

            # Límite mensual
            if config["limite_mensual"]:
                restante_mes = config["limite_mensual"] - usado_mes
                pct_mes = round(usado_mes / config["limite_mensual"] * 100)
                barra = _barra_progreso(pct_mes)
                estado = _semaforo_uso(pct_mes)
                print(f"   Límite mes:  {usado_mes}/{config['limite_mensual']} {barra} {estado}")
                print(f"   Restante:    {max(0, restante_mes)} {unidad} este mes")

            # Costo estimado
            if config["costo_por_uso"] and config["costo_por_uso"] > 0:
                costo_sesion = round(usado_sesion * config["costo_por_uso"], 4)
                costo_mes    = round(usado_mes    * config["costo_por_uso"], 4)
                if config.get("credito_free"):
                    print(f"   Costo sesión: ${costo_sesion} USD")
                    print(f"   Costo mes:    ${costo_mes} USD / ${config['credito_free']} crédito free")
                else:
                    print(f"   Costo sesión: ${costo_sesion} USD")

        print(f"\n{'='*55}")
        print(f"📁 Historial guardado en: {self.filepath}")
        print(f"{'='*55}\n")

    def alerta_limite(self, api: str, umbral_pct: int = 80) -> bool:
        """
        Retorna True si se superó el umbral de uso.
        Útil para frenar el script antes de agotar el límite.
        """
        config = API_CONFIG.get(api, {})
        limite = config.get("limite_diario") or config.get("limite_mensual")
        if not limite:
            return False

        if config.get("limite_diario"):
            usado = self.uso_hoy(api)
        else:
            usado = self.uso_mes(api)

        pct = round(usado / limite * 100)
        if pct >= umbral_pct:
            print(f"⚠️  {API_CONFIG[api]['name']}: {pct}% del límite usado ({usado}/{limite})")
            return True
        return False

    def puede_usar(self, api: str, cantidad: int = 1) -> bool:
        """Retorna True si hay crédito suficiente para usar la API."""
        config = API_CONFIG.get(api, {})

        if config.get("limite_diario"):
            usado = self.uso_hoy(api)
            return (usado + cantidad) <= config["limite_diario"]

        if config.get("limite_mensual"):
            usado = self.uso_mes(api)
            return (usado + cantidad) <= config["limite_mensual"]

        return True  # sin límite configurado


# ──────────────────────────────────────────────
# HELPERS DE DISPLAY
# ──────────────────────────────────────────────

def _barra_progreso(pct: int, largo: int = 10) -> str:
    llenos = round(pct / 100 * largo)
    vacios = largo - llenos
    return f"[{'█' * llenos}{'░' * vacios}]"


def _semaforo_uso(pct: int) -> str:
    if pct >= 90: return "🔴"
    if pct >= 70: return "🟡"
    return "🟢"


# ──────────────────────────────────────────────
# CLI — ver historial desde terminal
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ver historial de uso de APIs")
    parser.add_argument("--env", default="test", choices=["test", "prd"])
    parser.add_argument("--reset", action="store_true", help="Borrar historial")
    args = parser.parse_args()

    tracker = APITracker(env=args.env)

    if args.reset:
        os.remove(tracker.filepath)
        print(f"✅ Historial borrado: {tracker.filepath}")
    else:
        # Simular sesión con datos del día para mostrar resumen
        tracker.session = tracker.data.get(tracker.hoy, {})
        if tracker.session:
            tracker.resumen("Resumen del día")
        else:
            print(f"📊 Sin uso registrado hoy ({tracker.hoy}) en environment '{args.env}'")
            print(f"   Archivo: {tracker.filepath}")