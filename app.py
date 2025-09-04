# dashboard.py
import streamlit as st
import pandas as pd
import mysql.connector
from datetime import datetime

# ================== CONFIG ==================
st.set_page_config(page_title="Dashboard Picking (SAP)", layout="wide")

# ================== PARÁMETROS ==================
TABLE = "sap"
DATE_COL = "FECHA"  # <-- Cambiá si tu columna de fecha se llama distinto

# ================== ESTILOS ==================
st.markdown("""
<style>
.block-container { padding-top: 2rem !important; }
.kpi {
  background: #fff; border: 1px solid #eee; border-radius: 12px;
  padding: 16px; text-align: center; box-shadow: 0 2px 10px rgba(0,0,0,.03);
}
.kpi h3 { margin: 0; font-size: 1.4rem; }
.kpi small { color: #666; }
</style>
""", unsafe_allow_html=True)

# ================== CONEXIÓN MYSQL ==================
def get_conn():
    return mysql.connector.connect(
        host=st.secrets["app_marco_new"]["host"],
        user=st.secrets["app_marco_new"]["user"],
        password=st.secrets["app_marco_new"]["password"],
        database=st.secrets["app_marco_new"]["database"],
        port=st.secrets["app_marco_new"].get("port", 3306),
    )

# ================== DATA ACCESS ==================
@st.cache_data(ttl=120)
def load_base(date_range=None) -> pd.DataFrame:
    """
    Carga datos de la tabla sap con o sin filtro de fecha.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"SHOW COLUMNS FROM {TABLE} LIKE %s", (DATE_COL,))
    has_date = cur.fetchone() is not None
    cur.close()

    if has_date and date_range and len(date_range) == 2 and all(date_range):
        q = f"""
            SELECT NUMERO, CLIENTE, CODIGO, CANTIDAD,
                   COALESCE(PICKING,'N') AS PICKING,
                   {DATE_COL} AS FECHA
            FROM {TABLE}
            WHERE DATE({DATE_COL}) BETWEEN %s AND %s
        """
        params = [date_range[0].strftime("%Y-%m-%d"), date_range[1].strftime("%Y-%m-%d")]
    else:
        q = f"""
            SELECT NUMERO, CLIENTE, CODIGO, CANTIDAD,
                   COALESCE(PICKING,'N') AS PICKING
            FROM {TABLE}
        """
        params = []

    df = pd.read_sql(q, conn, params=params)
    conn.close()

    # Normalizaciones
    df["PICKING"] = (
        df.get("PICKING", "N")
        .fillna("N").astype(str).str.strip().str.upper()
        .replace({"": "N"})
    )
    df["CANTIDAD"] = pd.to_numeric(df.get("CANTIDAD", 0), errors="coerce").fillna(0)

    if "CLIENTE" in df.columns:
        df["CLIENTE"] = df["CLIENTE"].apply(
            lambda x: str(int(x)) if isinstance(x, (int, float)) and float(x).is_integer() else str(x)
        )

    if "FECHA" in df.columns:
        df["FECHA"] = pd.to_datetime(df["FECHA"], errors="coerce")

    return df

def agg_progress(df: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    picked = df[df["PICKING"] == "Y"].groupby(by, dropna=False)["CANTIDAD"].sum().rename("picked_qty")
    total  = df.groupby(by, dropna=False)["CANTIDAD"].sum().rename("total_qty")
    out = total.to_frame().join(picked, how="left").fillna({"picked_qty": 0})
    out["avance_pct"] = (out["picked_qty"] / out["total_qty"]).where(out["total_qty"] > 0, 0) * 100
    return out.reset_index()

# ================== STATE ==================
def _ensure_state():
    if "date_range" not in st.session_state:
        st.session_state.date_range = ()  # vacío por default
    if "sel_clientes" not in st.session_state:
        st.session_state.sel_clientes = []
    if "sel_skus" not in st.session_state:
        st.session_state.sel_skus = []

def reset_filters():
    st.session_state.date_range = ()
    st.session_state.sel_clientes = []
    st.session_state.sel_skus = []
    st.rerun()

_ensure_state()

# ================== UI: SIDEBAR ==================
st.sidebar.title("Filtros")

# Botón Limpiar filtros
st.sidebar.button("🧹 Limpiar filtros", on_click=reset_filters, use_container_width=True)

# Rango de fechas (arranca vacío)
st.sidebar.date_input(
    "Rango de fechas",
    key="date_range",
    value=(),
    help=f"Filtra por {DATE_COL} (si existe)."
)

# Cargar base con el rango seleccionado
df = load_base(st.session_state.date_range)

# Opciones de cliente y sku (a partir del dataset filtrado por fecha si corresponde)
clientes = sorted(df["CLIENTE"].dropna().unique().tolist()) if "CLIENTE" in df.columns else []
skus     = sorted(df["CODIGO"].dropna().unique().tolist()) if "CODIGO" in df.columns else []

st.sidebar.multiselect("Cliente", options=clientes, key="sel_clientes")
st.sidebar.multiselect("SKU", options=skus, key="sel_skus")

# Aplicar filtros acumulables
if st.session_state.sel_clientes:
    df = df[df["CLIENTE"].isin(st.session_state.sel_clientes)]
if st.session_state.sel_skus:
    df = df[df["CODIGO"].isin(st.session_state.sel_skus)]

# ================== KPIs GLOBALES ==================
st.title("Dashboard Picking (SAP)")

total_qty = float(df["CANTIDAD"].sum())
picked_qty = float(df.loc[df["PICKING"] == "Y", "CANTIDAD"].sum())
avance_pct = (picked_qty / total_qty * 100) if total_qty > 0 else 0

c1, c2, c3 = st.columns(3)
with c1:
    st.markdown('<div class="kpi"><small>Total Cantidad</small><h3>{}</h3></div>'.format(
        int(total_qty) if total_qty.is_integer() else round(total_qty, 2)
    ), unsafe_allow_html=True)
with c2:
    st.markdown('<div class="kpi"><small>Cantidad Pickeada</small><h3>{}</h3></div>'.format(
        int(picked_qty) if picked_qty.is_integer() else round(picked_qty, 2)
    ), unsafe_allow_html=True)
with c3:
    st.markdown('<div class="kpi"><small>Avance</small><h3>{:.1f}%</h3></div>'.format(avance_pct),
                unsafe_allow_html=True)

st.progress(avance_pct / 100 if total_qty > 0 else 0.0)
st.markdown("—")

# ================== TABS ==================
tab1, tab2, tab3 = st.tabs(["📅 Por fecha", "👤 Por cliente", "🏷️ Por SKU"])

with tab1:
    st.subheader("Avance por fecha")
    if "FECHA" in df.columns and not df["FECHA"].isna().all():
        tmp = df.copy()
        tmp["fecha_dia"] = pd.to_datetime(tmp["FECHA"], errors="coerce").dt.date
        g = agg_progress(tmp, by=["fecha_dia"]).sort_values("fecha_dia")
        st.dataframe(g.rename(columns={
            "fecha_dia": "Fecha",
            "total_qty": "Total",
            "picked_qty": "Pickeado",
            "avance_pct": "Avance %"
        }), use_container_width=True)

        try:
            import altair as alt
            chart = alt.Chart(g).mark_bar().encode(
                x=alt.X("fecha_dia:T", title="Fecha"),
                y=alt.Y("avance_pct:Q", title="Avance %"),
                tooltip=["fecha_dia:T", "total_qty:Q", "picked_qty:Q", alt.Tooltip("avance_pct:Q", format=".1f")]
            ).properties(height=300)
            st.altair_chart(chart, use_container_width=True)
        except Exception:
            st.info("No se pudo renderizar el gráfico (Altair no disponible).")
    else:
        st.warning(f"No hay columna `{DATE_COL}` o el filtro está vacío.")

with tab2:
    st.subheader("Avance por cliente")
    if "CLIENTE" in df.columns:
        g = agg_progress(df, by=["CLIENTE"]).sort_values("avance_pct", ascending=False)
        st.dataframe(g.rename(columns={
            "CLIENTE": "Cliente",
            "total_qty": "Total",
            "picked_qty": "Pickeado",
            "avance_pct": "Avance %"
        }), use_container_width=True)

        try:
            import altair as alt
            chart = alt.Chart(g).mark_bar().encode(
                x=alt.X("CLIENTE:N", sort="-y", title="Cliente"),
                y=alt.Y("avance_pct:Q", title="Avance %"),
                tooltip=["CLIENTE:N", "total_qty:Q", "picked_qty:Q", alt.Tooltip("avance_pct:Q", format=".1f")]
            ).properties(height=360)
            st.altair_chart(chart, use_container_width=True)
        except Exception:
            st.info("No se pudo renderizar el gráfico (Altair no disponible).")
    else:
        st.warning("La tabla no tiene columna CLIENTE.")

with tab3:
    st.subheader("Avance por SKU")
    if "CODIGO" in df.columns:
        g = agg_progress(df, by=["CODIGO"]).sort_values("avance_pct", ascending=False)
        st.dataframe(g.rename(columns={
            "CODIGO": "SKU",
            "total_qty": "Total",
            "picked_qty": "Pickeado",
            "avance_pct": "Avance %"
        }), use_container_width=True)

        try:
            import altair as alt
            chart = alt.Chart(g).mark_bar().encode(
                x=alt.X("CODIGO:N", sort="-y", title="SKU"),
                y=alt.Y("avance_pct:Q", title="Avance %"),
                tooltip=["CODIGO:N", "total_qty:Q", "picked_qty:Q", alt.Tooltip("avance_pct:Q", format=".1f")]
            ).properties(height=360)
            st.altair_chart(chart, use_container_width=True)
        except Exception:
            st.info("No se pudo renderizar el gráfico (Altair no disponible).")
    else:
        st.warning("La tabla no tiene columna CODIGO (SKU).")
