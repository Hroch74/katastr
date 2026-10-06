import streamlit as st
import tempfile
import os
import re
import pypdf
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# --- Zajištění fontu s plnou podporou české diakritiky ---
def setup_czech_fonts():
    system_paths = [
        ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
         '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
         '/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf'),
        ('C:\\Windows\\Fonts\\arial.ttf',
         'C:\\Windows\\Fonts\\arialbd.ttf',
         'C:\\Windows\\Fonts\\ariali.ttf')
    ]
    for reg, bld, obl in system_paths:
        if os.path.exists(reg):
            try:
                pdfmetrics.registerFont(TTFont('AppFont', reg))
                b_font = bld if os.path.exists(bld) else reg
                pdfmetrics.registerFont(TTFont('AppFont-Bold', b_font))
                o_font = obl if os.path.exists(obl) else reg
                pdfmetrics.registerFont(TTFont('AppFont-Oblique', o_font))
                return 'AppFont', 'AppFont-Bold', 'AppFont-Oblique'
            except Exception:
                pass
    return 'Helvetica', 'Helvetica-Bold', 'Helvetica-Oblique'

FONT_MAIN, FONT_BOLD, FONT_OBLIQUE = setup_czech_fonts()


# --- Modul pro orientační cenovou mapu dle lokality ---
def get_benchmark_prices(municipality, cadastral_area):
    m_clean = municipality.lower()
    c_clean = cadastral_area.lower()

    # Oblast Praha-východ (Tehovec, Říčany, Mukařov, Úvaly apod.)
    if any(k in m_clean or k in c_clean for k in ["tehovec", "říčany", "ricany", "mukařov", "mukarov", "babice"]):
        return {
            "region": "Praha-východ (příměstský koridor)",
            "raw_min": 2500, "raw_avg": 3500, "raw_max": 4500,
            "serviced_min": 7500, "serviced_avg": 9500, "serviced_max": 12500,
            "commercial_min": 3500, "commercial_avg": 4800, "commercial_max": 6500,
            "confidence": "Vysoká (aktivní příměstský trh)"
        }
    # Praha celá
    elif "praha" in m_clean or "praha" in c_clean:
        return {
            "region": "Hlavní město Praha",
            "raw_min": 5000, "raw_avg": 8000, "raw_max": 12000,
            "serviced_min": 14000, "serviced_avg": 18000, "serviced_max": 25000,
            "commercial_min": 6000, "commercial_avg": 9000, "commercial_max": 14000,
            "confidence": "Vysoká"
        }
    # Výchozí krajský / okresní průměr ČR
    return {
        "region": "Regionální průměr ČR (okresní trh)",
        "raw_min": 1000, "raw_avg": 1800, "raw_max": 2800,
        "serviced_min": 3500, "serviced_avg": 5000, "serviced_max": 7000,
        "commercial_min": 2000, "commercial_avg": 3000, "commercial_max": 4200,
        "confidence": "Orientační benchmark (nutno ověřit místním šetřením)"
    }


def fetch_zoning_info(cadastral_area, parcel_no):
    clean_area = str(cadastral_area).strip()
    clean_parcel = str(parcel_no).strip()

    # Specifické lokality s přesně známou plochou (např. Tehovec)
    if "Tehovec" in clean_area and "877" in clean_parcel:
        return {
            "source": "Územní plán obce Tehovec (plocha Z8 / GIS)",
            "zone_code": "VD / OM (Z8)",
            "zone_title": "VD / OM — Plochy drobné výroby, skladů a komerce (lokalita Z8)",
            "is_commercial": True,
            "requires_planning_contract": True,
            "max_coverage_pct": 50.0,
            "min_greenery_pct": 20.0,
            "max_floors": "max. 10 m (výrobní / skladový areál)",
            "note": "ZÁKAZ STAVBY RD. Dle ÚP podmíněno uzavřením PLÁNOVACÍ SMLOUVY s obcí na dopravní a technickou infrastrukturu!"
        }

    return {
        "source": "Územní plán obce (standardní regulativ zastavitelného území)",
        "zone_code": "BI",
        "zone_title": "BI — Bydlení individuální v rodinných domech",
        "is_commercial": False,
        "requires_planning_contract": False,
        "max_coverage_pct": 30.0,
        "min_greenery_pct": 50.0,
        "max_floors": "1 NP + podkroví (max. 9 m)",
        "note": "Přípustná výstavba samostatného rodinného domu."
    }


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
        try:
            area = float(p["area_m2"])
        except Exception:
            area = 1000.0

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
                "detail": f"Na listu vlastnictví probíhá aktivní vkladové řízení ({p['plomba_id']}). Může jít o převod vlastnictví, exekuční příkaz nebo zástavní právo banky. ZÁKAZ PODPISU A PLATBY: Nutno okamžitě nahlédnout do spisu na katastru!"
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
                "title": f"Komerční zóna: {up_params.get('zone_type', 'VD / OM')}",
                "detail": f"Pozemek je dle územního plánu určen pro výrobu a komerci. VÝSTAVBA BĚŽNÝCH RODINNÝCH DOMŮ JE ZDE PŘÍSNĚ ZAKÁZÁNA. Max. zastavěnost areálu {coverage_pct:.0f} % = {max_footprint:.1f} m²."
            })
        else:
            checks.append({
                "category": "Územní plán & Funkční zóna",
                "status": "PASS",
                "title": f"Obytná zóna: {up_params.get('zone_type', 'BI - Bydlení individuální')}",
                "detail": f"Přípustná výstavba rodinných domů. Max. zastavěnost {coverage_pct:.0f} % = {max_footprint:.1f} m² desky. Min. zeleň {greenery_pct:.0f} % = {min_green_m2:.1f} m²."
            })

        req_contract = up_params.get("requires_planning_contract", False)
        if req_contract:
            checks.append({
                "category": "Podmínka rozvoje (Plánovací smlouva)",
                "status": "WARNING",
                "title": "Vyžadována Plánovací smlouva s obcí dle § 130 stavebního zákona",
                "detail": "Podmínkou pro povolení záměru v této ploše je schválení plánovací smlouvy zastupitelstvem obce. Smlouva upraví napojení na infrastrukturu, případné kontribuce obci a termíny výstavby."
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
                "detail": f"Celá plocha {area:.0f} m² je v ZPF. Pro stavbu je nutné vyjmout zastavěnou a zpevněnou plochu (cca {max_footprint:.0f} m²)."
            })

        nets_verified = up_params.get("nets_verified", False)
        if not nets_verified:
            checks.append({
                "category": "Inženýrské sítě (DTM)",
                "status": "WARNING",
                "title": "Inženýrské sítě nejsou na pozemku ověřeny (nejsou součástí KN)",
                "detail": "Katastr nemovitostí sítě neeviduje. Na pozemku není garantováno žádné napojení na vodu, kanalizaci ani elektro. RIZIKO: Nutno podat žádost o vyjádření k existenci sítí a prověřit kapacitu přípojek a náklady na zasíťování."
            })
        else:
            checks.append({
                "category": "Inženýrské sítě (DTM)",
                "status": "PASS",
                "title": "Sítě a dopravní napojení uživatelsky potvrzeny",
                "detail": f"Dopravní napojení: {up_params.get('road', 'Sjezd z přilehlé komunikace')}\nSítě: {up_params.get('infrastructure', 'Dle technické dokumentace')}."
            })

        return checks


def generate_pdf_report(analyzer, output_pdf, up_params=None, price_info=None):
    data = analyzer.data
    evals = analyzer.evaluate_developer_rules(up_params)

    doc = SimpleDocTemplate(
        output_pdf,
        pagesize=A4,
        rightMargin=1.5*cm,
        leftMargin=1.5*cm,
        topMargin=1.5*cm,
        bottomMargin=1.5*cm
    )

    styles = getSampleStyleSheet()

    c_blue = colors.HexColor('#1F4E79')
    c_gray = colors.HexColor('#555555')
    c_dark = colors.HexColor('#222222')
    c_green = colors.HexColor('#1B5E20')
    c_orange = colors.HexColor('#E65100')
    c_red = colors.HexColor('#B71C1C')
    c_navy = colors.HexColor('#0D47A1')

    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName=FONT_BOLD,
        fontSize=15,
        leading=19,
        textColor=c_blue,
        spaceAfter=3
    )
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName=FONT_OBLIQUE,
        fontSize=9,
        leading=13,
        textColor=c_gray,
        spaceAfter=10
    )
    h2_style = ParagraphStyle(
        'H2',
        parent=styles['Heading2'],
        fontName=FONT_BOLD,
        fontSize=11,
        leading=15,
        textColor=c_blue,
        spaceBefore=8,
        spaceAfter=5
    )
    body_style = ParagraphStyle(
        'Body',
        parent=styles['Normal'],
        fontName=FONT_MAIN,
        fontSize=8.5,
        leading=12,
        textColor=c_dark
    )

    badge_pass = ParagraphStyle('Pass', fontName=FONT_BOLD, fontSize=7.5, textColor=c_green, alignment=1)
    badge_warn = ParagraphStyle('Warn', fontName=FONT_BOLD, fontSize=7.5, textColor=c_orange, alignment=1)
    badge_danger = ParagraphStyle('Danger', fontName=FONT_BOLD, fontSize=7.5, textColor=c_red, alignment=1)
    badge_info = ParagraphStyle('Info', fontName=FONT_BOLD, fontSize=7.5, textColor=c_navy, alignment=1)

    story = []
    story.append(Paragraph("PARCELCHECK AI — DEVELOPERSKÝ AUDIT POZEMKU", title_style))
    story.append(Paragraph(f"Automatická prověrka parcely č. {data['parcel_no']} | k.ú. {data['cadastral_area']} (obec {data['municipality']}) | LV {data['lv_no']}", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=c_blue, spaceBefore=2, spaceAfter=8))

    if data.get("has_plomba", False):
        plomba_text = "<b>POZOR: NA POZEMKU VÁZNE PLOMBA (" + str(data['plomba_id']) + ")</b><br/>Nemovitost je dotčena probíhající změnou právního vztahu. Před jakoukoliv transakcí je nezbytné nahlédnout do spisu na katastru!"
        plomba_p_style = ParagraphStyle('PlombaWarning', fontName=FONT_BOLD, fontSize=9, textColor=c_red)
        plomba_table = [[Paragraph(plomba_text, plomba_p_style)]]
        tp = Table(plomba_table, colWidths
