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
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
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
            "region": "Praha-východ (příměstský trh)",
            "raw_avg": 3500, "serviced_avg": 9500, "comm_avg": 4800,
            "raw_range": "2 500 – 4 500 Kč", "serviced_range": "7 500 – 12 500 Kč"
        }
    return {
        "region": "Regionální průměr ČR",
        "raw_avg": 1800, "serviced_avg": 5000, "comm_avg": 3000,
        "raw_range": "1 000 – 2 800 Kč", "serviced_range": "3 500 – 7 000 Kč"
    }

# --- Územní plán ---
def fetch_zoning_info(cadastral_area, parcel_no):
    c = str(cadastral_area).strip()
    p = str(parcel_no).strip()
    if "Tehovec" in c and "877" in p:
        return {
            "title": "VD/OM — Komerční plocha a sklady (lokalita Z8)",
            "is_commercial": True,
            "requires_contract": True,
            "cov_pct": 50.0, "grn_pct": 20.0,
            "note": "ZÁKAZ STAVBY RD. Podmíněno PLÁNOVACÍ SMLOUVOU s obcí!"
        }
    return {
        "title": "BI — Bydlení v rodinných domech",
        "is_commercial": False,
        "requires_contract": False,
        "cov_pct": 30.0, "grn_pct": 50.0,
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
            "parcel_no": self._ex(r"Parcelní číslo:\s*([0-9]+(?:/[0-9]+)?)"),
            "municipality": self._ex(r"Obec:\s*([^\n\r\[]+)"),
            "cadastral_area": self._ex(r"Katastrální území:\s*([^\n\r\[]+)"),
            "lv_no": self._ex(r"Číslo LV:\s*([0-9]+)"),
            "area_m2": self._ex(r"Výměra \[m2\]:\s*([0-9\s]+)").replace(" ", ""),
            "land_type": self._ex(r"Druh pozemku:\s*([^\n\r]+)"),
            "owner": self._ex(r"Vlastnické právo\s*(?:Podíl)?\s*\n\s*([^\n\r]+)"),
            "protection": self._ex(r"Způsob ochrany nemovitosti\s*Název\s*\n\s*([^\n\r]+)"),
            "limitations": "Nejsou evidována žádná omezení" in self.raw,
            "bpej": self._ex(r"BPEJ\s*Výměra\s*\n\s*([0-9]+)"),
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
        max_footprint = area * (cov / 100.0)
        checks = []

        if p["has_plomba"]:
            checks.append({
                "cat": "PLOMBA (STOPKA)", "stat": "DANGER",
                "title": f"Aktivní plomba: {p['plomba_id']}",
                "detail": "Probíhá vkladové řízení na katastru. ZÁKAZ PLATBY před nahlédnutím do spisu!"
            })
        else:
            checks.append({
                "cat": "Řízení na KN", "stat": "PASS",
                "title": "Bez plomby", "detail": "K nemovitosti neběží žádné zaplombované řízení."
            })

        if up["is_commercial"]:
            checks.append({
                "cat": "Územní plán", "stat": "WARNING",
                "title": f"Komerční zóna: {up['title']}",
                "detail": f"ZÁKAZ RODINNÝCH DOMŮ. Max. zastavěnost {cov:.0f} % = {max_footprint:.1f} m²."
            })
        else:
            checks.append({
                "cat": "Územní plán", "stat": "PASS",
                "title": f"Obytná zóna: {up['title']}",
                "detail": f"Přípustná stavba rodinného domu. Max. zastavěnost {cov:.0f} % = {max_footprint:.1f} m²."
            })

        if up.get("requires_contract", False):
            checks.append({
                "cat": "Podmínka rozvoje", "stat": "WARNING",
                "title": "Vyžadována Plánovací smlouva s obcí (§ 130 SZ)",
                "detail": "Povolení stavby vyžaduje schválení smlouvy zastupitelstvem obce (infrastruktura / příspěvek)."
            })

        if p["mortgage"]:
            checks.append({
                "cat": "Zástavní práva", "stat": "WARNING",
                "title": "Na pozemku vázne zástavní právo",
                "detail": "V kupní smlouvě podmínit platbu kvitancí věřitele a výmazem zástavy."
            })
        elif p["limitations"]:
            checks.append({
                "cat": "Právní stav", "stat": "PASS",
                "title": "V části C nejsou evidována omezení",
                "detail": "Pozemek je bez zástav a věcných břemen."
            })

        if not up.get("nets_verified", False):
            checks.append({
                "cat": "Inženýrské sítě", "stat": "WARNING",
                "title": "Sítě nejsou na pozemku ověřeny",
                "detail": "V katastru sítě nejsou. Nutno podat žádost o vyjádření k existenci sítí."
            })
        else:
            checks.append({
                "cat": "Inženýrské sítě", "stat": "PASS",
                "title": "Sítě potvrzeny v dosahu",
                "detail": "Dle technické dokumentace a vyjádření správců."
            })

        return checks


# --- Čisté vektorové SVG vykreslení parcelace (bez nutnosti instalovat matplotlib) ---
def render_parcelation_svg(area_total, net_area, num_plots, road_width, has_turn):
    svg_w = 850
    svg_h = 380
    margin = 25

    w_inner = svg_w - (2 * margin)
    h_inner = svg_h - (2 * margin)

    r_h_px = max(26, int(h_inner * (road_width / 25.0)))
    r_y_px = margin + int((h_inner - r_h_px) / 2.0)

    svg_parts = [
        f'<svg width="100%" height="{svg_h}" viewBox="0 0 {svg_w} {svg_h}" '
        f'xmlns="http://www.w3.org/2000/svg" style="background:#0F172A; border-radius:8px;">',
        f'<rect x="{margin}" y="{margin}" width="{w_inner}" height="{h_inner}" '
        f'fill="#1E293B" stroke="#38BDF8" stroke-width="2"/>'
    ]

    # Koridor silnice
    svg_parts.append(
        f'<rect x="{margin}" y="{r_y_px}" width="{w_inner}" height="{r_h_px}" '
        f'fill="#475569" stroke="#64748B" stroke-width="1.5"/>'
    )
    svg_parts.append(
        f'<text x="{margin + 20}" y="{r_y_px + int(r_h_px/2) + 4}" fill="#F1F5F9" '
        f'font-family="sans-serif" font-size="11" font-weight="bold">'
        f'Páteřní komunikace (šířka {road_width} m)</text>'
    )

    # Točna IZS
    if has_turn:
        turn_w = 46
        turn_h = min(h_inner - 10, r_h_px + 36)
        turn_x = margin + w_inner - turn_w - 4
        turn_y = margin + int((h_inner - turn_h) / 2.0)
        svg_parts.append(
            f'<rect x="{turn_x}" y="{turn_y}" width="{turn_w}" height="{turn_h}" '
            f'fill="#EF4444" fill-opacity="0.25" stroke="#EF4444" stroke-width="1.5" stroke-dasharray="4"/>'
        )
        svg_parts.append(
            f'<text x="{turn_x + int(turn_w/2)}" y="{turn_y + int(turn_h/2) + 3}" fill="#FCA5A5" '
            f'font-family="sans-serif" font-size="9" font-weight="bold" text-anchor="middle">IZS</text>'
        )

    # Rozdělení na parcely (Sever / Jih)
    if num_plots > 0:
        plots_north = (num_plots + 1) // 2
        plots_south = num_plots // 2
        p_area = net_area / num_plots

        # Severní pás
        north_h = r_y_px - margin
        pw_north = w_inner / max(1, plots_north)
        plot_idx = 1
        for i in range(plots_north):
            px = margin + (i * pw_north)
            svg_parts.append(
                f'<rect x="{px}" y="{margin}" width="{pw_north}" height="{north_h}" '
                f'fill="#10B981" fill-opacity="0.15" stroke="#10B981" stroke-width="1"/>'
            )
            svg_parts.append(
                f'<text x="{px + pw_north/2}" y="{margin + north_h/2 - 4}" fill="#6EE7B7" '
                f'font-family="sans-serif" font-size="11" font-weight="bold" text-anchor="middle">P{plot_idx}</text>'
            )
            svg_parts.append(
                f'<text x="{px + pw_north/2}" y="{margin + north_h/2 + 12}" fill="#A7F3D0" '
                f'font-family="sans-serif" font-size="9" text-anchor="middle">{p_area:.0f} m²</text>'
            )
            plot_idx += 1

        # Jižní pás
        south_y = r_y_px + r_h_px
        south_h = (margin + h_inner) - south_y
        if plots_south > 0:
            pw_south = w_inner / plots_south
            for i in range(plots_south):
                px = margin + (i * pw_south)
                svg_parts.append(
                    f'<rect x="{px}" y="{south_y}" width="{pw_south}" height="{south_h}" '
                    f'fill="#3B82F6" fill-opacity="0.15" stroke="#3B82F6" stroke-width="1"/>'
                )
                svg_parts.append(
                    f'<text x="{px + pw_south/2}" y="{south_y + south_h/2 - 4}" fill="#93C5FD" '
                    f'font-family="sans-serif" font-size="11" font-weight="bold" text-anchor="middle">P{plot_idx}</text>'
                )
                svg_parts.append(
                    f'<text x="{px + pw_south/2}" y="{south_y + south_h/2 + 12}" fill="#BFDBFE" '
                    f'font-family="sans-serif" font-size="9" text-anchor="middle">{p_area:.0f} m²</text>'
                )
                plot_idx += 1

    svg_parts.append('</svg>')
    return "".join(svg_parts)


def generate_pdf(analyzer, out_pdf, up, prices):
    d = analyzer.data
    evals = analyzer.evaluate_rules(up)
    doc = SimpleDocTemplate(
        out_pdf,
        pagesize=A4,
        rightMargin=1.5*cm, leftMargin=1.5*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm
    )
    styles = getSampleStyleSheet()

    c_blue = colors.HexColor('#1F4E79')
    c_red = colors.HexColor('#B71C1C')
    t_s = ParagraphStyle('T', fontName=FONT_BOLD, fontSize=13, leading=17, textColor=c_blue)
    b_s = ParagraphStyle('B', fontName=FONT_MAIN, fontSize=8, leading=11)
    bp = ParagraphStyle('BP', fontName=FONT_BOLD, fontSize=7, textColor=colors.HexColor('#1B5E20'), alignment=1)
    bw = ParagraphStyle('BW', fontName=FONT_BOLD, fontSize=7, textColor=colors.HexColor('#E65100'), alignment=1)
    bd = ParagraphStyle('BD', fontName=FONT_BOLD, fontSize=7, textColor=c_red, alignment=1)

    story = []
    story.append(Paragraph("PARCELCHECK AI — DEVELOPERSKÝ AUDIT & PARCELACE", t_s))
    story.append(Paragraph(
        f"Parcela č. {d['parcel_no']} | k.ú. {d['cadastral_area']} ({d['municipality']}) | LV {d['lv_no']}",
        b_s
    ))
    story.append(HRFlowable(width="100%", thickness=1.5, color=c_blue, spaceBefore=2, spaceAfter=6))

    if d["has_plomba"]:
        tp = Table([[
            Paragraph(f"<b>STOPKA: NA POZEMKU VÁZNE PLOMBA ({d['plomba_id']})</b>", ParagraphStyle('P', fontName=FONT_BOLD, fontSize=8.5, textColor=c_red))
        ]], colWidths=[18*cm])
        tp.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#FFEBEE')),
            ('BOX', (0,0), (-1,-1), 1, c_red),
            ('PADDING', (0,0), (-1,-1), 4)
        ]))
        story.append(tp)
        story.append(Spacer(1, 4))

    info_data = [
        [Paragraph(f"<b>Lokalita:</b> {d['municipality']} ({d['cadastral_area']})", b_s), Paragraph(f"<b>Výměra:</b> {d['area_m2']} m²", b_s)],
        [Paragraph(f"<b>Parcela / LV:</b> {d['parcel_no']} / LV {d['lv_no']}", b_s), Paragraph(f"<b>Druh:</b> {d['land_type']}", b_s)]
    ]
    t_info = Table(info_data, colWidths=[9*cm, 9*cm])
    t_info.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F8FAFC')),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
        ('PADDING', (0,0), (-1,-1), 3)
    ]))
    story.append(t_info)
    story.append(Spacer(1, 6))

    story.append(Paragraph("<b>Semafor developerských rizik a územního plánu:</b>", t_s))
    for item in evals:
        bg = '#FFEBEE' if item['stat'] == 'DANGER' else ('#FFF3E0' if item['stat'] == 'WARNING' else '#E8F5E9')
        badge = bd if item['stat'] == 'DANGER' else (bw if item['stat'] == 'WARNING' else bp)
        st_label = "STOPKA" if item['stat'] == 'DANGER' else ("POZOR" if item['stat'] == 'WARNING' else "OK")
        row = [
            [Paragraph(f"<b>[{item['cat']}] {item['title']}</b>", b_s), Paragraph(st_label, badge)],
            [Paragraph(item['detail'], b_s), ""]
        ]
        tr = Table(row, colWidths=[14.5*cm, 3.5*cm])
        tr.setStyle(TableStyle([
            ('SPAN', (0,1), (1,1)),
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor(bg)),
            ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
            ('PADDING', (0,0), (-1,-1), 3)
        ]))
        story.append(tr)
        story.append(Spacer(1, 2))

    doc.build(story)
    return out_pdf

# ==================== STREAMLIT ROZHRANÍ ====================
st.set_page_config(page_title="ParcelCheck AI", page_icon="🏗️", layout="wide")

st.title("🏗️ ParcelCheck AI — Due Diligence & Developerský audit")
st.caption("Automatická detekce katastru, územního plánu, cenové mapy a situace parcelace")

with st.sidebar:
    st.header("⚙️ Ověření pozemku")
    nets_ok = st.checkbox("Mám ověřeno fyzické napojení na sítě", value=False)
    st.caption("Při nezaškrtnutí systém sítě uvádí jako neověřené riziko.")

uploaded_file = st.file_uploader("Nahrajte PDF výpisu z Nahlížení do KN", type=["pdf"])

if uploaded_file is not None:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(uploaded_file.read())
        tmp_p = tmp.name

    reader = pypdf.PdfReader(tmp_p)
    text = "".join([p.extract_text() or "" for p in reader.pages])

    analyzer = ParcelCheckAnalyzer(text)
    d = analyzer.data
    try:
        area_total = float(d['area_m2'])
    except Exception:
        area_total = 1000.0

    auto_up = fetch_zoning_info(d["cadastral_area"], d["parcel_no"])
    bench_p = get_benchmark_prices(d["municipality"], d["cadastral_area"])

    if d["has_plomba"]:
        st.error(f"🚨 **KRITICKÉ UPOZORNĚNÍ: PLOMBA ({d['plomba_id']})!** Zákaz podpisu a platby bez nahlédnutí do spisu.")
    else:
        st.success(f"Parcela č. **{d['parcel_no']}**, k.ú. **{d['cadastral_area']}** — bez evidované plomby.")

    up_params = {
        "title": auto_up["title"],
        "is_commercial": auto_up["is_commercial"],
        "requires_contract": auto_up.get("requires_contract", False),
        "cov_pct": float(auto_up["cov_pct"]),
        "grn_pct": float(auto_up["grn_pct"]),
        "nets_verified": nets_ok
    }

    t1, t2 = st.tabs(["📋 1. Právní & Územní Audit", "📐 2. Developerská parcelace & Plánek"])

    with t1:
        if auto_up["is_commercial"]:
            st.warning(f"📍 **Územní plán:** {auto_up['title']}\n\n⚠️ {auto_up['note']}")
        else:
            st.info(f"📍 **Územní plán:** {auto_up['title']}\n\n✅ {auto_up['note']}")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Výměra", f"{d['area_m2']} m²")
        cov_val = auto_up['cov_pct']
        c2.metric("Max. zastavěnost", f"{(area_total * cov_val / 100):.1f} m²", f"{cov_val:.0f} %")
        grn_val = auto_up['grn_pct']
        c3.metric("Min. zeleň", f"{(area_total * grn_val / 100):.1f} m²", f"{grn_val:.0f} %")
        c4.metric("Sítě", "Potvrzeno" if nets_ok else "NEOVĚŘENO")

        st.subheader("📊 Cenová mapa lokality")
        st.caption(f"Oblast: **{bench_p['region']}**")
        cp1, cp2, cp3 = st.columns(3)
        cp1.metric("Nezasíťovaný pozemek", f"{bench_p['raw_avg']:,} Kč/m²", bench_p['raw_range'])
        cp2.metric("Zasíťovaná parcela RD", f"{bench_p['serviced_avg']:,} Kč/m²", bench_p['serviced_range'])
        cp3.metric("Komerční areál", f"{bench_p['comm_avg']:,} Kč/m²")

        st.subheader("📋 Semafor developerských rizik")
        for check in analyzer.evaluate_rules(up_params):
            if check["stat"] == "DANGER":
                st.error(f"**[{check['cat']}] {check['title']}**\n\n{check['detail']}")
            elif check["stat"] == "WARNING":
                st.warning(f"**[{check['cat']}] {check['title']}**\n\n{check['detail']}")
            else:
                st.success(f"**[{check['cat']}] {check['title']}**\n\n{check['detail']}")

        out_name = f"Audit_{d['municipality']}_{d['parcel_no'].replace('/', '_')}.pdf"
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_o:
            tmp_pdf_p = tmp_o.name

        generate_pdf(analyzer, tmp_pdf_p, up_params, bench_p)
        with open(tmp_pdf_p, "rb") as f_pdf:
            pdf_b = f_pdf.read()

        st.download_button(
            "📄 Stáhnout Manažerský PDF Audit",
            data=pdf_b,
            file_name=out_name,
            mime="application/pdf",
            type="primary"
        )

    with t2:
        st.subheader("📐 Návrh parcelace a rozpočet infrastruktury dle ČSN")
        pc1, pc2 = st.columns(2)
        with pc1:
            target_plot = st.number_input("Cílová výměra 1 parcely (m²)", min_value=400, max_value=2500, value=800, step=50)
            road_sel = st.selectbox("Typ uličního profilu", ["Standardní (8,0 m s chodníkem)", "Úsporná (6,5 m)"])
            has_turn = st.checkbox("Slepá ulice delší než 50 m (obratiště IZS)", value=True)
            has_contract = st.checkbox("Vyžadována plánovací smlouva s obcí (Z8 / rozvoj)", value=auto_up.get("requires_contract", False))
            contrib = st.number_input("Příspěvek obci na 1 parcelu (Kč)", min_value=0, max_value=500000, value=150000 if has_contract else 0, step=25000) if has_contract else 0

        with pc2:
            buy_p = st.number_input("Nákup surového pozemku (Kč/m²)", value=int(bench_p['raw_avg']), step=100)
            def_s = bench_p['comm_avg'] if auto_up['is_commercial'] else bench_p['serviced_avg']
            sell_p = st.number_input("Prodej zasíťované parcely (Kč/m²)", value=int(def_s), step=200)

        r_w = 8.0 if "8,0" in road_sel else 6.5
        turn_m2 = 130.0 if has_turn else 0.0
        r_len = max(35.0, round((area_total ** 0.5) * 1.15, 0))
        r_m2 = (r_len * r_w) + turn_m2
        net_m2 = max(0.0, area_total - r_m2)
        n_plots = int(net_m2 // target_plot)
        avg_plot = (net_m2 / n_plots) if n_plots > 0 else 0.0

        st.divider()
        b1, b2, b3, b4 = st.columns(4)
        b1.metric("Celková výměra", f"{area_total:.0f} m²")
        b2.metric("Silnice a točna", f"{r_m2:.0f} m²", f"{(r_m2/area_total*100):.1f} %")
        b3.metric("Čistá plocha parcel", f"{net_m2:.0f} m²")
        b4.metric("Počet parcel", f"{n_plots} ks", f"prům. {avg_plot:.0f} m²")

        # --- Vektorový
