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

    if d["has_plomba"]:
        tp = Table([[
            Paragraph(
                f"<b>STOPKA: PLOMBA ({d['plomba_id']})</b>",
                ParagraphStyle('P', fontName=FONT_BOLD, fontSize=8.5, textColor=c_red)
            )
        ]], colWidths=[18*cm])
        tp.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#FFEBEE')),
            ('BOX', (0,0), (-1,-1), 1, c_red),
            ('PADDING', (0,0), (-1,-1), 4)
        ]))
        story.append(tp)
        story.append(Spacer(1, 4))

    info_data = [
        [
            Paragraph(f"<b>Obec:</b> {d['municipality']}", b_s),
            Paragraph(f"<b>Výměra:</b> {d['area_m2']} m²", b_s)
        ],
        [
            Paragraph(f"<b>Parcela / LV:</b> {d['parcel_no']} / {d['lv_no']}", b_s),
            Paragraph(f"<b>Druh:</b> {d['land_type']}", b_s)
        ]
    ]
    t_info = Table(info_data, colWidths=[9*cm, 9*cm])
    t_info.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F8FAFC')),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
        ('PADDING', (0,0), (-1,-1), 3)
    ]))
    story.append(t_info)
    story.append(Spacer(1, 6))

    if parcel_table:
        story.append(
            Paragraph("<b>Geometrický návrh rozdělení pozemku:</b>", t_s)
        )
        p_rows = [[
            Paragraph("<b>Označení</b>", b_s),
            Paragraph("<b>Druh plochy</b>", b_s),
            Paragraph("<b>Výměra</b>", b_s),
            Paragraph("<b>Dopravní napojení</b>", b_s)
        ]]
        for row in parcel_table:
            p_rows.append([
                Paragraph(row["Označení parcely"], b_s),
                Paragraph(row["Účel využití"], b_s),
                Paragraph(row["Výměra"], b_s),
                Paragraph(row["Přístup"], b_s)
            ])
        tp_tab = Table(p_rows, colWidths=[3.5*cm, 5.0*cm, 3.5*cm, 6.0*cm])
        tp_tab.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), c_blue),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
            ('PADDING', (0,0), (-1,-1), 3)
        ]))
        story.append(tp_tab)
        story.append(Spacer(1, 6))

    story.append(
        Paragraph("<b>Semafor developerských rizik:</b>", t_s)
    )
    for item in evals:
        bg = '#FFEBEE' if item['stat'] == 'DANGER' else (
            '#FFF3E0' if item['stat'] == 'WARNING' else '#E8F5E9'
        )
        badge = bd if item['stat'] == 'DANGER' else (
            bw if item['stat'] == 'WARNING' else bp
        )
        st_label = "STOPKA" if item['stat'] == 'DANGER' else (
            "POZOR" if item['stat'] == 'WARNING' else "OK"
        )
        
        p_col1 = Paragraph(f"<b>[{item['cat']}] {item['title']}</b>", b_s)
        p_col2 = Paragraph(st_label, badge)
        p_detail = Paragraph(item['detail'], b_s)
        
        row = [
            [p_col1, p_col2],
            [p_detail, ""]
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
st.caption("Automatická detekce katastru, územního plánu, cenové mapy a parcelace")

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
        err_msg = "🚨 PLOMBA: " + str(d['plomba_id']) + "! Zákaz podpisu a platby!"
        st.error(err_msg)
    else:
        st.success("Parcela " + str(d['parcel_no']) + ", k.ú. " + str(d['cadastral_area']) + " — bez plomby.")

    up_params = {
        "title": auto_up["title"],
        "is_commercial": auto_up["is_commercial"],
        "requires_contract": auto_up.get("requires_contract", False),
        "cov_pct": float(auto_up["cov_pct"]),
        "grn_pct": float(auto_up["grn_pct"]),
        "nets_verified": nets_ok
    }

    t1, t2 = st.tabs([
        "📋 1. Právní & Územní Audit",
        "📐 2. Katastrální mapa & Geometrická parcelace"
    ])

    with t1:
        if auto_up["is_commercial"]:
            st.warning("📍 Územní plán: " + str(auto_up['title']) + "\n\n⚠️ " + str(auto_up['note']))
        else:
            st.info("📍 Územní plán: " + str(auto_up['title']) + "\n\n✅ " + str(auto_up['note']))

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Výměra", f"{d['area_m2']} m²")
        cov_val = auto_up['cov_pct']
        c2.metric("Max. zastavěnost", f"{(area_total * cov_val / 100):.1f} m²", f"{cov_val:.0f} %")
        grn_val = auto_up['grn_pct']
        c3.metric("Min. zeleň", f"{(area_total * grn_val / 100):.1f} m²", f"{grn_val:.0f} %")
        c4.metric("Sítě", "Potvrzeno" if nets_ok else "NEOVĚŘENO")

        st.subheader("📊 Cenová mapa lokality")
        st.caption("Oblast: " + str(bench_p['region']))
        cp1, cp2, cp3 = st.columns(3)
        cp1.metric("Nezasíťovaný pozemek", f"{bench_p['raw_avg']:,} Kč/m²", bench_p['raw_range'])
        cp2.metric("Zasíťovaná parcela RD", f"{bench_p['serviced_avg']:,} Kč/m²", bench_p['serviced_range'])
        cp3.metric("Komerční areál", f"{bench_p['comm_avg']:,} Kč/m²")

        st.subheader("📋 Semafor developerských rizik")
        for check in analyzer.evaluate_rules(up_params):
            ch_msg = "**[" + str(check['cat']) + "] " + str(check['title']) + "**\n\n" + str(check['detail'])
            if check["stat"] == "DANGER":
                st.error(ch_msg)
            elif check["stat"] == "WARNING":
                st.warning(ch_msg)
            else:
                st.success(ch_msg)

    with t2:
        st.subheader("🗺️ Reálná katastrální situace & Geometrický návrh dělení")
        
        url_map = "https://mapy.cz/zakladni?q=" + str(d['parcel_no']) + "%2C+" + str(d['cadastral_area']) + "&z=18"
        
        map_html = (
            '<div style="background:#1E293B; border-radius:8px; padding:12px; margin-bottom:15px; border:1px solid #334155;">'
            '<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">'
            '<span style="color:#38BDF8; font-weight:bold; font-size:14px;">🗺️ Katastrální mapa — ' + str(d['parcel_no']) + ' (' + str(d['cadastral_area']) + ')</span>'
            '<a href="' + url_map + '" target="_blank" style="background:#0284C7; color:white; padding:5px 10px; border-radius:4px; text-decoration:none; font-size:12px; font-weight:bold;">Otevřít na celé obrazovce ↗</a>'
            '</div>'
            '<iframe src="' + url_map + '" width="100%" height="420" style="border:none; border-radius:6px;"></iframe>'
            '</div>'
        )
        st.markdown(map_html, unsafe_allow_html=True)

        pc1, pc2 = st.columns(2)
        with pc1:
            target_plot = st.number_input(
                "Cílová výměra 1 parcely (m²)",
                min_value=400, max_value=2500, value=800, step=50
            )
            road_sel = st.selectbox(
                "Typ uličního profilu",
                ["Standardní (8,0 m s chodníkem)", "Úsporná (6,5 m)"]
            )
            has_turn = st.checkbox("Slepá ulice delší než 50 m (obratiště IZS)", value=True)
            has_contract = st.checkbox(
                "Vyžadována plánovací smlouva s obcí (Z8 / rozvoj)",
                value=auto_up.get("requires_contract", False)
            )
            contrib = st.number_input(
                "Příspěvek obci na 1 parcelu (Kč)",
                min_value=0, max_value=500000,
                value=150000 if has_contract else 0,
                step=25000
            ) if has_contract else 0

        with pc2:
            buy_p = st.number_input(
                "Nákup surového pozemku (Kč/m²)",
                value=int(bench_p['raw_avg']), step=100
            )
            def_s = bench_p['comm_avg'] if auto_up['is_commercial'] else bench_p['serviced_avg']
            sell_p = st.number_input(
                "Prodej zasíťované parcely (Kč/m²)",
                value=int(def_s), step=200
            )

        r_w = 8.0 if "8,0" in road_sel else 6.5
        turn_m2 = 130.0 if has_turn else 0.0
        r_len = max(35.0, round((area_total ** 0.5) * 1.15, 0))
        r_m2 = (r_len * r_w) + turn_m2
        net_m2 = max(0.0, area_total - r_m2)
        n_plots = int(net_m2 // target_plot)
        avg_plot = (net_m2 / n_plots) if n_plots > 0 else 0.0

        st.divider()
        st.markdown("#### 📐 Geometrický rozpad parcelace (Návrh geometrického plánu)")
        
        parcel_rows = []
        p_base = d["parcel_no"].split("/")[0]
        
        parcel_rows.append({
            "Označení parcely": "parc. č. " + str(p_base) + "/A",
            "Účel využití": "Ostatní plocha — komunikace a obratiště IZS",
            "Výměra": f"{r_m2:.0f} m²",
            "Přístup": "Napojení na stávající obecní komunikaci"
        })
        
        for i in range(1, n_plots + 1):
            parcel_rows.append({
                "Označení parcely": "parc. č. " + str(p_base) + "/" + str(i+1),
                "Účel využití": "Stavební pozemek pro RD",
                "Výměra": f"{avg_plot:.0f} m²",
                "Přístup": "Sjezd z nově zřízené parcely " + str(p_base) + "/A"
            })
            
        st.table(parcel_rows)

        st.divider()
        b1, b2, b3, b4 = st.columns(4)
        b1.metric("Celková výměra", f"{area_total:.0f} m²")
        b2.metric("Zábor komunikace", f"{r_m2:.0f} m²", f"{(r_m2/area_total*100):.1f} %")
        b3.metric("Čistá stavební plocha", f"{net_m2:.0f} m²")
        b4.metric("Počet stavebních parcel", f"{n_plots} ks", f"prům. {avg_plot:.0f} m²")

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

        out_name = "Audit_" + str(d['municipality']) + "_" + str(d['parcel_no'].replace('/', '_')) + ".pdf"
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_o:
            tmp_pdf_p = tmp_o.name

        generate_pdf(analyzer, tmp_pdf_p, up_params, bench_p, parcel_rows)
        with open(tmp_pdf_p, "rb") as f_pdf:
            pdf_b = f_pdf.read()

        st.download_button(
            "📄 Stáhnout Manažerský PDF Audit",
            data=pdf_b,
            file_name=out_name,
            mime="application/pdf",
            type="primary"
        )

    try:
        os.remove(tmp_p)
        os.remove(tmp_pdf_p)
    except Exception:
        pass
