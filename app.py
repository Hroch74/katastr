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

input_mode = st.radio("Způsob zadání:", ["📄 Nahrát PDF z Nahlížení do KN (načte výměru samo)", "✍️ Zadat ručně"], horizontal=True)

parcel_data = None

if input_mode == "📄 Nahrát PDF z Nahlížení do KN (načte výměru samo)":
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
        p_num = c1.text_input("Parcelní číslo", placeholder="např. 841/4")
        p_ku = c2.text_input("Obec nebo katastrální území", placeholder="např. Kozojedy")
        p_area = c3.number_input("Výměra v m² (pokud ji znáte z hlavy)", min_value=0, max_value=10000000, value=0, step=50)
        
        if st.form_submit_button("Prověřit pozemek", type="primary"):
            if p_num.strip() and p_ku.strip():
                parcel_data = {
                    "parcel_no": p_num.strip(),
                    "municipality": p_ku.strip(),
                    "cadastral_area": p_ku.strip(),
                    "area_m2": str(p_area) if p_area > 0 else "",
                    "land_type": "orná půda / neurčeno",
                    "has_plomba": False,
                    "plomba_id": ""
                }
            else:
                st.warning("Zadejte prosím číslo parcely i obec.")

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
    c_m2.metric("Území / Obec", p_ku)
    c_m3.metric("Výměra dle KN", f"{area_val:,.0f} m²".replace(',', ' ') if area_val > 0 else "Čeká na zadání")
    c_m4.metric("Druh v KN", p_type)

    if parcel_data.get("has_plomba"):
        st.error("POZOR PLOMBA: " + str(parcel_data.get("plomba_id")))

    # Přímé odkazy na ČÚZK, Mapy.cz a Ikarus21
    q_mapy = urllib.parse.quote(f"{p_num} {p_ku}")
    url_m = f"https://mapy.cz/zakladni?q={q_mapy}&z=17"
    url_c = "https://nahlizenidokn.cuzk.cz/"
    url_ik = "https://www.ikarus21.cz/"
    
    b1, b2, b3 = st.columns(3)
    b1.link_button("🌐 Otevřít na Mapy.cz (Katastr)", url_m, use_container_width=True)
    b2.link_button("📜 Zjistit výměru na Nahlížení ČÚZK", url_c, use_container_width=True)
    b3.link_button("📊 Ikarus21 (Cenové mapy)", url_ik, use_container_width=True)

    is_field = any(w in p_type.lower() for w in ["orná", "pole", "les", "travní", "zahrada"])
    z_type = st.radio("Status v Územním plánu obce:", ["Nestavební (pole/les/NZ)", "Zastavitelná plocha (RD)"], index=0 if is_field else 1)
    is_buildable = (z_type == "Zastavitelná plocha (RD)")

    # Katastrální mapa pro zadanou obec
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

    # Pokud výměra není známá, nevyhodnocujeme žádné vymyšlené rozměry
    if area_val <= 0:
        st.warning(f"⚠️ Výměra parcely č. {p_num} není zadána. Klikněte na tlačítko ČÚZK výše, opište zjištěnou výměru a zadejte ji sem:")
        area_input = st.number_input("Zadejte zjištěnou výměru v m²:", min_value=0, max_value=10000000, value=0, step=50, key=f"inp_{p_num}_{p_ku}")
        if area_input > 0:
            area_val = float(area_input)
            st.rerun()

    # Teprve se známou výměrou počítáme ekonomiku
    if area_val > 0:
        if not is_buildable:
            st.error(f"🛑 Pozemek {p_num} je nestavební orná půda / pole ({area_val:,.0f} m²). Zákaz výstavby RD.")
            cp1, cp2 = st.columns(2)
            p_agr = cp1.number_input("Cena orné půdy dle Ikarusu (Kč/m²)", value=60, step=5)
            p_spec = cp2.number_input("Spekulativní cena s výhledem ÚP (Kč/m²)", value=450, step=25)
            
            a1, a2 = st.columns(2)
            a1.metric(f"Zemědělská hodnota ({p_agr} Kč/m²)", f"{area_val * p_agr:,.0f} Kč".replace(',', ' '))
            a2.metric("Rozvojová hodnota", f"{area_val * p_spec:,.0f} Kč".replace(',', ' '))
        else:
            st.success(f"🏡 Zastavitelná plocha: Parcela {p_num} ({area_val:,.0f} m²) je určena k zástavbě RD.")
            pc1, pc2 = st.columns(2)
            with pc1:
                t_plot = st.number_input("Cílová výměra 1 parcely (m²)", value=800, step=50)
                road_pct = st.slider("Podíl komunikací (%)", min_value=10, max_value=25, value=15)
            with pc2:
                buy_m2 = st.number_input("Nákup pozemku dle Ikarusu (Kč/m²)", value=3500, step=100)
                sell_m2 = st.number_input("Prodej parcely dle Ikarusu (Kč/m²)", value=9500, step=200)

            road_m2 = area_val * (road_pct / 100.0)
            net_m2 = area_val - road_m2
            n_plots = int(net_m2 // t_plot) if t_plot > 0 else 0
            road_len = max(30.0, road_m2 / 8.0)
            capex = (road_len * 32500.0) + (n_plots * 120000.0)
            cost_tot = (area_val * buy_m2) + capex
            rev_tot = net_m2 * sell_m2
            profit = rev_tot - cost_tot
            margin = (profit / rev_tot * 100.0) if rev_tot > 0 else 0.0

            st.divider()
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Počet parcel RD", f"{n_plots} ks")
            k2.metric("Infrastruktura", f"{capex:,.0f} Kč".replace(',', ' '))
            k3.metric("Očekávané tržby", f"{rev_tot:,.0f} Kč".replace(',', ' '))
            k4.metric("Hrubý zisk", f"{profit:,.0f} Kč".replace(',', ' '), f"{margin:.1f} % marže")
else:
    st.info("Nahrajte PDF výpisu z KN nebo zadejte parcelní číslo a obec nahoře.")