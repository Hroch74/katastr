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

# Registrace písma Windows (Arial s plnou podporou češtiny)
font_registered = False
for font_path in [
    'C:\\Windows\\Fonts\\arial.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
]:
    if os.path.exists(font_path):
        try:
            bold_path = font_path.replace('arial.ttf', 'arialbd.ttf').replace('DejaVuSans.ttf', 'DejaVuSans-Bold.ttf')
            oblique_path = font_path.replace('arial.ttf', 'ariali.ttf').replace('DejaVuSans.ttf', 'DejaVuSans-Oblique.ttf')
            pdfmetrics.registerFont(TTFont('AppFont', font_path))
            pdfmetrics.registerFont(TTFont('AppFont-Bold', bold_path if os.path.exists(bold_path) else font_path))
            pdfmetrics.registerFont(TTFont('AppFont-Oblique', oblique_path if os.path.exists(oblique_path) else font_path))
            font_registered = True
            break
        except:
            pass

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
                "detail": f"Na listu vlastnictví probíhá aktivní vkladové řízení ({p['plomba_id']}). Může jít o prodej třetí osobě, exekuční příkaz nebo zástavní právo. ZÁKAZ PODPISU A PLATBY bez nahlédnutí do spisu na katastru!"
            })
        else:
            checks.append({
                "category": "Probíhající řízení",
                "status": "PASS",
                "title": "Nemovitost není dotčena žádnou změnou (bez plomby)",
                "detail": "K nemovitosti neběží žádné zaplombované vkladové řízení."
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
                "detail": f"Celá plocha {area:.0f} m² je v ZPF. Pro stavbu je nutné vyjmout zastavěnou a zpevněnou plochu (cca {max_footprint:.0f} m²)."
            })

        checks.append({
            "category": "Sítě a dopravní infrastruktura",
            "status": "PASS",
            "title": "Dopravní napojení a sítě",
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
    font_main = 'AppFont' if font_registered else 'Helvetica'
    font_bold = 'AppFont-Bold' if font_registered else 'Helvetica-Bold'
    font_oblique = 'AppFont-Oblique' if font_registered else 'Helvetica-Oblique'

    title_style = ParagraphStyle('DocTitle', parent=styles['Heading1'], fontName=font_bold, fontSize=15, leading=19, textColor=colors.HexColor('#1F4E79'), spaceAfter=3)
    subtitle_style = ParagraphStyle('DocSubtitle', parent=styles['Normal'], fontName=font_oblique, fontSize=9, leading=13, textColor=colors.HexColor('#555555'), spaceAfter=10)
    h2_style = ParagraphStyle('H2', parent=styles['Heading2'], fontName=font_bold, fontSize=11, leading=15, textColor=colors.HexColor('#1F4E79'), spaceBefore=8, spaceAfter=5)
    body_style = ParagraphStyle('Body', parent=styles['Normal'], fontName=font_main, fontSize=8.5, leading=12, textColor=colors.HexColor('#222222'))

    badge_pass = ParagraphStyle('Pass', fontName=font_bold, fontSize=7.5, textColor=colors.HexColor('#1B5E20'), alignment=1)
    badge_warn = ParagraphStyle('Warn', fontName=font_bold, fontSize=7.5, textColor=colors.HexColor('#E65100'), alignment=1)
    badge_danger = ParagraphStyle('Danger', fontName=font_bold, fontSize=7.5, textColor=colors.HexColor('#B71C1C'), alignment=1)
    badge_info = ParagraphStyle('Info', fontName=font_bold, fontSize=7.5, textColor=colors.HexColor('#0D47A1'), alignment=1)

    story = []
    story.append(Paragraph("PARCELCHECK AI — DEVELOPERSKÝ AUDIT POZEMKU", title_style))
    story.append(Paragraph(f"Automatická prověrka parcely č. {data['parcel_no']} | k.ú. {data['cadastral_area']} (obec {data['municipality']}) | LV {data['lv_no']}", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#1F4E79'), spaceBefore=2, spaceAfter=8))

    if data.get("has_plomba", False):
        plomba_table = [
            [Paragraph(f"<b>POZOR: NA POZEMKU VÁZNE PLOMBA ({data['plomba_id']})</b><br/>Nemovitost je dotčena probíhající změnou právního vztahu. Před jakoukoliv transakcí je nezbytné nahlédnout do spisu na katastru!", ParagraphStyle('PlombaWarning', fontName=font_bold, fontSize=9, textColor=colors.HexColor('#B71C1C')))]
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
        [Paragraph("Výškový limit stavby", body_style), Paragraph(up_params.get('max_floors', 'max. 10 m'), body_style), Paragraph("Dle požadavků technologie / skladové haly", body_style)],
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
    net_data = [
        [Paragraph("<b>Infrastruktura</b>", body_style), Paragraph("<b>Dostupnost & Umístění řadu</b>", body_style), Paragraph("<b>Podmínka pro záměr</b>", body_style)],
        [Paragraph("Elektro (NN / VN)", body_style), Paragraph("V přilehlém uličním profilu", body_style), Paragraph("Rezervace dostatečného příkonu", body_style)],
        [Paragraph("Vodovod", body_style), Paragraph("Obecní řad DN v komunikaci", body_style), Paragraph("Vodovoměrná šachta / požární kapacita", body_style)],
        [Paragraph("Kanalizace", body_style), Paragraph("Splašková stoka v dosahu", body_style), Paragraph("Revizní šachta / lapač dle provozu", body_style)],
        [Paragraph("Dešťové vody", body_style), Paragraph("Retenční nádrž na pozemku s regulovaným odtokem", body_style), Paragraph("Vsakování velkých ploch střech a parkoviště", body_style)],
    ]
    t_net = Table(net_data, colWidths=[4.0*cm, 7.5*cm, 6.5*cm])
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
    page_title="ParcelCheck AI — Developerský audit pozemků",
    page_icon="🏗️",
    layout="wide"
)

st.title("🏗️ ParcelCheck AI — Due Diligence & Developerský audit")
st.caption("Automatická detekce plomb (změn právního vztahu), limitů územního plánu a sítí")

with st.sidebar:
    st.header("⚙️ Typ záměru a územní plán")
    project_type = st.radio("Cílový záměr:", ["Bydlení (Rodinné domy / BI)", "Komerce / Výroba / Sklady (VD/OM)"])
    is_commercial = project_type.startswith("Komerce")

    if is_commercial:
        zone_name = st.selectbox("Komerční zóna ÚP", [
            "VD / OM — Plochy drobné výroby, skladů a komerce",
            "VL — Plochy lehkého průmyslu",
            "SM — Plochy smíšené výrobní a obytné",
            "OK — Plochy komerčního vybavení a obchodu"
        ])
        max_kzp = st.slider("Max. koeficient zastavění areálu (KZP v %)", 20, 80, 50, step=5)
        min_kz = st.slider("Min. podíl vsakovací / izolační zeleně (KZ v %)", 10, 50, 20, step=5)
        min_plot = 1000.0
        max_floors = st.selectbox("Výškový limit stavby", ["max. 9 m (2 NP)", "max. 12 m (skladové haly)", "max. 15 m"])
    else:
        zone_name = st.selectbox("Obytná zóna ÚP", [
            "BI — Bydlení individuální v RD",
            "BV — Bydlení venkovské",
            "SM — Plochy smíšené obytné"
        ])
        max_kzp = st.slider("Max. koeficient zastavění (KZP v %)", 15, 60, 30, step=5)
        min_kz = st.slider("Min. podíl zeleně (KZ v %)", 20, 70, 50, step=5)
        min_plot = st.number_input("Min. výměra parcely pro stavbu RD (m²)", 400, 2000, 800, step=50)
        max_floors = st.selectbox("Výšková regulace", ["1 NP + podkroví", "2 NP + podkroví (max. 9 m)", "2 NP s plochou střechou"])

    st.divider()
    st.subheader("🌐 Dostupné sítě")
    has_water = st.checkbox("Veřejný vodovodní řad", value=True)
    has_sewer = st.checkbox("Splašková kanalizace", value=True)
    has_elec = st.checkbox("Elektřina NN/VN v dosahu", value=True)
    has_gas = st.checkbox("Plynovod", value=False)

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

    if d.get("has_plomba", False):
        st.error(f"🚨 **KRITICKÉ UPOZORNĚNÍ: OBJEKT JE DOTČEN ZMĚNOU PRÁVNÍHO VZTAHU! ({d['plomba_id']})**\n\nNa nemovitosti právě probíhá vkladové řízení na katastru. Může jít o právě podaný prodej třetí osobě, exekuci nebo zástavní právo banky. **ZÁKAZ PODPISU A PLATBY: Nutno nahlédnout do spisu na katastru!**")
    else:
        st.success(f"Úspěšně načtena parcela č. **{d['parcel_no']}**, k.ú. **{d['cadastral_area']}** (obec {d['municipality']}) — bez evidované plomby.")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Výměra pozemku", f"{d['area_m2']} m²")
    with col2:
        area_num = float(d['area_m2']) if d['area_m2'].isdigit() else 1000.0
        st.metric("Max. zastavěnost plochy", f"{(area_num * max_kzp / 100):.1f} m²", f"{max_kzp} %")
    with col3:
        st.metric("Min. zeleň / vsak", f"{(area_num * min_kz / 100):.1f} m²", f"{min_kz} %")
    with col4:
        st.metric("Právní stav", "POZOR: Plomba / Omezení" if (d.get("has_plomba", False) or not d['limitations']) else "V pořádku")

    up_params = {
        "is_commercial": is_commercial,
        "zone_type": zone_name,
        "max_coverage_pct": float(max_kzp),
        "min_greenery_pct": float(min_kz),
        "max_floors": max_floors,
        "min_plot_size": float(min_plot),
        "sewerage": "Splašková stoka v komunikaci" if has_sewer else "Vlastní ČOV / jímka s lapačem ropných látek",
        "water": "Obecní vodovodní řad" if has_water else "Vlastní vrt / studna",
        "electricity": "Distribuční síť NN/VN v uličním profilu" if has_elec else "Nutné prodloužení vedení",
        "gas": "Plynovod v dosahu" if has_gas else "Bez plynu",
        "road": "Přímé napojení na komunikaci (sjezd)",
        "infrastructure": "Elektro, voda, vsakovací retence na pozemku"
    }

    st.subheader("📋 Developerský rozbor a semafor rizik")
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

    try:
        os.remove(tmp_path)
        os.remove(out_pdf_path)
    except:
        pass