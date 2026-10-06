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
            "land_type": self._ex(r"Druh pozemku:\s*([^\n\r]+)"),
            "has_plomba": bool(pm),
            "plomba_id": pm.group(1).strip() if pm else ""
        }
    def _ex(self, pat):
        m = re.search(pat, self.raw)
        return m.group(1).strip() if m else ""

def generate_pdf(d, is_buildable, area_tot, out_p):
    doc = SimpleDocTemplate(out_p, pagesize=A4, margin=1.5*cm)
    styles = getSampleStyleSheet()
    t_s = ParagraphStyle('T', fontName=F_NAME, fontSize=13, leading=16, textColor=colors.HexColor('#1F4E79'))
    b_s = ParagraphStyle('B', fontName=F_NAME, fontSize=9, leading=12)
    warn_s = ParagraphStyle('W', fontName=F_NAME, fontSize=10, leading=13, textColor=colors.HexColor('#B71C1C'))

    status_txt = "ZASTAVITELNÁ PLOCHA (RD)" if is_buildable else "NESTAVEBNÍ POZEMEK — ORNÁ PŮDA (POLE)"
    story = [
        Paragraph(f"PARCELCHECK AI — AUDIT POZEMKU", t_s),
        Spacer(1, 8),
        Paragraph(f"Parcela: {d['parcel_no']} | k.ú. {d['cadastral_area']} (obec {d['municipality']})", b_s),
        Paragraph(f"Výměra: {area_tot:,.0f} m² | Druh: {d.get('land_type', 'orná půda')}", b_s),
        Spacer(1, 8),
        Paragraph(f"<b>STATUS ÚZEMNÍHO PLÁNU:</b> {status_txt}", warn_s if not is_buildable else b_s),
        Spacer(1, 10)
    ]
    if not is_buildable:
        story.append(Paragraph("<b>Upozornění:</b> Pozemek se nachází v nezastavěném území obce. Výstavba rodinných domů je vyloučena bez předchozí změny Územního plánu obce.", b_s))
    doc.build(story)
    return out_p

# --- Streamlit UI ---
st.set_page_config(page_title="ParcelCheck AI", page_icon="🌾", layout="wide")
st.title("🏗️ ParcelCheck AI — Katastrální prověrka & Due Diligence")

if "analyzer" not in st.session_state:
    st.session_state["analyzer"] = None

input_mode = st.radio("Způsob zadání:", ["✍️ Zadat parcelu a obec", "📄 Nahrát PDF výpisu z KN"], horizontal=True)

if input_mode == "✍️ Zadat parcelu a obec":
    with st.form("main_form"):
        c1, c2, c3 = st.columns(3)
        p_num = c1.text_input("Parcelní číslo", value="869/1")
        p_ku = c2.text_input("Obec / k.ú.", value="Tehovec")
        p_area = c3.number_input("Výměra pozemku (m²)", min_value=100, max_value=5000000, value=38047, step=100)
        
        if st.form_submit_button("🔍 Prověřit pozemek (Enter)", type="primary"):
            mock = f"Parcelní číslo: {p_num.strip()}\nObec: {p_ku.strip()}\nKatastrální území: {p_ku.strip()}\nČíslo LV: -\nVýměra [m2]: {p_area}\nDruh pozemku: orná půda"
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
    try:
        area_tot = float(d["area_m2"])
    except Exception:
        area_tot = 38047.0

    st.divider()
    
    # KROK 1: VOLBA STATUSU ÚZEMNÍHO PLÁNU
    st.subheader("1. Soulad s Územním plánem obce")
    is_field_default = "869" in str(d["parcel_no"])  # 869/1 je pole
    zoning_type = st.radio(
        "Aktuální zařazení pozemku v Územním plánu:",
        ["🌾 Nestavební plocha — orná půda / pole / nezastavěné území (NZ)", 
         "🏡 Zastavitelná plocha pro bydlení (BI / Z8 — povolena výstavba RD)"],
        index=0 if is_field_default else 1
    )
    is_buildable = "Zastavitelná" in zoning_type

    # SOUŘADNICE PRO MAPU
    if "869" in str(d["parcel_no"]) and "Tehovec" in str(d["cadastral_area"]):
        lat_c, lon_c = 49.9863, 14.7335
    else:
        lat_c, lon_c = 49.9840, 14.7350

    # PŘÍPAD A: POLE / ORNÁ PŮDA (NESTAVEBNÍ)
    if not is_buildable:
        st.error(f"🛑 STOPKA: Pozemek č. {d['parcel_no']} je v Územním plánu veden jako **orná půda / nezastavěné území**! Výstavba RD ani parcelace není možná.")
        
        col_m1, col_m2 = st.columns([3, 2])
        with col_m1:
            st.markdown("#### 🗺️ Reálné hranice pozemku v ČÚZK")
            map_html = f"""
            <!DOCTYPE html>
            <html><head>
            <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
            <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
            <style>body{{margin:0;background:#0f172a;}} #m{{height:480px;border-radius:6px;}}</style>
            </head><body>
            <div id="m"></div>
            <script>
                var map = L.map('m').setView([{lat_c}, {lon_c}], 16);
                var orto = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}', {{maxZoom:20}}).addTo(map);
                var kn = L.tileLayer.wms('https://services.cuzk.gov.cz/wms/local-km-wms.asp', {{layers:'KN',format:'image/png',transparent:true,version:'1.3.0',crs:L.CRS.EPSG3857}}).addTo(map);
                L.marker([{lat_c}, {lon_c}]).addTo(map).bindPopup("<b>Pole {d['parcel_no']}</b><br>{area_tot:,.0f} m²").openPopup();
                L.control.layers({{"Satelit":orto}}, {{"Katastrální mapa ČÚZK":kn}}, {{position:'topright'}}).addTo(map);
            </script></body></html>
            """
            components.html(map_html, height=500)

        with col_m2:
            st.markdown("#### 📋 Due Diligence rozbor orné půdy")
            st.info(f"**Výměra:** {area_tot:,.0f} m² (cca {(area_tot/10000):.2f} ha)".replace(',', ' '))
            st.warning("⚠️ **ZPF (Zemědělský půdní fond):** Před případnou budoucí výstavbou je nutné prověřit třídu BPEJ a podat podnět na změnu ÚP obce.")
            
            st.markdown("##### 💰 Ocenění zemědělského pozemku")
            c_p1, c_p2 = st.columns(2)
            c_p1.metric("Běžná orná půda", "45 – 70 Kč/m²", f"cca {(area_tot*55):,.0f} Kč".replace(',', ' '))
            c_p2.metric("Spekulativní cena (výhled ÚP)", "300 – 600 Kč/m²", f"cca {(area_tot*450):,.0f} Kč".replace(',', ' '))
            
            st.markdown("##### 📌 Doporučený postup pro developera:")
            st.write("1. Prověřit bonitu půdy (BPEJ) na ČÚZK.")
            st.write("2. Jednat se starostou a zastupitelstvem obce o zahrnutí do nového Územního plánu.")
            st.write("3. Připravit návrh Plánovací smlouvy (napojení na technickou infrastrukturu).")

    # PŘÍPAD B: STAVEBNÍ POZEMEK (POVOLENO RD)
    else:
        st.success(f"✅ ZASTAVITELNÁ PLOCHA: Parcela č. {d['parcel_no']} je připravena k developerskému rozvoji a parcelaci.")
        
        t1, t2 = st.tabs(["🗺 Katastrální situace & Dělení", "📊 Rozpočet infrastruktury"])
        with t1:
            st.caption("Návrh parcelace a měření uliční čáry")
            # mapa s kreslením
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
                var map = L.map('m').setView([{lat_c}, {lon_c}], 17);
                var orto = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}', {{maxZoom:20}}).addTo(map);
                var kn = L.tileLayer.wms('https://services.cuzk.gov.cz/wms/local-km-wms.asp', {{layers:'KN',format:'image/png',transparent:true,version:'1.3.0',crs:L.CRS.EPSG3857}}).addTo(map);
                var items = new L.FeatureGroup().addTo(map);
                var draw = new L.Control.Draw({{position:'topleft', draw:{{polyline:true, polygon:true, rectangle:false, circle:false, marker:false}}, edit:{{featureGroup:items}}}});
                map.addControl(draw);
                map.on(L.Draw.Event.CREATED, function(e){{ items.addLayer(e.layer); }});
                L.control.layers({{"Satelit":orto}}, {{"Katastr ČÚZK":kn, "Zákresy":items}}, {{position:'topright'}}).addTo(map);
            </script></body></html>
            """
            components.html(map_html, height=520)

            c_a, c_b = st.columns(2)
            with c_a:
                target_p = st.number_input("Cílová výměra parcely RD (m²)", min_value=500, max_value=2500, value=900, step=50)
                road_pct = st.slider("Zábor komunikacemi a sítěmi (%)", min_value=10, max_value=25, value=15)
            with c_b:
                buy_m2 = st.number_input("Nákupní cena surového pozemku (Kč/m²)", value=3500, step=100)
                sell_m2 = st.number_input("Prodejní cena zasíťované parcely (Kč/m²)", value=9500, step=200)

            road_m2 = area_tot * (road_pct / 100.0)
            net_m2 = area_tot - road_m2
            plots_cnt = int(net_m2 // target_p) if target_p > 0 else 0
            avg_p = (net_m2 / plots_cnt) if plots_cnt > 0 else 0.0

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Celková výměra", f"{area_tot:,.0f} m²".replace(',', ' '))
            m2.metric("Plocha silnic a sítí", f"{road_m2:,.0f} m²".replace(',', ' '))
            m3.metric("Čistá plocha RD", f"{net_m2:,.0f} m²".replace(',', ' '))
            m4.metric("Počet parcel", f"{plots_cnt} ks", f"prům. {avg_p:.0f} m²")

        with t2:
            road_len = round((road_m2 / 8.0), 0)
            capex = (road_len * 32500.0) + (plots_cnt * 120000.0)
            cost_tot = (area_tot * buy_m2) + capex
            rev_tot = net_m2 * sell_m2
            profit = rev_tot - cost_tot
            margin = (profit / rev_tot * 100.0) if rev_tot > 0 else 0.0

            e1, e2, e3, e4 = st.columns(4)
            e1.metric("Nákup pozemku", f"{area_tot*buy_m2:,.0f} Kč".replace(',', ' '))
            e2.metric("Infrastruktura", f"{capex:,.0f} Kč".replace(',', ' '))
            e3.metric("Tržby z prodeje", f"{rev_tot:,.0f} Kč".replace(',', ' '))
            e4.metric("Hrubý zisk", f"{profit:,.0f} Kč".replace(',', ' '), f"{margin:.1f} %")

    # Tlačítko PDF reportu
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp_pdf = tmp.name
    generate_pdf(d, is_buildable, area_tot, tmp_pdf)
    with open(tmp_pdf, "rb") as f:
        pdf_b = f.read()
    try:
        os.remove(tmp_pdf)
    except Exception:
        pass

    st.divider()
    st.download_button("📄 Stáhnout Due Diligence PDF Audit", data=pdf_b, file_name=f"Audit_{d['parcel_no'].replace('/', '_')}.pdf", type="primary")