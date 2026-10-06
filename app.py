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
    if "Tehovec" in c and ("877" in p or "850" in p):
        return {"title": "BI / Z8 (Plánovací smlouva)", "comm": False, "cov": 30.0, "req_c": True}
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

with st.form("quick_form"):
    c1, c2 = st.columns(2)
    p_num = c1.text_input("Parcelní číslo", value="850/1")
    p_ku = c2.text_input("Obec nebo katastrální území", value="Tehovec")
    if st.form_submit_button("🚀 Prověřit pozemek a načíst mapu (Enter)", type="primary"):
        mock = f"Parcelní číslo: {p_num}\nObec: {p_ku}\nKatastrální území: {p_ku}\nČíslo LV: -\nVýměra [m2]: 2500"
        st.session_state["analyzer"] = Analyzer(mock)

an = st.session_state["analyzer"]
if an:
    d = an.data
    up = get_zoning(d["cadastral_area"], d["parcel_no"])
    bench = get_benchmarks(d["municipality"])
    area_tot = float(d["area_m2"]) if d["area_m2"] else 2500.0

    st.success(f"Pozemek č. {d['parcel_no']}, k.ú. {d['cadastral_area']} — připraven k parcelaci")

    t1, t2 = st.tabs(["🗺 Katastrální mapa & Návrh dělení", "📊 Ekonomika & PDF"])

    with t1:
        st.subheader("Letecká ortofotomapa & Katastr nemovitostí ČÚZK")
        map_html = f"""
        <!DOCTYPE html>
        <html><head>
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet.draw/1.0.4/leaflet.draw.css" />
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet.draw/1.0.4/leaflet.draw.js"></script>
        <style>body{{margin:0;background:#0f172a;}} #m{{height:500px;border-radius:6px;}}</style>
        </head><body>
        <div id="m"></div>
        <script>
            var map = L.map('m').setView([49.9840, 14.7350], 17);
            var orto = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}', {{maxZoom:20}}).addTo(map);
            var kn = L.tileLayer.wms('https://services.cuzk.gov.cz/wms/local-km-wms.asp', {{layers:'KN',format:'image/png',transparent:true,version:'1.3.0',crs:L.CRS.EPSG3857}}).addTo(map);
            var items = new L.FeatureGroup().addTo(map);
            var draw = new L.Control.Draw({{
                position:'topleft',
                draw:{{polyline:{{shapeOptions:{{color:'#ef4444',weight:4}}}}, polygon:{{shapeOptions:{{color:'#10b981',fillOpacity:0.35}}}}, rectangle:false, circle:false, circlemarker:false, marker:false}},
                edit:{{featureGroup:items}}
            }});
            map.addControl(draw);
            map.on(L.Draw.Event.CREATED, function(e){{ items.addLayer(e.layer); }});
            L.control.layers({{"Satelit":orto}}, {{"Katastr ČÚZK":kn, "Moje dělení":items}}, {{position:'topright'}}).addTo(map);
            fetch("https://nominatim.openstreetmap.org/search?format=json&q=" + encodeURIComponent("{d['parcel_no']}, {d['cadastral_area']}, ČR"))
                .then(r=>r.json()).then(res=>{{
                    if(res && res.length>0) map.setView([res[0].lat, res[0].lon], 18);
                    else fetch("https://nominatim.openstreetmap.org/search?format=json&q=" + encodeURIComponent("{d['cadastral_area']}, ČR"))
                        .then(r=>r.json()).then(r2=>{{ if(r2 && r2.length>0) map.setView([r2[0].lat, r2[0].lon], 16); }});
                }});
        </script></body></html>
        """
        components.html(map_html, height=520)

        c_a, c_b = st.columns(2)
        with c_a:
            target_p = st.number_input("Cílová výměra 1 parcely (m²)", min_value=400, max_value=2500, value=800, step=50)
            road_w = st.selectbox("Šířka ulice", [8.0, 6.5], index=0)
        with c_b:
            buy_m2 = st.number_input("Nákup pozemku (Kč/m²)", value=int(bench["raw"]), step=100)
            sell_m2 = st.number_input("Prodej zasíťované parcely (Kč/m²)", value=int(bench["serv"]), step=200)

        road_len = max(35.0, round((area_tot ** 0.5) * 1.15, 0))
        road_m2 = (road_len * road_w) + 130.0
        net_m2 = max(0.0, area_tot - road_m2)
        plots_cnt = int(net_m2 // target_p) if target_p > 0 else 0
        avg_p = (net_m2 / plots_cnt) if plots_cnt > 0 else 0.0

        st.divider()
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Celková plocha", f"{area_tot:.0f} m²")
        m2.metric("Plocha silnice", f"{road_m2:.0f} m²")
        m3.metric("Čisté parcely", f"{net_m2:.0f} m²")
        m4.metric("Počet parcel", f"{plots_cnt} ks", f"prům. {avg_p:.0f} m²")

    with t2:
        capex = road_len * (14000.0 if road_w == 8.0 else 11000.0) + (road_len * 19900.0) + (plots_cnt * 110000.0)
        cost_tot = (area_tot * buy_m2) + capex
        rev_tot = net_m2 * sell_m2
        profit = rev_tot - cost_tot
        margin = (profit / rev_tot * 100.0) if rev_tot > 0 else 0.0

        e1, e2, e3, e4 = st.columns(4)
        e1.metric("Nákup surového pozemku", f"{area_tot * buy_m2:,.0f} Kč".replace(',', ' '))
        e2.metric("Infrastruktura a sítě", f"{capex:,.0f} Kč".replace(',', ' '))
        e3.metric("Tržby z parcel", f"{rev_tot:,.0f} Kč".replace(',', ' '))
        e4.metric("Zisk projektu", f"{profit:,.0f} Kč".replace(',', ' '), f"{margin:.1f} % marže")

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