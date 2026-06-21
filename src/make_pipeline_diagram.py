"""
Genera un diagrama PNG del pipeline del scraper E-14: navegacion -> descarga
-> subida a Azure. Uso interno/documentacion, no es parte del scraper.

Ejecutar: python src/make_pipeline_diagram.py
"""
import os
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "pipeline_diagram.png")

fig, ax = plt.subplots(figsize=(11, 14))
ax.set_xlim(0, 10)
ax.set_ylim(0, 26)
ax.axis("off")

COLOR_SITE = "#0f4ee6"
COLOR_PROC = "#122459"
COLOR_AZURE = "#0078D4"
COLOR_TEXT_WHITE = "white"


def box(x, y, w, h, text, color, fontsize=10.5, text_color="white"):
    rect = FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.12,rounding_size=0.15",
        linewidth=0, facecolor=color,
    )
    ax.add_patch(rect)
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
             fontsize=fontsize, color=text_color, wrap=True, linespacing=1.4)


def arrow(x1, y1, x2, y2, label=None):
    arr = FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=18,
                           linewidth=1.6, color="#444444")
    ax.add_patch(arr)
    if label:
        ax.text((x1 + x2) / 2 + 0.15, (y1 + y2) / 2, label, fontsize=8.5,
                 color="#444444", va="center")


# Titulo
ax.text(5, 25.3, "Pipeline E-14 — Registraduria Nacional", ha="center",
        fontsize=15, fontweight="bold", color=COLOR_PROC)
ax.text(5, 24.75, "divulgacione14presidente.registraduria.gov.co", ha="center",
        fontsize=9, color="#666666")

y = 22.6
box(1, y, 8, 1.3,
    "1. Portal oficial (Angular SPA)\nVisor Ciudadano — requiere navegador real (Akamai bloquea HTTP simple)",
    COLOR_SITE)
arrow(5, y, 5, y - 0.6)

y -= 2.0
box(1, y, 8, 1.5,
    "2. Playwright automatiza la navegacion\nDepartamento -> Municipio -> Zona -> Puesto -> Consultar -> 'Ver' Mesa N",
    COLOR_PROC)
arrow(5, y, 5, y - 0.6)

y -= 2.0
box(1, y, 8, 1.5,
    "3. Intercepta la respuesta HTTP real del PDF (antes del blob)\nGET /assets/temis/pdf/{depto}/{municipio}/{zona}/{puesto}/{mesa}/{corp}/{hash}.pdf",
    COLOR_PROC, fontsize=9.5)
arrow(5, y, 5, y - 0.6)

y -= 2.0
box(1, y, 8, 1.5,
    "4. Guarda el PDF localmente\ndata/downloads/{depto}_{municipio}_{zona}_{puesto}_{mesa}.pdf",
    COLOR_PROC, fontsize=9.5)
arrow(5, y, 5, y - 0.6)

y -= 2.0
box(1, y, 8, 1.6,
    "5. Sube a Azure Blob Storage\nStorage account: actibox  |  Container: tecnologia-activos-x-cambio",
    COLOR_AZURE)
arrow(5, y, 5, y - 0.6)

y -= 1.9
box(0.6, y, 8.8, 1.6,
    "Ruta del blob:\nactibox.blob.core.windows.net/tecnologia-activos-x-cambio/\ne14/{depto}/{municipio}/{zona}/{puesto}/{archivo}.pdf",
    "#e8f0fe", fontsize=9.5, text_color="#0f3d8c")
arrow(5, y, 5, y - 0.6)

y -= 2.0
box(1, y, 8, 1.3,
    "6. (Pendiente) Plataforma web propia\nlee el blob y lo muestra en tiempo real",
    "#999999", fontsize=10)

# Checkpoint lateral
ax.text(9.5, 14.3, "checkpoint_{depto}.json\n(permite reanudar si\nse interrumpe)",
        fontsize=7.5, color="#888888", ha="left", va="center", style="italic")
arrow(9.0, 18.6, 9.0, 14.6)

plt.tight_layout()
plt.savefig(OUT_PATH, dpi=160, bbox_inches="tight")
print(f"Guardado: {OUT_PATH}")
