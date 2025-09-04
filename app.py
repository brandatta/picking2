# dashboard.py
import streamlit as st
import pandas as pd
import mysql.connector
from datetime import datetime, timedelta

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
def load_base(date_from: datetime | None = None, date_to: datetime | None = None, use_date_filter: bool = False) -> pd.DataFrame:
    """
    Carga datos de la tabla sap. Si use_date_filter=True y existe DATE_COL, filtra por rango.
    Normaliza CANTIDAD y PICKING.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"SHOW COLUMNS FROM {TABLE} LIKE %s", (DATE_COL,))
    has_date = cur.fetchone() is not None
    cur.close()

    if use_date_filter and has_date and date_from and date_to:
        q = f"""
            SELECT NUMERO, CLIENTE, CODIGO, CANTIDAD,
                   COALESCE(PICKING,'N') AS PICKING,
                   {DATE_COL} AS FECHA
            FROM {TABLE}
            WHERE DATE({DATE_COL}) BETWEEN %s AND %s
        """
        params = [date_from.strftime("%Y-%m-%d"), date_to.strftime("%Y-%m-%d")]
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
    df["PICKING"] = df.get("PICKING", "N")
    df["PICKING"] = (
        df["PICKING"]
        .fillna("N").astype(str).str.strip().str.upper()
        .replace({"": "N"})
    )

    # CANTIDAD
    df["CANTIDAD"] = pd.to_numeric(df.get("CANTIDAD", 0), errors="coerce").fillna(0)

    # CLIENTE sin .0 si es entero
    if "CLIENTE" in df.columns:
        df["CLIENTE"] = df["CLIENTE"].apply(
            lambda x: str(int(x)) if isinstance(x, (int, float)) and float(x).is_integer() else str(x)
        )

    # FECHA
    if "FECHA" in df.columns:
        df["FECHA"] = pd.to_datetime(df["FECHA"], errors="coerce")

    return df

def agg_progress(df: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    picked = df[df["PICKING"] == "Y"].groupby(by, dropna=False)["CANTIDAD"].sum().rename("picked_qty")
    total  = df.groupby(by, dropna=False)["CANTIDAD"].sum().rename("total_qty")
    out = total.to_frame().join(picked, how="left").fillna({"picked_qty": 0})
    out["avance_pct"] = (out["picked_qty"] / out["total_qty"]).where(out["total_qty"] > 0, 0) * 100
    return out.reset_index()

# ================== UI: SIDEBAR (filtros independientes y acumulables) ==================
st.sidebar.title("Filtros")

# 1) Universo completo para opciones (independiente de filtros aplicados)
df_universe = load_base(use_date_filter=False)

clientes_all = sorted(df_universe["CLIENTE"].dropna().unique().tolist()) if "CLIENTE" in df_universe.columns else []
skus_all     = sorted(df_universe["CODIGO"].dropna().unique().tolist()) if "CODIGO" in df_universe.columns else []

# 2) Controles para activar/desactivar cada filtro
apply_date = st.sidebar.checkbox("Filtrar por fecha", value=True)
apply_client = st.sidebar.checkbox("Filtrar por cliente", value=False)
apply_sku = st.sidebar.checkbox("Filtrar por SKU", value=False)

# Rango de fechas (por defecto últimos 30 días)
default_to = datetime.now().date()
default_from = (datetime.now() - timedelta(days=30)).date()
date_from, date_to = default_from, default_to
if apply_date:
    date_range = st.sidebar.date_input("Rango de fechas", (default_from, default_to))
    if isinstance(date_range, tuple) and len(date_range) == 2:
        date_from, date_to = date_range

# Selectores de cliente y sku (independientes del resto)
sel_clientes = st.sidebar.multiselect("Cliente", options=clientes_all, default=[])
sel_skus = st.sidebar.multiselect("SKU", options=skus_all, default=[])

# Modo combinación entre Cliente y SKU
combine_mode = st.sidebar.radio(
    "Combinar Cliente y SKU",
    options=["AND", "OR"],
    horizontal=True,
    help="AND = debe cumplir ambos filtros. OR = alcanza con Cliente o SKU."
)

# 3) Dataset filtrado (acumulación según activados)
df = load_base(date_from, date_to, use_date_filter=apply_date)

# Aplicar filtros de Cliente/SKU según modo
if apply_client and sel_clientes and apply_sku and sel_skus:
    if combine_mode == "AND":
        df = df[df["CLIENTE"].isin(sel_clientes) & df["CODIGO"].isin(sel_skus)]
    else:  # OR
        df = df[df["CLIENTE"].isin(sel_clientes) | df["CODIGO"].isin(sel_skus)]
else:
    if apply_client and sel_clientes:
        df = df[df["CLIENTE"].isin(sel_clientes)]
    if apply_sku and sel_skus:
        df = df[df["CODIGO"].isin(sel_skus)]

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
st.markdown("---")

# ================== TABS ==================
def safe_is_date_col(df: pd.DataFrame) -> bool:
    return "FECHA" in df.columns and not df["FECHA"].isna().all()

tab1, tab2, tab3 = st.tabs(["📅 Por fecha", "👤 Por cliente", "🏷️ Por SKU"])

with tab1:
    st.subheader("Avance por fecha")
    if safe_is_date_col(df):
        tmp = df.copy()
        tmp["fecha_dia"] = tmp["FECHA"].dt.date
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
        st.warning(f"No hay columna `{DATE_COL}` con datos para este rango.")

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
