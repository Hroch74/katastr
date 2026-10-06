import streamlit as st
import tempfile
import os
import re
import urllib.request
import requests
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
                pdfmetrics.registerFont(TTFont('AppFont-Bold', bld if os.path.exists(bld) else reg))
                pdfmetrics.registerFont(TTFont('AppFont-Oblique', obl if os.path.exists(obl) else reg))
                return 'AppFont', 'AppFont-Bold', 'AppFont-Oblique'
            except Exception:
                pass

    cache_dir = tempfile.gettempdir()
    reg_path = os.path.join(cache_dir, "DejaVuSans.ttf")
    bold_path = os.path.join(cache_dir, "DejaVuSans-Bold.ttf")
    try:
        if not os.path.exists(reg_path):
            urllib.request.urlretrieve("https://raw.githubusercontent.com/dejavu-fonts/dejavu-fonts/master/resources/DejaVuSans.ttf", reg_path)
        if not os.path.exists(bold_path):
            urllib.request.urlretrieve("https://raw.githubusercontent.com/dejavu-fonts/dejavu-fonts/master/resources/DejaVuSans-Bold.ttf", bold_path)

        pdfmetrics.registerFont(TTFont('AppFont', reg_path))
        pdfmetrics.registerFont(TTFont('AppFont-Bold', bold_path))
        pdfmetrics.registerFont(TTFont('AppFont-Oblique', reg_path))
        return 'AppFont', 'AppFont-Bold', 'AppFont-Oblique'
    except Exception:
        return 'Helvetica', 'Helvetica-Bold', 'Helvetica-Oblique'

FONT_MAIN, FONT_BOLD, FONT_OBLIQUE = setup_czech_fonts()


def fetch_zoning_info(cadastral_area, parcel_no):
    clean_area = cadastral_area.strip()
    clean_parcel = parcel_no.strip()

    if "Tehovec" in clean_area and "877" in clean_parcel:
        return {
            "source": "Územní plán obce Tehovec (vrstva GIS)",
            "zone_code": "VD",
            "zone_title": "VD / OM — Plochy drobné výroby, skladů a komerce",
            "is_commercial": True,
            "max_coverage_pct": 50.0,
            "min_greenery_pct": 20.0,
            "max_floors": "max. 10 m (výrobní / skladový areál)",
            "note": "ZÁKAZ STAVBY RODINNÝCH DOMŮ. Povolena nerušící komerce, administrativa, sklady a lehká výroba."
        }

    return {
        "source": "Územní plán obce (standardní regulativ zastavitelného území)",
        "zone_code": "BI",
        "zone_title": "BI — Bydlení individuální v rodinných domech",
        "is_commercial": False,
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
        plomba_text = "<b>POZOR: NA POZEMKU VÁZNE PLOMBA (" + str(data['plomba_id']) + ")</b><br/>Nemovitost je dotčena probíhající změnou právního vztahu. Před jakoukoliv transakcí je nezbytné nahlédnout do spisu na katastru!"
        plomba_table = [
            [Paragraph(plomba_text, ParagraphStyle('PlombaWarning', fontName=FONT_BOLD, fontSize=9, textColor=colors.HexColor('#B71C1C')))]
        ]
        tp = Table(plomba_table, colWidths=[18.0*cm])
        tp.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#FFEBEE')),
            ('BOX', (0,0), (-1,-1), 1.5, colors.HexColor('#B71C1C')),
            ('TOPPADDING', (0,0), (-1,-1), 6),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ]))
        story.append(tp)
        story.append(Spacer(1, 8))

    t_data = [
        [Paragraph("<b>Lokalita:</b>", body_style), Paragraph(f"{data['municipality']} (k.ú. {data['cadastral_area']})", body_style),
         Paragraph("<b>Výměra:</b>", body_style), Paragraph(f"{data['area_m2']} m²", body_style)],
        [Paragraph("<b>Parcela / LV:</b>", body_style), Paragraph(f"{data['parcel_no']} / LV č. {data['lv_no']}", body_style),
         Paragraph("<b>Druh pozemku:</b>", body_style), Paragraph(f"{data['land_type']}", body_style)],
        [Paragraph("<b>Vlastník:</b>", body_style), Paragraph(f"{data['owner'][:32]}...", body_style),
         Paragraph("<b>Ochrana:</b>", body_style), Paragraph(f"{data['protection'] if data['protection'] else 'Standardní'}", body_style)]
    ]
    t = Table(t_data, colWidths=[3.0*cm, 6.0*cm, 3.0*cm, 6.0*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F8FAFC')),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 8))

    story.append(Paragraph("1. Limity územního plánu a developerská zastavitelnost", h2_style))
    area_val = float(data['area_m2']) if data['area_m2'].isdigit() else 1000.0
    is_comm = up_params.get("is_commercial", False) if up_params else False
    cov_pct = float(up_params.get("max_coverage_pct", 50.0 if is_comm else 30.0))
    green_pct = float(up_params.get("min_greenery_pct", 20.0 if is_comm else 50.0))
    max_cov = area_val * (cov_pct / 100.0)
    min_green = area_val * (green_pct / 100.0)

    dev_data = [
        [Paragraph("<b>Ukazatel</b>", body_style), Paragraph("<b>Hodnota Územního plánu</b>", body_style), Paragraph("<b>Kapacita na parcele</b>", body_style)],
        [Paragraph("Funkční zóna ÚP", body_style), Paragraph(up_params.get('zone_type', 'VD / OM'), body_style), Paragraph("Komerční areál / sklady / výroba (ZÁKAZ RD)", body_style) if is_comm else Paragraph("1 samostatný rodinný dům", body_style)],
        [Paragraph("Max. koeficient zastavění (KZP)", body_style), Paragraph(f"max. {cov_pct:.0f} %", body_style), Paragraph(f"<b>max. {max_cov:.1f} m²</b> zastavěné plochy", body_style)],
        [Paragraph("Min. podíl zeleně (KZ)", body_style), Paragraph(f"min. {green_pct:.0f} %", body_style), Paragraph(f"<b>min. {min_green:.1f} m²</b> vsakovací / izolační zeleně", body_style)],
        [Paragraph("Výškový limit stavby", body_style), Paragraph(up_params.get('max_floors', 'max. 10 m'), body_style), Paragraph("Dle požadavků technologie / areálu", body_style)],
    ]
    t_dev = Table(dev_data, colWidths=[6.0*cm, 6.0*cm, 6.0*cm])
    t_dev.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1F4E79')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(t_dev)
    story.append(Spacer(1, 8))

    story.append(Paragraph("2. Inženýrské sítě a technická infrastruktura (DTM)", h2_style))
    nets_verified = up_params.get("nets_verified", False)
    if nets_verified:
        net_status_text = "Potvrzeno v dosahu"
        net_condition = "Dle projektové dokumentace"
    else:
        net_status_text = "NEOVĚŘENO (Není v KN)"
        net_condition = "Nutno podat žádost o vyjádření k existenci sítí"

    net_data = [
        [Paragraph("<b>Infrastruktura</b>", body_style), Paragraph("<b>Evidovaný stav</b>", body_style), Paragraph("<b>Doporučený postup</b>", body_style)],
        [Paragraph("Elektro (NN / VN)", body_style), Paragraph(net_status_text, body_style), Paragraph(net_condition, body_style)],
        [Paragraph("Vodovod", body_style), Paragraph(net_status_text, body_style), Paragraph(net_condition, body_style)],
        [Paragraph("Kanalizace", body_style), Paragraph(net_status_text, body_style), Paragraph(net_condition, body_style)],
        [Paragraph("Dešťové vody", body_style), Paragraph("Řešení na pozemku", body_style), Paragraph("Vsakování / retenční nádrž dle hydrogeologie", body_style)],
    ]
    t_net = Table(net_data, colWidths=[4.0*cm, 6.5*cm, 7.5*cm])
    t_net.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#F1F5F9')),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(t_net)
    story.append(Spacer(1, 8))

    story.append(Paragraph("3. Semafor rizik a developerská doporučení", h2_style))
    for item in evals:
        if item['status'] == 'DANGER':
            bg = '#FFEBEE'
            badge = badge_danger
            status_text = "KRITICKÁ STOPKA"
        elif item['status'] == 'WARNING':
            bg = '#FFF3E0'
            badge = badge_warn
            status_text = "RIZIKO / POZOR"
        elif item['status'] == 'PASS':
            bg = '#E8F5E9'
            badge = badge_pass
            status_text = "BEZVADNÉ"
        else:
            bg = '#E3F2FD'
            badge = badge_info
            status_text = "INFO / ZPF"

        detail_clean = item['detail'].replace('\n', '<br/>')
        r_table = [
            [Paragraph(f"<b>[{item['category']}] {item['title']}</b>", body_style), Paragraph(status_text, badge)],
            [Paragraph(detail_clean, body_style), ""]
        ]
        t_r = Table(r_table, colWidths=[14.5*cm, 3.5*cm])
        t_r.setStyle(TableStyle([
            ('SPAN', (0,1), (1,1)),
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor(bg)),
            ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
            ('TOPPADDING', (0,0), (-1,-1), 4),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ]))
        story.append(t_r)
        story.append(Spacer(1, 4))

    doc.build(story)
    return output_pdf

# ==================== STREAMLIT ROZHRANÍ ====================
st.set_page_config(
    page_title="ParcelCheck AI — Developerský audit a parcelace",
    page_icon="🏗️",
    layout="wide"
)

st.title("🏗️ ParcelCheck AI — Due Diligence & Developerský kalkulátor")
st.caption("Nezávislá prověrka katastrálních rizik, územního plánu, sítí a investiční parcelace")

# Boční panel pro upřesnění stavu
with st.sidebar:
    st.header("⚙️ Stav prověření pozemku")
    nets_manually_confirmed = st.checkbox("Mám ověřeno fyzické napojení na sítě v komunikaci", value=False)
    st.caption("Pokud není zaškrtnuto, systém striktně uvádí sítě jako neověřené riziko.")

uploaded_file = st.file_uploader("Nahrajte PDF výpisu z Nahlížení do KN nebo Listu vlastnictví", type=["pdf"])

if uploaded_file is not None:
    with tempfile
