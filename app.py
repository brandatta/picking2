
import streamlit as st
import pandas as pd
import mysql.connector
from datetime import datetime, timedelta

# ================== CONFIG ==================
st.set_page_config(page_title="Dashboard Picking (SAP)", layout="wide")

# ================== PARÁMETROS ==================
TABLE = "sap"
SCHEMA = "app_marco_new"  # solo a modo informativo; usamos st.secrets
DATE_COL = "FECHA"        # <--- CAMBIAR si tu columna tiene otro nombre

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
@st.cache_data(ttl=60)
def load_base(date_from: datetime | None, date_to: datetime | None) -> pd.DataFrame:
    """
    Carga datos de la tabla sap con filtros de fecha (si la columna existe).
    Normaliza CANTIDAD y PICKING para poder calcular avances.
    """
    conn = get_conn()
    cur = conn.cursor()
    # Chequear si existe la columna de fecha
    cur.execute(f"SHOW COLUMNS FROM {TABLE} LIKE %s", (DATE_COL,))
    has_date = cur.fetchone() is not None
    cur.close()

    if has_date and date_from and date_to:
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
    if "PICKING" in df.columns:
        df["PICKING"] = (df["PICKING"].fillna("N").astype(str).str.strip().str.upper().replace({"": "N"}))
    else:
        df["PICKING"] = "N"

    # CANTIDAD a numérico
    if "CANTIDAD" in df.columns:
        df["CANTIDAD"] = pd.to_numeric(df["CANTIDAD"], errors="coerce").fillna(0)
    else:
        df["CANTIDAD"] = 0

    # CLIENTE sin .0 si es entero
    if "CLIENTE" in df.columns:
        df["CLIENTE"] = df["CLIENTE"].apply(
            lambda x: str(int(x)) if isinstance(x, (int, float)) and float(x).is_integer() else str(x)
        )

    # FECHA si existe
    if "FECHA" not in df.columns and has_date:
        # Si la columna existe pero no se seleccionó (no debería pasar), forzamos FECHA
        df["FECHA"] = pd.NaT
    if "FECHA" in df.columns:
        df["FECHA"] = pd.to_datetime(df["FECHA"], errors="coerce")

    return df

def agg_progress(df: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """
    Calcula total_qty, picked_qty y avance (%) agrupado por columnas en `by`.
    """
    # picked_qty = suma de CANTIDAD donde PICKING = 'Y'
    picked = df[df["PICKING"] == "Y"].groupby(by, dropna=False)["CANTIDAD"].sum().rename("picked_qty")
    total = df.groupby(by, dropna=False)["CANTIDAD"].sum().rename("total_qty")
    out = total.to_frame().join(picked, how="left").fillna({"picked_qty": 0})
    out["avance_pct"] = (out["picked_qty"] / out["total_qty"]).where(out["total_qty"] > 0, 0) * 100
    out = out.reset_index()
    return out

# ================== UI: SIDEBAR ==================
st.sidebar.title("Filtros")
default_to = datetime.now().date()
default_from = (datetime.now() - timedelta(days=30)).date()

date_range = st.sidebar.date_input(
    "Rango de fechas",
    (default_from, default_to),
    help=f"Filtra por {DATE_COL} (si existe)."
)
if isinstance(date_range, tuple) and len(date_range) == 2:
    date_from, date_to = date_range
else:
    date_from, date_to = default_from, default_to

df = load_base(date_from, date_to)

# Filtros dinámicos (cliente y SKU)
clientes = sorted(df["CLIENTE"].dropna().unique().tolist()) if "CLIENTE" in df.columns else []
skus = sorted(df["CODIGO"].dropna().unique().tolist()) if "CODIGO" in df.columns else []

sel_clientes = st.sidebar.multiselect("Cliente", options=clientes, default=[])
sel_skus = st.sidebar.multiselect("SKU", options=skus, default=[])

if sel_clientes:
    df = df[df["CLIENTE"].isin(sel_clientes)]
if sel_skus:
    df = df[df["CODIGO"].isin(sel_skus)]

# ================== KPIs GLOBALES ==================
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
        tmp["fecha_dia"] = tmp["FECHA"].dt.date
        g = agg_progress(tmp, by=["fecha_dia"])
        g = g.sort_values("fecha_dia")

        st.dataframe(g.rename(columns={
            "fecha_dia": "Fecha",
            "total_qty": "Total",
            "picked_qty": "Pickeado",
            "avance_pct": "Avance %"
        }), use_container_width=True)

        # Chart (Altair)
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
        st.warning(f"No se encontró la columna de fecha `{DATE_COL}` o no tiene datos en el rango.")

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
