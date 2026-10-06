import streamlit as st
import tempfile
import os
import re
import urllib.request
import pypdf
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# --- Zajištění fontu s plnou podporou české diakritiky (Windows i Linux Cloud) ---
def setup_czech_fonts():
    font_main = 'Helvetica'
    font_bold = 'Helvetica-Bold'
    font_oblique = 'Helvetica-Oblique'

    # 1. Zkouška systémových písem Windows a Linux
    candidates = [
        ('C:\\Windows\\Fonts\\arial.ttf', 'C:\\Windows\\Fonts\\arialbd.ttf', 'C:\\Windows\\Fonts\\ariali.ttf'),
        ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf'),
        ('/usr/share/fonts/TTF/DejaVuSans.ttf', '/usr/share/fonts/TTF/DejaVuSans-Bold.ttf', '/usr/share/fonts/TTF/DejaVuSans-Oblique.ttf')
    ]
    for regular, bold, oblique in candidates:
        if os.path.exists(regular):
            try:
                pdfmetrics.registerFont(TTFont('AppFont', regular))
                pdfmetrics.registerFont(TTFont('AppFont-Bold', bold if os.path.exists(bold) else regular))
                pdfmetrics.registerFont(TTFont('AppFont-Oblique', oblique if os.path.exists(oblique) else regular))
                return 'AppFont', 'AppFont-Bold', 'AppFont-Oblique'
            except Exception:
                pass

    # 2. Automatické stažení DejaVu Sans na cloudu, pokud systémový font chybí
    cache_dir = tempfile.gettempdir()
    reg_path = os.path.join(cache_dir, "DejaVuSans.ttf")
    bold_path = os.path.join(cache_dir, "DejaVuSans-Bold.ttf")
    try:
        if not os.path.exists(reg_path):
            urllib.request.urlretrieve("https://github.com/dejavu-fonts/dejavu-fonts/raw/master/resources/DejaVuSans.ttf", reg_path)
        if not os.path.exists(bold_path):
            urllib.request.urlretrieve("https://github.com/dejavu-fonts/dejavu-fonts/raw/master/resources/DejaVuSans-Bold.ttf", bold_path)

        pdfmetrics.registerFont(TTFont('AppFont', reg_path))
        pdfmetrics.registerFont(TTFont('AppFont-Bold', bold_path))
        pdfmetrics.registerFont(TTFont('AppFont-Oblique', reg_path))
        return 'AppFont', 'AppFont-Bold', 'AppFont-Oblique'
    except Exception:
        return font_main, font_bold, font_oblique

FONT_MAIN, FONT_BOLD, FONT_OBLIQUE = setup_czech_fonts()


class ParcelCheckAnalyzer:
    def __init__(self, raw_text):
        self.raw = raw_text
        self.data = self._parse()

    def _parse(self):
        plomba_match = re.search(r"Objekt je dotčen změnou právního vztahu:\s*([^;\n\r]+)", self.raw)
        has_plomba = bool(plomba_match)
        plomba_id = plomba_match.group(1).strip() if plomba_match else ""

        data = {
            "parcel_no": self._extract(r"Parcelní číslo:\s*([0-9]+(?:/[0-9]+)?)"),
            "municipality": self._extract(r"Obec:\s*([^\n\r\[]+)"),
            "cadastral_area": self._extract(r"Katastrální území:\s*([^\n\r\[]+)"),
            "lv_no": self._extract(r"Číslo LV:\s*([0-9]+)"),
            "area_m2": self._extract(r"Výměra \[m2\]:\s*([0-9\s]+)"),
            "land_type": self._extract(r"Druh pozemku:\s*([^\n\r]+)"),
            "owner": self._extract(r"Vlastnické právo\s*(?:Podíl)?\s*\n\s*([^\n\r]+)"),
            "protection": self._extract(r"Způsob ochrany nemovitosti\s*Název\s*\n\s*([^\n\r]+)"),
            "limitations": "Nejsou evidována žádná omezení" in self.raw,
            "has_building": "Součástí je stavba" in self.raw,
            "bpej": self._extract(r"BPEJ\s*Výměra\s*\n\s*([0-9]+)"),
            "mortgage": "Zástavní právo" in self.raw,
            "easement": "Věcné břemeno" in self.raw or "Služebnost" in self.raw,
            "bans": "Závazek neumožnit" in self.raw or "Závazek nezajistit" in self.raw,
            "has_plomba": has_plomba,
            "plomba_id": plomba_id
        }
        if data["area_m2"]:
            data["area_m2"] = data["area_m2"].replace(" ", "").strip()
        return data

    def _extract(self, pattern):
        m = re.search(pattern, self.raw)
        return m.group(1).strip() if m else ""

    def evaluate_developer_rules(self, up_params=None):
        p = self.data
        area = float(p["area_m2"]) if p["area_m2"].isdigit() else 1000.0

        is_commercial = up_params.get("is_commercial", False) if up_params else False
        coverage_pct = float(up_params.get("max_coverage_pct", 50.0 if is_commercial else 30.0))
        greenery_pct = float(up_params.get("min_greenery_pct", 20.0 if is_commercial else 50.0))
        max_footprint = area * (coverage_pct / 100.0)
        min_green_m2 = area * (greenery_pct / 100.0)

        checks = []

        if p.get("has_plomba", False):
            checks.append({
                "category": "PLOMBA / PROBÍHAJÍCÍ ŘÍZENÍ (STOPKA)",
                "status": "DANGER",
                "title": f"Objekt je dotčen změnou právního vztahu: {p['plomba_id']}",
                "detail": f"Na listu vlastnictví probíhá aktivní vkladové řízení ({p['plomba_id']}). Může jít o převod vlastnictví, exekuční příkaz nebo zástavní právo. ZÁKAZ PODPISU A PLATBY: Nutno okamžitě nahlédnout do spisu na katastru!"
            })
        else:
            checks.append({
                "category": "Probíhající řízení",
                "status": "PASS",
                "title": "Nemovitost není dotčena žádnou změnou (bez plomby)",
                "detail": "K nemovitosti neběží žádné zaplombované vkladové ani záznamové řízení."
            })

        if is_commercial:
            checks.append({
                "category": "Územní plán & Funkční zóna",
                "status": "WARNING",
                "title": f"Komerční / Výrobní zóna: {up_params.get('zone_type', 'VD / OM')}",
                "detail": f"Pozemek je určen pro komerční využití (sklady, lehká výroba, služby, administrativa). VÝSTAVBA BĚŽNÝCH RODINNÝCH DOMŮ JE V TÉTO ZÓNĚ PŘÍSNĚ ZAKÁZÁNA. Max. zastavěnost {coverage_pct:.0f} % = {max_footprint:.1f} m² areálu."
            })
        else:
            checks.append({
                "category": "Územní plán & Funkční zóna",
                "status": "PASS",
                "title": f"Obytná zóna: {up_params.get('zone_type', 'BI - Bydlení individuální')}",
                "detail": f"Přípustná výstavba rodinných domů. Max. zastavěnost {coverage_pct:.0f} % = {max_footprint:.1f} m² desky. Min. zeleň {greenery_pct:.0f} % = {min_green_m2:.1f} m²."
            })

        if p["mortgage"]:
            checks.append({
                "category": "Zástavní práva & Dluhy",
                "status": "WARNING",
                "title": "Na pozemku vázne zástavní právo smluvní",
                "detail": "Nutno v kupní smlouvě podmínit výplatu kupní ceny kvitancí věřitele a výmazem zástavy."
            })
        elif p["limitations"]:
            checks.append({
                "category": "Právní stav",
                "status": "PASS",
                "title": "V části C nejsou evidována žádná omezení vlastnického práva",
                "detail": "Pozemek je bez zapsaných zástav, exekucí či věcných břemen."
            })

        if "zemědělský" in p["protection"].lower() or p["land_type"] in ["trvalý travní porost", "orná půda", "zahrada"]:
            bpej_str = p['bpej'] if p['bpej'] else "Dle bonity"
            checks.append({
                "category": "Zemědělský půdní fond (ZPF)",
                "status": "INFO",
                "title": f"Druh: {p['land_type']} — nutné odnětí ze ZPF (BPEJ: {bpej_str})",
                "detail": f"Celá plocha {area:.0f} m² je v ZPF. Pro výstavbu je nutné vyjmout zastavěnou a zpevněnou plochu (cca {max_footprint:.0f} m²)."
            })

        checks.append({
            "category": "Sítě a dopravní infrastruktura",
            "status": "PASS",
            "title": "Dopravní napojení a technické sítě",
            "detail": f"Dopravní napojení: {up_params.get('road', 'Sjezd z přilehlé komunikace')}\nSítě: {up_params.get('infrastructure', 'Elektro, voda, dešťová retence')}."
        })

        return checks

def generate_pdf_report(analyzer, output_pdf, up_params=None):
    data = analyzer.data
    evals = analyzer.evaluate_developer_rules(up_params)

    doc = SimpleDocTemplate(
        output_pdf,
        pagesize=A4,
        rightMargin=1.5*cm, leftMargin=1.5*cm, topMargin=1.5*cm, bottomMargin=1.5*cm
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle('DocTitle', parent=styles['Heading1'], fontName=FONT_BOLD, fontSize=15, leading=19, textColor=colors.HexColor('#1F4E79'), spaceAfter=3)
    subtitle_style = ParagraphStyle('DocSubtitle', parent=styles['Normal'], fontName=FONT_OBLIQUE, fontSize=9, leading=13, textColor=colors.HexColor('#555555'), spaceAfter=10)
    h2_style = ParagraphStyle('H2', parent=styles['Heading2'], fontName=FONT_BOLD, fontSize=11, leading=15, textColor=colors.HexColor('#1F4E79'), spaceBefore=8, spaceAfter=5)
    body_style = ParagraphStyle('Body', parent=styles['Normal'], fontName=FONT_MAIN, fontSize=8.5, leading=12, textColor=colors.HexColor('#222222'))

    badge_pass = ParagraphStyle('Pass', fontName=FONT_BOLD, fontSize=7.5, textColor=colors.HexColor('#1B5E20'), alignment=1)
    badge_warn = ParagraphStyle('Warn', fontName=FONT_BOLD, fontSize=7.5, textColor=colors.HexColor('#E65100'), alignment=1)
    badge_danger = ParagraphStyle('Danger', fontName=FONT_BOLD, fontSize=7.5, textColor=colors.HexColor('#B71C1C'), alignment=1)
    badge_info = ParagraphStyle('Info', fontName=FONT_BOLD, fontSize=7.5, textColor=colors.HexColor('#0D47A1'), alignment=1)

    story = []
    story.append(Paragraph("PARCELCHECK AI — DEVELOPERSKÝ AUDIT POZEMKU", title_style))
    story.append(Paragraph(f"Automatická prověrka parcely č. {data['parcel_no']} | k.ú. {data['cadastral_area']} (obec {data['municipality']}) | LV {data['lv_no']}", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#1F4E79'), spaceBefore=2, spaceAfter=8))

    if data.get("has_plomba", False):
        plomba_table = [
            [Paragraph(f"<b>POZOR: NA POZEMKU VÁZNE PLOMBA ({data['plomba_id']})</b><br/>Nemovitost je dotčena probíhající změnou právního
