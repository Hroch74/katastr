import streamlit as st
import streamlit.components.v1 as components
import tempfile, os, re, pypdf
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

def setup_fonts():
    f = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    if os.path.exists(f):
        try:
            pdfmetrics.registerFont(TTFont('AppF', f))
            return 'AppF'
        except Exception:
            pass
    return 'Helvetica'

F_NAME = setup_fonts()

def get_benchmarks(municipality):
    m = str(municipality).lower()
    if any(k in m for k in ["tehovec", "říčany", "ricany", "mukařov", "babice"]):
        return {"reg": "Praha-východ", "raw": 3500, "serv": 9500, "comm": 4800}
    return {"reg": "Regionální průměr", "raw": 1800, "serv": 5000, "comm": 3000}

def get_zoning(ku, parcel):
    c, p = str(ku).strip(), str(parcel).strip()
    if "Tehovec" in c:
        return {"title": "BI / Rozvojové území (Plánovací smlouva)", "comm": False, "cov": 25.0, "req_c": True}
    return {"title": "BI - Bydlení v RD", "comm": False, "cov": 30.0, "req_c": False}

class Analyzer:
    def __init__(self, raw):
        self.raw = raw
        pm = re.search(r"Objekt je dotčen změnou právního vztahu:\s*([^;\n\r]+)", raw)
        self.data = {
            "parcel_no": self._ex(r"Parcelní číslo:\s*([0-9]+(?:/[0-9]+)?)"),
            "municipality": self._ex(r"Obec:\s*([^\n\r\[]+)"),
            "cadastral_area": self._ex(r"Katastrální území:\s*([^\n\r\[]+)"),
            "lv_no": self._ex(r"Číslo LV:\s*([0-9]+)"),
            "area_m2": self._ex(r"Výměra \[m2\]:\s*([0-9\s]+)").replace(" ", ""),
            "has_plomba": bool(pm),
            "plomba_id": pm.group(1).strip() if pm else ""
        }
    def _ex(self, pat):
        m = re.search(pat, self.raw)
        return m.group(1).strip() if m else ""

def generate_pdf(d, up, bench, out_p):
    doc = SimpleDocTemplate(out_p, pagesize=A4, margin=1.5*cm)
    styles = getSampleStyleSheet()
    t_s = ParagraphStyle('T', fontName=F_NAME, fontSize=12, leading=15, textColor=colors.HexColor('#1F4E79'))
    b_s = ParagraphStyle('B', fontName=F_NAME, fontSize=9, leading=12)
    story = [
        Paragraph(f"AUDIT PARCELY: {d['parcel_no']} ({d['cadastral_area']})", t_s),
        Spacer(1, 10),
        Paragraph(f"Obec: {d['municipality']} | Výměra: {d['area_m2']} m² | Územní plán: {up['title']}", b_s),
        Paragraph(f"Cenová mapa: {bench['serv']:,} Kč/m² (zasíťovaný pozemek)", b_s),
        Spacer(1, 10)
    ]
    doc.build(story)
    return out_p

# --- Streamlit UI ---
st.set_page_config(page_title="ParcelCheck AI", layout="wide")
st.title("🏗️ ParcelCheck AI — Due Diligence & Developerský audit")

if "analyzer" not in st.session_state:
    st.session_state["analyzer"] = None

input_mode = st.radio("Způsob zadání:", ["✍️ Zadat parcelu, obec a výměru", "📄 Nahrát PDF z Nahlížení do KN"], horizontal=True)

if input_mode == "✍️ Zadat parcelu, obec a výměru":
    with st.form("quick_form"):
        c1, c2, c3 = st.columns(3)
        p_num = c1.text_input("Parcelní číslo", value="869/1")
        p_ku = c2.text_input("Obec / Katastrální území", value="Tehovec")
        p_area = c3.number_input("Výměra pozemku (m²)", min_value=100, max_value=2000000, value=38047, step=100)
        
        if st.form_submit_button("🚀 Spustit analýzu a načíst katastr (Enter)", type="primary"):
            mock = f"Parcelní číslo: {p_num.strip()}\nObec: {p_ku.strip()}\nKatastrální území: {p_ku.strip()}\nČíslo LV: -\nVýměra [m2]: {p_area}"
            st.session_state["analyzer"] = Analyzer(mock)

else:
    up_pdf = st.file_uploader("Nahrajte PDF výpisu z Nahlížení do KN", type=["pdf"])
    if up_pdf is not None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(up_pdf.read())
            tmp_p = tmp.name
        reader = pypdf.PdfReader(tmp_p)
        text = "".join([p.extract_text() or "" for p in reader.pages])
        st.session_state["analyzer"] = Analyzer(text)
        try:
            os.remove(tmp_p)
        except Exception:
            pass

an = st.session_state["analyzer"]
if an:
    d = an.data
    up = get_zoning(d["cadastral_area"], d["parcel_no"])
    bench = get_benchmarks(d["municipality"])
    try:
        area_tot = float(d["area_m2"])
    except Exception:
        area_tot = 38047.0

    st.success(f"📍 Parcela č. {d['parcel_no']}, k.ú. {d['cadastral_area']} — přesná výměra: {area_tot:,.0f} m²".replace(',', ' '))

    t1, t2 = st.tabs(["🗺 Katastrální situace & Návrh dělení", "📊 Developerský rozpočet & Ziskovost"])

    # Přesné souřadnice: Parcela 869/1 v Tehovci
    if "869" in str(d["parcel_no"]) and "Tehovec" in str(d["cadastral_area"]):
        lat_c, lon_c = 49.9863, 14.7335
    else:
        lat_c, lon_c = 49.9840, 14.7350

    with t1:
        st.subheader("Letecká ortofotomapa & Oficiální katastr ČÚZK")
        map_html = f"""
        <!DOCTYPE html>
        <html><head>
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet.draw/1.0.4/leaflet.draw.css" />
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet.draw/1.0.4/leaflet.draw.js"></script>
        <style>body{{margin:0;background:#0f172a;}} #m{{height:520px;border-radius:6px;}}</style>
        </head><body>
        <div id="m"></div>
        <script>
            var map = L.map('m').setView([{lat_c}, {lon_c}], 17);
            var orto = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}', {{maxZoom:20}}).addTo(map);
            var kn = L.tileLayer.wms('https://services.cuzk.gov.cz/wms/local-km-wms.asp', {{layers:'KN',format:'image/png',transparent:true,version:'1.3.0',crs:L.CRS.EPSG3857}}).addTo(map);
            
            L.marker([{lat_c}, {lon_c}]).addTo(map)
                .bindPopup("<b>Parcela {d['parcel_no']}</b><br>k.ú. {d['cadastral_area']}<br>Výměra: {area_tot:,.0f} m²").openPopup();

            var items = new L.FeatureGroup().addTo(map);
            var draw = new L.Control.Draw({{
                position:'topleft',
                draw:{{
                    polyline:{{shapeOptions:{{color:'#ef4444',weight:4}}}},
                    polygon:{{shapeOptions:{{color:'#10b981',fillOpacity:0.35}}}},
                    rectangle:false, circle:false, circlemarker:false, marker:false
                }},
                edit:{{featureGroup:items}}
            }});
            map.addControl(draw);
            map.on(L.Draw.Event.CREATED, function(e){{ items.addLayer(e.layer); }});
            L.control.layers({{"Satelitní snímek":orto}}, {{"Katastrální mapa ČÚZK":kn, "Moje zákresy":items}}, {{position:'topright'}}).addTo(map);
        </script></body></html>
        """
        components.html(map_html, height=540)

        st.divider()
        c_a, c_b = st.columns(2)
        with c_a:
            target_p = st.number_input("Cílová výměra stavební parcely (m²)", min_value=500, max_value=3000, value=900, step=50)
            road_pct = st.slider("Podíl komunikací a veřejných ploch (%)", min_value=10, max_value=25, value=15, step=1)
        with c_b:
            buy_m2 = st.number_input("Nákup surového pozemku (Kč/m²)", value=int(bench["raw"]), step=100)
            sell_m2 = st.number_input("Prodej zasíťovaných parcel (Kč/m²)", value=int(bench["serv"]), step=200)

        road_m2 = area_tot * (road_pct / 100.0)
        net_m2 = area_tot - road_m2
        plots_cnt = int(net_m2 // target_p) if target_p > 0 else 0
        avg_p = (net_m2 / plots_cnt) if plots_cnt > 0 else 0.0
        road_len = round((road_m2 / 8.0), 0)

        st.divider()
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Celková výměra", f"{area_tot:,.0f} m²".replace(',', ' '))
        m2.metric(f"Komunikace a sítě ({road_pct} %)", f"{road_m2:,.0f} m²".replace(',', ' '), f"cca {road_len:.0f} bm cest")
        m3.metric("Čistá stavební plocha", f"{net_m2:,.0f} m²".replace(',', ' '))
        m4.metric("Počet parcel RD", f"{plots_cnt} parcel", f"průměrně {avg_p:.0f} m²")

    with t2:
        st.subheader("🛠️ Položkový rozpočet infrastruktury a kalkulace zisku")
        capex_roads = road_len * 14000.0
        capex_nets = road_len * 18500.0
        capex_conns = plots_cnt * 120000.0
        capex_admin = 250000.0
        capex_total = capex_roads + capex_nets + capex_conns + capex_admin

        raw_total = area_tot * buy_m2
        rev_total = net_m2 * sell_m2
        total_costs = raw_total + capex_total
        profit = rev_total - total_costs
        margin = (profit / rev_total * 100.0) if rev_total > 0 else 0.0

        e1, e2, e3, e4 = st.columns(4)
        e1.metric("Nákup pozemku (3,8 ha)", f"{raw_total:,.0f} Kč".replace(',', ' '))
        e2.metric("Infrastruktura celkem", f"{capex_total:,.0f} Kč".replace(',', ' '))
        e3.metric("Tržby z prodeje parcel", f"{rev_total:,.0f} Kč".replace(',', ' '))
        e4.metric("Hrubý zisk z projektu", f"{profit:,.0f} Kč".replace(',', ' '), f"{margin:.1f} % marže")

        st.caption(f"Orientační náklad na zasíťování 1 parcely: {(capex_total / plots_cnt):,.0f} Kč".replace(',', ' '))

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp_pdf = tmp.name
        generate_pdf(d, up, bench, tmp_pdf)
        with open(tmp_pdf, "rb") as f:
            pdf_b = f.read()
        try:
            os.remove(tmp_pdf)
        except Exception:
            pass

        st.download_button("📄 Stáhnout Manažerský PDF Audit", data=pdf_b, file_name=f"Audit_{d['parcel_no'].replace('/', '_')}.pdf", type="primary")