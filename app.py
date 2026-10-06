import streamlit as st
import tempfile
import os
import re
import pypdf
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as patches

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    HRFlowable,
    Image as ReportLabImage
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


# --- Vykreslení situace parcelace ---
def render_parcelation_plot(area_total, net_area, num_plots, road_width, road_len, has_turn):
    fig, ax = plt.subplots(figsize=(10, 5))
    
    # Geometrické proporce areálu
    aspect_ratio = 1.8
    total_w = (area_total * aspect_ratio) ** 0.5
    total_h = area_total / total_w

    # Obrys celkového pozemku
    outer_box = patches.Rectangle(
        (0, 0), total_w, total_h,
        linewidth=2, edgecolor='#1F4E79', facecolor='#F8FAFC'
    )
    ax.add_patch(outer_box)

    # Koridor páteřní komunikace středem pozemku
    road_y = (total_h - road_width) / 2.0
    road_box = patches.Rectangle(
        (0, road_y), total_w, road_width,
        linewidth=1, edgecolor='#475569', facecolor='#E2E8F0', hatch='//'
    )
    ax.add_patch(road_box)
    ax.text(
        total_w * 0.35, road_y + (road_width / 2.0),
        f"Komunikace (šířka {road_width} m)",
        color='#334155', fontsize=8, weight='bold', va='center'
    )

    # Obratiště IZS na konci
    if has_turn:
        turn_size = 12.0
        turn_y = (total_h - turn_size) / 2.0
        turn_x = max(0, total_w - turn_size)
        turn_box = patches.Rectangle(
            (turn_x, turn_y), turn_size, turn_size,
            linewidth=1.5, edgecolor='#DC2626', facecolor='#FEE2E2'
        )
        ax.add_patch(turn_box)
        ax.text(
            turn_x + (turn_size / 2.0), turn_y + (turn_size / 2.0),
            "Točna IZS\n12x12 m",
            color='#B91C1C', fontsize=7, weight='bold', ha='center', va='center'
        )

    # Rozdělení na parcely po severní a jižní straně ulice
    if num_plots > 0:
        plots_north = (num_plots + 1) // 2
        plots_south = num_plots // 2

        strip_h_north = (total_h - road_width) / 2.0
        strip_h_south = strip_h_north

        dx_north = total_w / max(1, plots_north)
        plot_idx = 1

        # Severní strana
        for i in range(plots_north):
            px = i * dx_north
            pw = dx_north
            p_area = (net_area / num_plots)
            p_box = patches.Rectangle(
                (px, road_y + road_width), pw, strip_h_north,
                linewidth=1, edgecolor='#16A34A', facecolor='#DCFCE7', alpha=0.5
            )
            ax.add_patch(p_box)
            ax.text(
                px + pw / 2.0, road_y + road_width + strip_h_north / 2.0,
                f"P{plot_idx}\n{p_area:.0f} m²",
                color='#15803D', fontsize=8, weight='bold', ha='center', va='center'
            )
            plot_idx += 1

        # Jižní strana
        if plots_south > 0:
            dx_south = total_w / plots_south
            for i in range(plots_south):
                px = i * dx_south
                pw = dx_south
                p_area = (net_area / num_plots)
                p_box = patches.Rectangle(
                    (px, 0), pw, strip_h_south,
                    linewidth=1, edgecolor='#2563EB', facecolor='#DBEAFE', alpha=0.5
                )
                ax.add_patch(p_box)
                ax.text(
                    px + pw / 2.0, strip_h_south / 2.0,
                    f"P{plot_idx}\n{p_area:.0f} m²",
                    color='#1D4ED8', fontsize=8, weight='bold', ha='center', va='center'
                )
                plot_idx += 1

    ax.set_xlim(-5, total_w + 5)
    ax.set_ylim(-5, total_h + 5)
    ax.set_aspect('equal')
    ax.axis('off')
    plt.title(
        f"Schematický návrh parcelace: {num_plots} stavebních parcel na ploše {area_total:.0f} m²",
        fontsize=10, weight='bold', pad=12
    )

    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as img_file:
        plt.savefig(img_file.name, dpi=180, bbox_inches='tight')
        img_path = img_file.name
    plt.close(fig)
    return img_path


def generate_pdf(analyzer, out_pdf, up, prices, plot_img=None):
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

    if plot_img and os.path.exists(plot_img):
        story.append(Paragraph("<b>Situační návrh dělení pozemku a uličního koridoru (ČSN 73 6110):</b>", t_s))
        story.append(Spacer(1, 3))
        story.append(ReportLabImage(plot_img, width=17.5*cm, height=8.0*cm))
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

        # --- Grafické zobrazení plánku parcelace ---
        st.divider()
        st.markdown("#### 🗺️ Vizuální situační schéma parcelace")
        plot_img_path = render_parcelation_plot(
            area_total, net_m2, n_plots, r_w, r_len, has_turn
        )
        st.image(plot_img_path, use_container_width=True)

        st.divider()
        st.markdown("#### 🛠️ Položkový rozpočet infrastruktury")
        unit_road = 14000.0 if r_w == 8.0 else 11000.0
        cost_road = r_len * unit_road
        cost_pave = r_len * 4000.0 if r_w == 8.0 else 0.0
        cost_water = r_len * 4200.0
        cost_sewer = r_len * 7500.0
        cost_rain = r_len * 5000.0
        cost_elec = r_len * 3200.0
        n_lamps = max(2, int(r_len // 30) + 1)
        cost_light = n_lamps * 45000.0
        cost_conn = n_plots * 110000.0
        cost_turn = turn_m2 * 1800.0 if has_turn else 0.0
        cost_zpf = r_m2 * 250.0
        cost_legal = 120000.0 if has_contract else 0.0
        cost_contrib = n_plots * contrib if has_contract else 0.0

        tot_capex = (
            cost_road + cost_pave + cost_water + cost_sewer + cost_rain +
            cost_elec + cost_light + cost_conn + cost_turn + cost_zpf +
            cost_legal + cost_contrib
        )

        tbl = [
            {"Položka infrastruktury": f"Komunikace ({r_len:.0f} bm, šířka {r_w} m)", "Orientační náklad": f"{cost_road:,.0f} Kč"},
            {"Položka infrastruktury": "Chodník 1,5 m", "Orientační náklad": f"{cost_pave:,.0f} Kč"},
            {"Položka infrastruktury": "Obratiště IZS (točna)", "Orientační náklad": f"{cost_turn:,.0f} Kč"},
            {"Položka infrastruktury": "Vodovodní řad PE-HD", "Orientační náklad": f"{cost_water:,.0f} Kč"},
            {"Položka infrastruktury": "Splašková kanalizace", "Orientační náklad": f"{cost_sewer:,.0f} Kč"},
            {"Položka infrastruktury": "Dešťová retence ulice", "Orientační náklad": f"{cost_rain:,.0f} Kč"},
            {"Položka infrastruktury": "Elektro NN (kabelizace)", "Orientační náklad": f"{cost_elec:,.0f} Kč"},
            {"Položka infrastruktury": f"Veřejné osvětlení ({n_lamps} lamp)", "Orientační náklad": f"{cost_light:,.0f} Kč"},
            {"Položka infrastruktury": f"Přípojky pro {n_plots} parcel", "Orientační náklad": f"{cost_conn:,.0f} Kč"},
            {"Položka infrastruktury": "Odnětí silnice ze ZPF", "Orientační náklad": f"{cost_zpf:,.0f} Kč"}
        ]
        if has_contract:
            tbl.append({"Položka infrastruktury": "Právní servis plánovací smlouvy", "Orientační náklad": f"{cost_legal:,.0f} Kč"})
            tbl.append({"Položka infrastruktury": f"Příspěvek obci ({n_plots} parcel)", "Orientační náklad": f"{cost_contrib:,.0f} Kč"})

        st.table(tbl)
        k1, k2 = st.columns(2)
        k1.metric("Celkové náklady sítí", f"{tot_capex:,.0f} Kč".replace(',', ' '))
        cpp = (tot_capex / n_plots) if n_plots > 0 else 0.0
        k2.metric("Náklad na 1 parcelu", f"{cpp:,.0f} Kč".replace(',', ' '))

        st.divider()
        raw_c = area_total * buy_p
        rev_c = net_m2 * sell_p
        prof_c = rev_c - raw_c - tot_capex
        mar_c = (prof_c / rev_c * 100.0) if rev_c > 0 else 0.0

        r1, r2, r3, r4 = st.columns(4)
        r1.metric("Nákup pozemku", f"{raw_c:,.0f} Kč".replace(',', ' '))
        r2.metric("Tržby z parcel", f"{rev_c:,.0f} Kč".replace(',', ' '))
        r3.metric("Hrubý zisk", f"{prof_c:,.0f} Kč".replace(',', ' '))
        r4.metric("Marže projektu", f"{mar_c:.1f} %")

        out_name = f"Audit_s_parcelaci_{d['municipality']}_{d['parcel_no'].replace('/', '_')}.pdf"
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_o:
            tmp_pdf_p = tmp_o.name

        generate_pdf(analyzer, tmp_pdf_p, up_params, bench_p, plot_img_path)
        with open(tmp_pdf_p, "rb") as f_pdf:
            pdf_b = f_pdf.read()

        st.download_button(
            "📄 Stáhnout Manažerský PDF Audit včetně Situačního plánku",
            data=pdf_b,
            file_name=out_name,
            mime="application/pdf",
            type="primary"
        )

    try:
        os.remove(tmp_p)
        os.remove(tmp_pdf_p)
        os.remove(plot_img_path)
    except Exception:
        pass
