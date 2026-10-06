import streamlit as st
import streamlit.components.v1 as components
import urllib.parse

st.set_page_config(page_title="ParcelCheck AI", page_icon="🏛️", layout="wide")
st.title("🏛️ ParcelCheck AI — Katastrální prověrka & Due Diligence")

with st.sidebar:
    st.header("⚙️ Nastavení auditu")
    st.info("Aplikace automaticky zacílí katastrální mapu ČÚZK na jakoukoliv obec v ČR.")

# Vstupní formulář
with st.form("search_form"):
    c1, c2, c3 = st.columns([1.5, 2, 1.5])
    p_num = c1.text_input("Parcelní číslo", placeholder="např. 841/4", value="")
    p_ku = c2.text_input("Obec nebo katastrální území", placeholder="např. Kozojedy", value="")
    p_area = c3.number_input("Výměra v m² (dle KN)", min_value=0, max_value=5000000, value=0, step=100)
    
    submitted = st.form_submit_button("🔍 Vyhledat pozemek a načíst katastr (Enter)", type="primary", use_container_width=True)

if submitted or (p_num and p_ku):
    p_num_clean = p_num.strip()
    p_ku_clean = p_ku.strip()
    
    if not p_num_clean or not p_ku_clean:
        st.warning("Zadejte prosím číslo parcely i obec.")
    else:
        st.success(f"📍 Prověřuji parcelu č. **{p_num_clean}** v katastrálním území **{p_ku_clean}**")
        
        # Odkazy na oficiální zdroje
        q_mapy = urllib.parse.quote(f"{p_num_clean} {p_ku_clean}")
        url_mapy = f"https://mapy.cz/zakladni?q={q_mapy}&z=17"
        url_cuzk = "https://nahlizenidokn.cuzk.cz/"

        b_c1, b_c2, b_c3 = st.columns([2, 2, 2])
        b_c1.link_button("🌐 Otevřít parcelu na Mapy.cz (Katastr)", url_mapy, type="secondary", use_container_width=True)
        b_c2.link_button("📜 Otevřít Nahlížení do KN (ČÚZK)", url_cuzk, type="secondary", use_container_width=True)
        
        st.divider()

        # Typ pozemku dle ÚP
        zoning_type = st.radio(
            "Reálný status v Územním plánu obce:",
            ["🌾 Nestavební pozemek (orná půda / louka / les / pole)", 
             "🏡 Zastavitelná plocha (určeno pro výstavbu RD / komerční)"],
            horizontal=True
        )
        is_buildable = "Zastavitelná" in zoning_type

        # Zobrazení mapy zacílené na zadanou obec
        st.subheader(f"🗺️ Katastrální mapa ČÚZK & Ortofoto: {p_ku_clean}")
        
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

            L.control.layers({{"Letecký snímek": orto}}, {{"Katastrální hranice ČÚZK": cuzkKN, "Zákresy": items}}, {{position: 'topright'}}).addTo(map);

            // Geocoding vyhledání přesné obce v ČR
            var query = "{p_ku_clean}, Česká republika";
            fetch("https://nominatim.openstreetmap.org/search?format=json&q=" + encodeURIComponent(query))
                .then(r => r.json())
                .then(data => {{
                    if (data && data.length > 0) {{
                        var lat = parseFloat(data[0].lat);
                        var lon = parseFloat(data[0].lon);
                        map.setView([lat, lon], 16);
                        L.marker([lat, lon]).addTo(map).bindPopup("<b>{p_ku_clean}</b><br>Hledaná parcela: {p_num_clean}").openPopup();
                    }}
                }});
        </script>
        </body></html>
        """
        components.html(map_html, height=540)

        # Vyhodnocení podle typu pozemku
        if not is_buildable:
            st.error(f"🛑 POZOR: Parcela č. {p_num_clean} v k.ú. {p_ku_clean} je vedena jako nestavební půda (pole/les/NZ).")
            
            e1, e2 = st.columns(2)
            with e1:
                st.markdown("#### 🌾 Parametry orné / nestavební půdy")
                if p_area > 0:
                    st.metric("Zadaná výměra", f"{p_area:,.0f} m²".replace(',', ' '), f"{(p_area/10000):.2f} ha")
                    st.metric("Orientační zemědělská hodnota (45–70 Kč/m²)", f"{(p_area * 55):,.0f} Kč".replace(',', ' '))
                else:
                    st.info("💡 Výměru můžete zadat do pole v horním formuláři pro spočtení hodnoty půdy.")
            
            with e2:
                st.markdown("#### ⚖️ Rizika a postup pro developera")
                st.write("1. **Zákaz výstavby:** Pozemek leží mimo zastavitelné území dle platného ÚP.")
                st.write("2. **ZPF:** Nutno prověřit bonitu půdy (I. a II. třída ochrany je téměř nevynutelná k odnětí).")
                st.write("3. **Podnět na změnu ÚP:** Nutno jednat se samosprávou o zařazení do rozvojových ploch.")

        else:
            st.success(f"🏡 Zastavitelná plocha: Pro parcelu č. {p_num_clean} lze provést bilanci parcelace a sítí.")
            if p_area > 0:
                p_c1, p_c2 = st.columns(2)
                with p_c1:
                    target_plot = st.number_input("Cílová výměra 1 parcely RD (m²)", min_value=500, max_value=3000, value=800, step=50)
                    road_pct = st.slider("Zábor na silnice a sítě (%)", min_value=10, max_value=25, value=15)
                with p_c2:
                    buy_m2 = st.number_input("Nákup surového pozemku (Kč/m²)", value=2500, step=100)
                    sell_m2 = st.number_input("Prodejní cena zasíťované parcely (Kč/m²)", value=8500, step=200)

                road_m2 = p_area * (road_pct / 100.0)
                net_m2 = p_area - road_m2
                n_plots = int(net_m2 // target_plot) if target_plot > 0 else 0
                avg_p = (net_m2 / n_plots) if n_plots > 0 else 0.0

                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Celková plocha", f"{p_area:,.0f} m²".replace(',', ' '))
                m2.metric("Plocha infrastruktury", f"{road_m2:,.0f} m²".replace(',', ' '))
                m3.metric("Čistá stavební plocha", f"{net_m2:,.0f} m²".replace(',', ' '))
                m4.metric("Stavební parcely", f"{n_plots} ks", f"průměrně {avg_p:.0f} m²")
            else:
                st.info("Zadejte výměru v horním formuláři pro aktivaci kalkulačky parcelace a ziskovosti.")
else:
    st.info("💡 Zadejte parcelní číslo a obec do formuláře výše a stiskněte Enter.")