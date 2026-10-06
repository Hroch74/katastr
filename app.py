import streamlit as st
import tempfile
import os
import re
import pypdf

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    HRFlowable
)
from reportlab.lib.styles import (
    getSampleStyleSheet,
    ParagraphStyle
)
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# --- Písma pro PDF s českou diakritikou ---
def setup_czech_fonts():
    f_reg = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    f_bld = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
    if os.path.exists(f_reg):
        try:
            pdfmetrics.registerFont(TTFont('AppFont', f_reg))
            b_use = f_bld if os.path.exists(f_bld) else f_reg
            pdfmetrics.registerFont(TTFont('AppFont-Bold', b_use))
            return 'AppFont', 'AppFont-Bold'
        except Exception:
            pass
    return 'Helvetica', 'Helvetica-Bold'

FONT_MAIN, FONT_BOLD = setup_czech_fonts()

# --- Cenová mapa okresu ---
def get_benchmark_prices(municipality, cadastral_area):
    m = str(municipality).lower()
    c = str(cadastral_area).lower()
    if any(k in m or k in c for k in [
        "tehovec", "říčany", "ricany", "mukařov", "babice"
    ]):
        return {
            "region": "Praha-východ",
            "raw_avg": 3500,
            "serviced_avg": 9500,
            "comm_avg": 4800,
            "raw_range": "2 500 - 4 500 Kč",
            "serviced_range": "7 500 - 12 500 Kč"
        }
    return {
        "region": "Regionální průměr ČR",
        "raw_avg": 1800,
        "serviced_avg": 5000,
        "comm_avg": 3000,
        "raw_range": "1 000 - 2 800 Kč",
        "serviced_range": "3 500 - 7 000 Kč"
    }

# --- Územní plán ---
def fetch_zoning_info(cadastral_area, parcel_no):
    c = str(cadastral_area).strip()
    p = str(parcel_no).strip()
    if "Tehovec" in c and ("877" in p or "850" in p):
        return {
            "title": "BI / Z8 — Zastavitelná plocha Z8",
            "is_commercial": False,
            "requires_contract": True,
            "cov_pct": 30.0,
            "grn_pct": 50.0,
            "note": "Podmíněno PLÁNOVACÍ SMLOUVOU s obcí!"
        }
    return {
        "title": "BI — Bydlení v rodinných domech",
        "is_commercial": False,
        "requires_contract": False,
        "cov_pct": 30.0,
        "grn_pct": 50.0,
        "note": "Přípustná stavba rodinného domu."
    }

class ParcelCheckAnalyzer:
    def __init__(self, raw_text):
        self.raw = raw_text
        self.data = self._parse()

    def _parse(self):
        pm = re.search(
            r"Objekt je dotčen změnou právního vztahu:\s*([^;\n\r]+)",
            self.raw
        )
        data = {
            "parcel_no": self._ex(
                r"Parcelní číslo:\s*([0-9]+(?:/[0-9]+)?)"
            ),
            "municipality": self._ex(
                r"Obec:\s*([^\n\r\[]+)"
            ),
            "cadastral_area": self._ex(
                r"Katastrální území:\s*([^\n\r\[]+)"
            ),
            "lv_no": self._ex(
                r"Číslo LV:\s*([0-9]+)"
            ),
            "area_m2": self._ex(
                r"Výměra \[m2\]:\s*([0-9\s]+)"
            ).replace(" ", ""),
            "land_type": self._ex(
                r"Druh pozemku:\s*([^\n\r]+)"
            ),
            "owner": self._ex(
                r"Vlastnické právo\s*(?:Podíl)?\s*\n\s*([^\n\r]+)"
            ),
            "protection": self._ex(
                r"Způsob ochrany nemovitosti\s*Název\s*\n\s*([^\n\r]+)"
            ),
            "limitations": "Nejsou evidována žádná omezení" in self.raw,
            "bpej": self._ex(
                r"BPEJ\s*Výměra\s*\n\s*([0-9]+)"
            ),
            "mortgage": "Zástavní právo" in self.raw,
            "has_plomba": bool(pm),
            "plomba_id": pm.group(1).strip() if pm else ""
        }
        return data

    def _ex(self, pattern):
        m = re.search(pattern, self.raw)
        return m.group(1).strip() if m else ""

    def evaluate_rules(self, up):
        p = self.data
        try:
            area = float(p["area_m2"])
        except Exception:
            area = 1000.0

        cov = up["cov_pct"]
        max_foot = area * (cov / 100.0)
        checks = []

        if p["has_plomba"]:
            checks.append({
                "cat": "PLOMBA (STOPKA)",
                "stat": "DANGER",
                "title": f"Plomba: {p['plomba_id']}",
                "detail": "Běží vklad na KN. ZÁKAZ PLATBY!"
            })
        else:
            checks.append({
                "cat": "Řízení na KN",
                "stat": "PASS",
                "title": "Bez plomby",
                "detail": "K nemovitosti neběží žádné řízení."
            })

        if up["is_commercial"]:
            checks.append({
                "cat": "Územní plán",
                "stat": "WARNING",
                "title": f"Komerční zóna: {up['title']}",
                "detail": f"ZÁKAZ RD. Zastavěnost {cov:.0f} % = {max_foot:.0f} m²."
            })
        else:
            checks.append({
                "cat": "Územní plán",
                "stat": "PASS",
                "title": f"Obytná zóna: {up['title']}",
                "detail": f"Přípustný RD. Zastavěnost {cov:.0f} % = {max_foot:.0f} m²."
            })

        if up.get("requires_contract", False):
            checks.append({
                "cat": "Podmínka rozvoje",
                "stat": "WARNING",
                "title": "Vyžadována Plánovací smlouva s obcí",
                "detail": "Povolení stavby vyžaduje schválení smlouvy obcí."
            })

        if p["mortgage"]:
            checks.append({
                "cat": "Zástavní práva",
                "stat": "WARNING",
                "title": "Na pozemku vázne zástavní právo",
                "detail": "Podmínit výplatu kvitancí a výmazem."
            })
        elif p["limitations"]:
            checks.append({
                "cat": "Právní stav",
                "stat": "PASS",
                "title": "V části C nejsou evidována omezení",
                "detail": "Bez zástav a věcných břemen."
            })

        if not up.get("nets_verified", False):
            checks.append({
                "cat": "Inženýrské sítě",
                "stat": "WARNING",
                "title": "Sítě nejsou na pozemku ověřeny",
                "detail": "V KN sítě nejsou. Podat žádost správcům."
            })
        else:
            checks.append({
                "cat": "Inženýrské sítě",
                "stat": "PASS",
                "title": "Sítě potvrzeny v dosahu",
                "detail": "Dle technické dokumentace záměru."
            })

        return checks


def generate_pdf(analyzer, out_pdf, up, prices, parcel_table=None):
    d = analyzer.data
    evals = analyzer.evaluate_rules(up)
    doc = SimpleDocTemplate(
        out_pdf,
        pagesize=A4,
        rightMargin=1.5*cm,
        leftMargin=1.5*cm,
        topMargin=1.5*cm,
        bottomMargin=1.5*cm
    )
    styles = getSampleStyleSheet()

    c_blue = colors.HexColor('#1F4E79')
    c_red = colors.HexColor('#B71C1C')
    t_s = ParagraphStyle(
        'T',
        fontName=FONT_BOLD,
        fontSize=13,
        leading=17,
        textColor=c_blue
    )
    b_s = ParagraphStyle(
        'B',
        fontName=FONT_MAIN,
        fontSize=8,
        leading=11
    )
    bp = ParagraphStyle(
        'BP',
        fontName=FONT_BOLD,
        fontSize=7,
        textColor=colors.HexColor('#1B5E20'),
        alignment=1
    )
    bw = ParagraphStyle(
        'BW',
        fontName=FONT_BOLD,
        fontSize=7,
        textColor=colors.HexColor('#E65100'),
        alignment=1
    )
    bd = ParagraphStyle(
        'BD',
        fontName=FONT_BOLD,
        fontSize=7,
        textColor=c_red,
        alignment=1
    )

    story = []
    story.append(
        Paragraph("PARCELCHECK AI — AUDIT & PARCELACE", t_s)
    )
    story.append(
        Paragraph(
            f"Parcela {d['parcel_no']} | k.ú. {d['cadastral_area']} | LV {d['lv_no']}",
            b_s
        )
    )
    story.append(
        HRFlowable(
            width="100%",
            thickness=1.5,
            color=c_blue,
            spaceBefore=2,
            spaceAfter=6
        )
    )

    if d
