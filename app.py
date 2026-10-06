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
            "lv_no": self._ex(r"Číslo LV:\s*([0-9]+)"),
            "area_m2": self._ex(r"Výměra \[m2\]:\s*([0-9\s]+)").replace(" ", ""),
            "land_type": self._ex(r"Druh pozemku:\s*([^\n\r]+)"),
            "has_plomba": bool(pm),
            "plomba_id": pm.group(1).strip() if pm else ""
        }
    def _ex(self, pat):
        m = re.search(pat, self.raw)
        return m.group(1).strip() if m else ""

# Přepínač způsobu zadání
input_mode = st.radio(
    "Vyberte způsob zadání:",
    ["📄 Nahrát PDF výpisu z Nahlížení do KN (doporučeno — načte vše automaticky)", 
     "✍️ Zadat ručně (parcela a obec)"],
    horizontal=True
)

parcel_data = None

if "📄 Nahrát PDF" in input_mode:
    up_pdf = st.file_uploader("Nahrajte PDF výpisu z Nahlížení do KN", type=["pdf"])
    if up_pdf is not None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(up_pdf.read())
            tmp_p = tmp.name
        
        try:
            reader = pypdf.PdfReader(tmp_p)
            text = "".join([p.extract_text() or "" for p in reader.pages])
            an = Analyzer(text)
            parcel_data = an.data
        except Exception as e:
            st.error(f"Chyba při čtení PDF: {e}")
        finally:
            try:
                os.remove(tmp_p)
            except Exception:
                pass

else:
    with st.form("manual_form"):
        c1, c2, c3 = st.columns(3)
        p_num = c1.text_input("Parcelní číslo", placeholder="např. 841/4")
        p_ku = c2.text_input("Obec nebo katastrální území", placeholder="např. Kozojedy")
        p_area = c3.number_input("Výměra v m² (pokud znáte)", min_value=0, max_value=5000000, value=0, step=100)
        
        if st.form_submit_button("🔍 Prověřit pozemek (Enter)", type="primary", use_container_width=True):
            if p_num.strip() and p_ku.strip():
                parcel_data = {
                    "parcel_no": p_num.strip(),
                    "municipality": p_ku.strip(),
                    "cadastral_area": p_ku.strip(),
                    "lv_no": "-",
                    "area_m2": str(p_area) if p_area > 0 else "",
                    "land_type": "orná půda / neurčeno",
                    "has_plomba": False,
                    "plomba_id": ""
                }
            else:
                st.warning("Vyplňte prosím číslo parcely i obec.")

# Zobrazení výsledků
if parcel_data:
    p_num = parcel_data.get("parcel_no", "")
    p_ku = parcel_data.get("cadastral_area", "") or parcel_data.get("municipality", "")
    p_type = parcel_data.get("land_type", "orná půda")
    
    # Získání výměry
    try:
        area_val = float(parcel_data.get("area_m2", 0))
    except Exception:
        area_val = 0.0

    st.divider()

    # Informační panel
    col_i1, col_i2, col_i3, col_i4 = st.columns(4)
    col_i1.metric("Parcela", p_num)
    col_i2.metric("Katastrální území / Obec", p_ku)
    col_i3.metric("Výměra z KN", f"{area_val:,.0f} m²".replace(',', ' ') if area_val > 0 else "Nezadáno")
    col_i4.metric("Druh pozemku", p_type if p_type else "orná půda")

    if parcel_data.get("has_plomba"):
        st.error(f"🚨 POZOR PLOMBA: {parcel_data.get('plomba_id')} — probíhá řízení na katastru!")

    # Přímé odkazy na ČÚZK a Mapy.cz
    q_mapy = urllib.parse.quote(f"{p_num} {p_ku}")
    url_mapy = f"https://mapy.cz/zakladni?q={q_mapy}&z=17"
    url_cuzk = "https://nahlizenidokn.cuzk.cz/"
    
    btn1, btn2 = st.columns(2)
    btn1.link_button("🌐 Otevřít parcelu přímo na Mapy.cz (Katastr)", url_mapy, use_container_width=True)
    btn2.link_button("📜 Otevřít Nahlížení do KN (ČÚZK)", url_cuzk, use_container_width=True)

    st.divider()

    # Volba statusu v Územním plánu
    is_field_detected = any(w in p_type.lower() for w in ["orná", "pole", "les", "trvalý travní", "zahrada"])
    zoning = st.radio(
        "Status pozemku v Územním plánu obce:",
        ["🌾 Nestavební plocha — orná půda / nezastavěné území (NZ / zákaz staveb RD)",
         "🏡 Zastavitelná plocha — bydlení (BI / povolena výstavba RD)"],
        index=0 if is_field_detected else 1,
        horizontal=True
    )
    is_buildable = "Zastavitelná" in zoning

    # Mapa zacílená na obec a parcelu
    st.subheader(f"🗺️ Katastrální mapa ČÚZK & Ortofoto: {p_ku}")
    
    map_html = f"""
    <!DOCTYPE html>
    <html><head>
    <meta charset="utf-8" />
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet.draw/1.0.4/leaflet.draw.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet.draw/1.0.4/leaflet.draw.js"></script>
    <style>body{{margin:0;padding:0;background:#0f172a;}} #m{{width:100%;height:520px;border-radius:6px;}}</style>
    </head><body>
    <div id="m"></div>
    <script>
        var map = L.map('m').setView([49.8175, 15.4730], 8);

        var orto = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
            maxZoom: 20, attribution: 'Letecký snímek'
        }}).addTo(map);

        var cuzkKN = L.tileLayer.wms('https://services.cuzk.gov.cz/wms/local-km-wms.asp', {{
            layers: 'KN', format: 'image/png', transparent: true, version: '1.3.0', crs: L.CRS.EPSG3857, attribution: 'ČÚZK'
        }}).addTo(map);

        var items = new L.FeatureGroup().addTo(map);
        var draw = new L.Control.Draw({{
            position: 'topleft',
            draw: {{
                polyline: {{ shapeOptions: {{ color: '#EF4444', weight: 4 }} }},
                polygon: {{ allowIntersection: false, showArea: true, shapeOptions: {{ color: '#10B981', fillOpacity: 0.35, weight: 2 }} }},
                rectangle: false, circle: false, circlemarker: false, marker: false
            }},
            edit: {{ featureGroup: items }}
        }});
        map.addControl(draw);
        map.on(L.Draw.Event.CREATED, function (e) {{ items.addLayer(e.layer); }});

        L.control.layers({{"Letecký snímek": orto}}, {{"Katastrální hranice ČÚZK": cuzkKN, "Moje zákresy": items}}, {{position: 'topright'}}).addTo(map);

        var query = "{p_ku}, Česká republika";
        fetch("https://nominatim.openstreetmap.org/search?format=json&q=" + encodeURIComponent(query))
            .then(r => r.json())
            .then(data => {{
                if (data && data.length > 0) {{
                    var lat = parseFloat(data[0].lat);
                    var lon = parseFloat(data[0].lon);
                    map.setView([lat, lon], 16);
                    L.marker([lat, lon]).addTo(map).bindPopup("<b>{p_ku}</b><br>Parcela č. {p_num}").openPopup();
                }}
            }});
    </script>
    </body></html>
    """
    components.html(map_html, height=540)

    # Rozbor
    if not is_buildable:
        st.error(f"🛑 POZOR: Parcela č. {p_num} v k.ú. {p_ku} je orná půda / nestavební pozemek.")
        e1, e2 = st.columns(2)
        with e1:
            st.markdown("#### 🌾 Zemědělská hodnota půdy")
            if area_val > 0:
                st.metric("Výměra pozemku", f"{area_val:,.0f} m²".replace(',', ' '), f"{(area_val/10000):.2f} ha")
                st.metric("Běžná zemědělská cena (45–70 Kč/m²)", f"{(area_val * 55):,.0f} Kč".replace(',', ' '))
                st.metric("Spekulativní hodnota (výhled ÚP 300–600 Kč/m²)", f"{(area_val * 450):,.0f} Kč".replace(',', ' '))
            else:
                st.info("Zadejte výměru pozemku pro výpočet ocenění.")
        with e2:
            st.markdown("#### 📌 Co to znamená pro investora / developera")
            st.write("1. **Stavba RD není povolena.** Pozemek je v nezastavěném území obce.")
            st.write("2. **ZPF:** Nutno zjistit třídu ochrany půdy (I. a II. třída se pro zástavbu téměř nepovoluje vyjmout).")
            st.write("3. **Postup:** Podat žádost / podnět na pořízení změny Územního plánu na obci.")

    else:
        st.success(f"🏡 Zastavitelná plocha: Pro parcelu č. {p_num} lze počítat parcelaci a sítě.")
        if area_val > 0:
            pc1, pc2 = st.columns(2)
            with pc1:
                target_plot = st.number_input("Cílová výměra 1 parcely RD (m²)", min_value=400, max_value=2500, value=800, step=50)
                road_pct = st.slider("Zábor na cesty a sítě (%)", min_value=10, max_value=25, value=15)
            with pc2:
                buy_m2 = st.number_input("Nákup surového pozemku (Kč/m²)", value=2500, step=100)
                sell_m2 = st.number_input("Prodejní cena parcely (Kč/m²)", value=8500, step=200)

            road_m2 = area_val * (road_pct / 100.0)
            net_m2 = area_val - road_m2
            n_plots = int(net_m2 // target_plot) if target_plot > 0 else 0
            avg_p = (net_m2 / n_plots) if n_plots > 0 else 0.0

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Celková výměra", f"{area_val:,.0f} m²".replace(',', ' '))
            m2.metric("Plocha komunikací", f"{road_m2:,.0f} m²".replace(',', ' '))
            m3.metric("Čisté parcely", f"{net_m2