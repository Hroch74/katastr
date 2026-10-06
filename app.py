import streamlit as st
import streamlit.components.v1 as components
import tempfile, os, re, urllib.parse
import pypdf

st.set_page_config(page_title="ParcelCheck AI", page_icon="🏛️", layout="wide")
st.title("🏛️ ParcelCheck AI — Katastrální prověrka & Due Diligence")

class Analyzer:
    def __init__(self, raw):
        self.raw = raw
        pm = re.search(r"Objekt je dotčen změnou právního vztahu:\s*([^;\n\r]+)", raw)
        self.data = {
            "parcel_no": self._ex(r"Parcelní číslo:\s*([0-9]+(?:/[0-9]+)?)"),
            "municipality": self._ex(r"Obec:\s*([^\n\r\[]+)"),
            "cadastral_area": self._ex(r"Katastrální území:\s*([^\n\r\[]+)"),
            "area_m2": self._ex(r"Výměra \[m2\]:\s*([0-9\s]+)").replace(" ", ""),
            "land_type": self._ex(r"Druh pozemku:\s*([^\n\r]+)"),
            "has_plomba": bool(pm),
            "plomba_id": pm.group(1).strip() if pm else ""
        }
    def _ex(self, pat):
        m = re.search(pat, self.raw)
        return m.group(1).strip() if m else ""

input_mode = st.radio("Způsob zadání:", ["📄 Nahrát PDF z KN", "✍️ Zadat ručně"], horizontal=True)

parcel_data = None

if input_mode == "📄 Nahrát PDF z KN":
    up_pdf = st.file_uploader("Nahrajte PDF výpisu z Nahlížení do KN", type=["pdf"])
    if up_pdf is not None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(up_pdf.read())
            tmp_p = tmp.name
        try:
            reader = pypdf.PdfReader(tmp_p)
            text = "".join([p.extract_text() or "" for p in reader.pages])
            parcel_data = Analyzer(text).data
        finally:
            try:
                os.remove(tmp_p)
            except Exception:
                pass
else:
    with st.form("f_manual"):
        c1, c2, c3 = st.columns(3)
        p_num = c1.text_input("Parcelní číslo", value="841/4")
        p_ku = c2.text_input("Obec nebo k.ú.", value="Kozojedy")
        p_area = c3.number_input("Výměra m2", min_value=0, max_value=5000000, value=0, step=100)
        if st.form_submit_button("Prověřit pozemek", type="primary"):
            if p_num and p_ku:
                parcel_data = {
                    "parcel_no": p_num.strip(),
                    "municipality": p_ku.strip(),
                    "cadastral_area": p_ku.strip(),
                    "area_m2": str(p_area) if p_area > 0 else "",
                    "land_type": "orná půda / neurčeno",
                    "has_plomba": False,
                    "plomba_id": ""
                }

if parcel_data:
    p_num = parcel_data.get("parcel_no", "")
    p_ku = parcel_data.get("cadastral_area", "") or parcel_data.get("municipality", "")
    p_type = parcel_data.get("land_type", "orná půda")
    
    try:
        area_val = float(parcel_data.get("area_m2", 0))
    except Exception:
        area_val = 0.0

    st.divider()
    c_m1, c_m2, c_m3, c_m4 = st.columns(4)
    c_m1.metric("Parcela", p_num)
    c_m2.metric("Území", p_ku)
    c_m3.metric("Výměra", f"{area_val:.0f} m2" if area_val > 0 else "Nezadáno")
    c_m4.metric("Druh", p_type)

    if parcel_data.get("has_plomba"):
        st.error("POZOR PLOMBA: " + str(parcel_data.get("plomba_id")))

    q_mapy = urllib.parse.quote(f"{p_num} {p_ku}")
    url_m = f"https://mapy.cz/zakladni?q={q_mapy}&z=17"
    url_c = "https://nahlizenidokn.cuzk.cz/"
    
    b1, b2 = st.columns(2)
    b1.link_button("Otevřít na Mapy.cz (Katastr)", url_m, use_container_width=True)
    b2.link_button("Otevřít Nahlížení ČÚZK", url_c, use_container_width=True)

    is_field = any(w in p_type.lower() for w in ["orná", "pole", "les", "travní", "zahrada"])
    z_type = st.radio("Status v Územním plánu:", ["Nestavební (pole/les/NZ)", "Zastavitelná plocha (RD)"], index=0 if is_field else 1)
    is_buildable = (z_type == "Zastavitelná plocha (RD)")

    st.subheader(f"Katastrální mapa ČÚZK: {p_ku}")
    m_code = f"""
    <!DOCTYPE html><html><head>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>body{{margin:0;background:#0f172a;}} #m{{width:100%;height:480px;}}</style>
    </head><body><div id="m"></div><script>
        var map = L.map('m').setView([49.8175, 15.4730], 8);
        var orto = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}', {{maxZoom:20}}).addTo(map);
        var cuzk = L.tileLayer.wms('https://services.cuzk.gov.cz/wms/local-km-wms.asp', {{layers:'KN',format:'image/png',transparent:true,version:'1.3.0',crs:L.CRS.EPSG3857}}).addTo(map);
        L.control.layers({{"Letecký":orto}}, {{"Katastr ČÚZK":cuzk}}, {{position:'topright'}}).addTo(map);
        fetch("https://nominatim.openstreetmap.org/search?format=json&q=" + encodeURIComponent("{p_ku}, ČR"))
            .then(r=>r.json()).then(d=>{{
                if(d && d.length>0) {{
                    map.setView([parseFloat(d[0].lat), parseFloat(d[0].lon)], 16);
                    L.marker([parseFloat(d[0].lat), parseFloat(d[0].lon)]).addTo(map).bindPopup("<b>{p_ku}</b><br>Parcela {p_num}").openPopup();
                }}
            }});
    </script></body></html>
    """
    components.html(m_code, height=500)

    if not is_buildable:
        st.error(f"Pozemek {p_num} je nestavební orná půda / pole. Zákaz výstavby RD.")
        if area_val > 0:
            a1, a2 = st.columns(2)
            a1.metric("Cena orné půdy (cca 55 Kč/m2)", f"{area_val * 55:,.0f} Kč")
            a2.metric("Spekulativní výhled (cca 450 Kč/m2)", f"{area_val * 450:,.0f} Kč")
    else:
        st.success(f"Zastavitelná plocha: Parcela {p_num} určena k zástavbě RD.")
        if area_val > 0:
            t_plot = st.number_input("Cílová výměra 1 parcely (m2)", value=800, step=50)
            road_m2 = area_val * 0.15
            net_m2 = area_val - road_m2
            n_plots = int(net_m2 // t_plot) if t_plot > 0 else 0
            
            k1, k2, k3 = st.columns(3)
            k1.metric("Plocha cest (15%)", f"{road_m2:.0f} m2")
            k2.metric("Čisté stavební parcely", f"{net_m2:.0f} m2")
            k3.metric("Počet parcel", f"{n_plots} ks")
else:
    st.info("Nahrajte PDF nebo zadejte parcelu a obec nahore.")