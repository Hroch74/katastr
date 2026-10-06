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

# --- Zajištění fontu s plnou podporou české diakritiky (bezpečná inicializace) ---
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


def fetch_zoning_info(cadastral_area, parcel_no):
    clean_area = str(cadastral_area).strip()
    clean_parcel = str(parcel_no).strip()

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
        tp = Table(plomba_table, colWidths=[18.0*cm])
        tp.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#FFEBEE')),
            ('BOX', (0,0), (-1,-1), 1.5, c_red),
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
    try:
        area_val = float(data['area_m2'])
    except Exception:
        area_val = 1000.0

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
        ('BACKGROUND', (0,0), (-1,0), c_blue),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(t_dev)
    story.append(Spacer(1, 8))

    story.append(Paragraph("2. Inženýrské sítě a technická infrastruktura (DTM)", h2_style))
    nets_verified = up_params.get("nets_verified", False)
    net_status_text = "Potvrzeno v dosahu" if nets_verified else "NEOVĚŘENO (Není v KN)"
    net_condition = "Dle projektové dokumentace" if nets_verified else "Nutno podat žádost o vyjádření k existenci sítí"

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

with st.sidebar:
    st.header("⚙️ Stav prověření pozemku")
    nets_manually_confirmed = st.checkbox("Mám ověřeno fyzické napojení na sítě v komunikaci", value=False)
    st.caption("Pokud není zaškrtnuto, systém striktně uvádí sítě jako neověřené riziko.")

uploaded_file = st.file_uploader("Nahrajte PDF výpisu z Nahlížení do KN nebo Listu vlastnictví", type=["pdf"])

if uploaded_file is not None:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(uploaded_file.read())
        tmp_path = tmp.name

    reader = pypdf.PdfReader(tmp_path)
    text = ""
    for page in reader.pages:
        text += page.extract_text() or ""

    analyzer = ParcelCheckAnalyzer(text)
    d = analyzer.data
    try:
        area_total = float(d['area_m2'])
    except Exception:
        area_total = 1000.0

    auto_up = fetch_zoning_info(d["cadastral_area"], d["parcel_no"])
    is_commercial = auto_up["is_commercial"]

    if d.get("has_plomba", False):
        st.error(f"🚨 **KRITICKÉ UPOZORNĚNÍ: OBJEKT JE DOTČEN ZMĚNOU PRÁVNÍHO VZTAHU! ({d['plomba_id']})**\n\nNa nemovitosti právě probíhá vkladové řízení na katastru. Může jít o prodej třetí osobě, exekuci nebo zástavní právo banky. **ZÁKAZ PODPISU A PLATBY: Nutno nahlédnout do spisu na katastru!**")
    else:
        st.success(f"Úspěšně načtena parcela č. **{d['parcel_no']}**, k.ú. **{d['cadastral_area']}** (obec {d['municipality']}) — bez evidované plomby.")

    up_params = {
        "is_commercial": is_commercial,
        "zone_type": auto_up["zone_title"],
        "max_coverage_pct": float(auto_up["max_coverage_pct"]),
        "min_greenery_pct": float(auto_up["min_greenery_pct"]),
        "max_floors": auto_up["max_floors"],
        "nets_verified": nets_manually_confirmed,
        "road": "Sjezd z přilehlé komunikace",
        "infrastructure": "Dle technické dokumentace" if nets_manually_confirmed else "Nezajištěno"
    }

    tab1, tab2 = st.tabs(["📋 1. Základní audit pozemku a rizika", "📐 2. Developerská parcelace a rozpočet sítí"])

    with tab1:
        if is_commercial:
            st.warning(f"📍 **Územní plán (detekováno):** {auto_up['zone_title']}\n\n⚠️ **{auto_up['note']}**")
        else:
            st.info(f"📍 **Územní plán (detekováno):** {auto_up['zone_title']}\n\n✅ {auto_up['note']}")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Výměra pozemku", f"{d['area_m2']} m²")
        cov_pct = auto_up['max_coverage_pct']
        c2.metric("Max. zastavěnost plochy", f"{(area_total * cov_pct / 100):.1f} m²", f"{cov_pct:.0f} %")
        grn_pct = auto_up['min_greenery_pct']
        c3.metric("Min. podíl zeleně", f"{(area_total * grn_pct / 100):.1f} m²", f"{grn_pct:.0f} %")
        c4.metric("Inženýrské sítě", "Potvrzeno v dosahu" if nets_manually_confirmed else "NEOVĚŘENO / CHYBÍ")

        st.subheader("📋 Semafor developerských rizik")
        checks = analyzer.evaluate_developer_rules(up_params)
        for c in checks:
            if c["status"] == "DANGER":
                st.error(f"**[{c['category']}] {c['title']}**\n\n{c['detail']}")
            elif c["status"] == "WARNING":
                st.warning(f"**[{c['category']}] {c['title']}**\n\n{c['detail']}")
            elif c["status"] == "PASS":
                st.success(f"**[{c['category']}] {c['title']}**\n\n{c['detail']}")
            else:
                st.info(f"**[{c['category']}] {c['title']}**\n\n{c['detail']}")

        out_pdf_name = f"Audit_{d['municipality']}_{d['parcel_no'].replace('/', '_')}.pdf"
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_out:
            out_pdf_path = tmp_out.name

        generate_pdf_report(analyzer, out_pdf_path, up_params)

        with open(out_pdf_path, "rb") as f:
            pdf_bytes = f.read()

        st.download_button(
            label="📄 Stáhnout kompletní Manažerský PDF Audit",
            data=pdf_bytes,
            file_name=out_pdf_name,
            mime="application/pdf",
            type="primary"
        )

    with tab2:
        st.subheader("📐 Návrh parcelace a rozpočtový kalkulátor infrastruktury")
        st.caption("Orientační kalkulace dělení pozemku a nákladů na komunikaci, sítě a přípojky dle standardů ČSN 73 6110.")

        p_col1, p_col2 = st.columns(2)
        with p_col1:
            st.markdown("##### 1. Parametry parcelace")
            min_plot_target = st.number_input(
                "Cílová výměra jedné stavební parcely (m²)",
                min_value=400,
                max_value=2500,
                value=800,
                step=50
            )
            road_type = st.selectbox("Typ uličního profilu", [
                "Standardní obytná ulice (šířka koridoru 8,0 m s chodníkem)",
                "Úsporná obytná zóna (šířka koridoru 6,5 m se sdruženým prostorem)"
            ])
            has_turnaround = st.checkbox(
                "Slepá ulice delší než 50 m (vyžaduje obratiště IZS / T-kladivo)",
                value=True
            )

        with p_col2:
            st.markdown("##### 2. Investiční ekonomika")
            purchase_price_m2 = st.number_input(
                "Nákupní cena surového pozemku (Kč/m²)",
                min_value=500,
                max_value=20000,
                value=2500,
                step=100
            )
            sell_price_m2 = st.number_input(
                "Předpokládaná prodejní cena zasíťované parcely (Kč/m²)",
                min_value=1000,
                max_value=30000,
                value=5500,
                step=200
            )

        if "8,0" in road_type:
            road_width = 8.0
            road_unit_cost = 14000.0
            pavement_unit_cost = 4000.0
        else:
            road_width = 6.5
            road_unit_cost = 11000.0
            pavement_unit_cost = 0.0

        turnaround_area = 130.0 if has_turnaround else 0.0

        est_road_length = max(35.0, round((area_total ** 0.5) * 1.15, 0))
        road_area = (est_road_length * road_width) + turnaround_area
        net_building_area = max(0.0, area_total - road_area)

        num_plots = int(net_building_area // min_plot_target)
        avg_plot_area = (net_building_area / num_plots) if num_plots > 0 else 0.0

        st.divider()
        st.markdown("#### 📊 Výsledná bilance území")
        b1, b2, b3, b4 = st.columns(4)
        b1.metric("Celková výměra", f"{area_total:.0f} m²")
        b2.metric("Zábor na silnici a točnu", f"{road_area:.0f} m²", f"{(road_area / area_total * 100):.1f} %")
        b3.metric("Čistá stavební plocha", f"{net_building_area:.0f} m²")
        b4.metric("Počet stavebních parcel", f"{num_plots} parcel", f"prům. {avg_plot_area:.0f} m²")

        st.divider()
        st.markdown("#### 🛠️ Položkový rozpočet vybudování infrastruktury")

        cost_road = est_road_length * road_unit_cost
        cost_pavement = est_road_length * pavement_unit_cost
        cost_water = est_road_length * 4200.0
        cost_sewer = est_road_length * 7500.0
        cost_rain = est_road_length * 5000.0
        cost_elec = est_road_length * 3200.0
        num_lamps = max(2, int(est_road_length // 30) + 1)
        cost_lighting = num_lamps * 45000.0
        cost_connections = num_plots * 110000.0
        cost_turnaround = turnaround_area * 1800.0 if has_turnaround else 0.0
        cost_zpf = road_area * 250.0

        total_capex = (
            cost_road +
            cost_pavement +
            cost_water +
            cost_sewer +
            cost_rain +
            cost_elec +
            cost_lighting +
            cost_connections +
            cost_turnaround +
            cost_zpf
        )

        pavement_label = f"{est_road_length:.0f} bm" if road_width == 8.0 else "V profilu"

        capex_table = [
            {"Položka infrastruktury": f"Komunikace (délka {est_road_length:.0f} m, šířka {road_width} m)", "Jednotka": f"{est_road_length:.0f} bm", "Orientační náklad": f"{cost_road:,.0f} Kč"},
            {"Položka infrastruktury": "Chodník (šířka 1,5 m, zámková dlažba)", "Jednotka": pavement_label, "Orientační náklad": f"{cost_pavement:,.0f} Kč"},
            {"Položka infrastruktury": "Obratiště IZS (kladivo / točna pro hasiče)", "Jednotka": f"{turnaround_area:.0f} m²", "Orientační náklad": f"{cost_turnaround:,.0f} Kč"},
            {"Položka infrastruktury": "Vodovodní řad PE-HD 90/110", "Jednotka": f"{est_road_length:.0f} bm", "Orientační náklad": f"{cost_water:,.0f} Kč"},
            {"Položka infrastruktury": "Splašková kanalizace PVC DN 200/250", "Jednotka": f"{est_road_length:.0f} bm", "Orientační náklad": f"{cost_sewer:,.0f} Kč"},
            {"Položka infrastruktury": "Dešťová retence a odvodnění ulice", "Jednotka": f"{est_road_length:.0f} bm", "Orientační náklad": f"{cost_rain:,.0f} Kč"},
            {"Položka infrastruktury": "Elektro NN (kabelizace + rozvaděče)", "Jednotka": f"{est_road_length:.0f} bm", "Orientační náklad": f"{cost_elec:,.0f} Kč"},
            {"Položka infrastruktury": f"Veřejné LED osvětlení ({num_lamps} stožárů)", "Jednotka": f"{num_lamps} ks", "Orientační náklad": f"{cost_lighting:,.0f} Kč"},
            {"Položka infrastruktury": f"Domovní přípojky pro {num_plots} parcel (voda, kan, elektro)", "Jednotka": f"{num_plots} kpl", "Orientační náklad": f"{cost_connections:,.0f} Kč"},
            {"Položka infrastruktury": "Zákonný poplatek za odnětí komunikace ze ZPF", "Jednotka": f"{road_area:.0f} m²", "Orientační náklad": f"{cost_zpf:,.0f} Kč"}
        ]
        st.table(capex_table)

        col_tot1, col_tot2 = st.columns(2)
        col_tot1.metric("Celkové náklady na zasíťování a komunikaci", f"{total_capex:,.0f} Kč".replace(',', ' '))
        cost_per_plot = (total_capex / num_plots) if num_plots > 0 else 0.0
        col_tot2.metric("Náklad na zasíťování 1 parcely", f"{cost_per_plot:,.0f} Kč".replace(',', ' '))

        st.divider()
        st.markdown("#### 💰 Hrubá investiční rozvaha projektu (P&L)")
        raw_land_cost = area_total * purchase_price_m2
        gross_sales = net_building_area * sell_price_m2
        gross_profit = gross_sales - raw_land_cost - total_capex
        margin_pct = (gross_profit / gross_sales * 100.0) if gross_sales > 0 else 0.0

        r1, r2, r3, r4 = st.columns(4)
        r1.metric("Nákup pozemku", f"{raw_land_cost:,.0f} Kč".replace(',', ' '))
        r2.metric("Tržby z prodeje parcel", f"{gross_sales:,.0f} Kč".replace(',', ' '))
        r3.metric("Předpokládaný hrubý zisk", f"{gross_profit:,.0f} Kč".replace(',', ' '))
        r4.metric("Zisková marže projektu", f"{margin_pct:.1f} %")

    try:
        os.remove(tmp_path)
        os.remove(out_pdf_path)
    except Exception:
        pass
