# dashboard.py
import streamlit as st
import pandas as pd
import mysql.connector
from datetime import datetime

# ================== CONFIG ==================
st.set_page_config(page_title="Dashboard Picking (SAP)", layout="wide")

# ================== PARÁMETROS ==================
TABLE = "sap"
DATE_COL = "FECHA"  # Cambiar si la columna de fecha tiene otro nombre

# ================== ESTILOS ==================
st.markdown("""
<style>
.block-container { padding-top: 1.0rem !important; }

/* Barra de filtros */
.filter-bar {
  background: #fafafa; border: 1px solid #eee; border-radius: 12px;
  padding: 16px; margin: 8px auto 16px auto; max-width: 1200px;
  box-shadow: 0 1px 6px rgba(0,0,0,.04);
}

/* Popover del datepicker por encima */
div[data-baseweb="popover"], div[role="dialog"] { z-index: 10000 !important; }
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
    """Carga datos con o sin filtro de fecha (si DATE_COL existe)."""
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
        st.session_state.date_range = ()   # sin fecha por defecto
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

# ================== FILTROS ARRIBA ==================
st.markdown('<div class="filter-bar">', unsafe_allow_html=True)

c1, c2, c3 = st.columns(3)
with c1:
    st.date_input("Rango de fechas", key="date_range", value=(),
                  help=f"Filtra por {DATE_COL} (si existe).")

# Cargar base en función de la fecha antes de poblar selects
df = load_base(st.session_state.date_range)

clientes_opts = sorted(df["CLIENTE"].dropna().unique().tolist()) if "CLIENTE" in df.columns else []
skus_opts     = sorted(df["CODIGO"].dropna().unique().tolist()) if "CODIGO" in df.columns else []

with c2:
    st.multiselect("Cliente", options=clientes_opts, key="sel_clientes")
with c3:
    st.multiselect("SKU", options=skus_opts, key="sel_skus")

st.button("Limpiar filtros", on_click=reset_filters)

st.markdown('</div>', unsafe_allow_html=True)

# Aplicar filtros acumulables
if st.session_state.sel_clientes:
    df = df[df["CLIENTE"].isin(st.session_state.sel_clientes)]
if st.session_state.sel_skus:
    df = df[df["CODIGO"].isin(st.session_state.sel_skus)]

# ================== RESULTADOS EN EXPANDERS ==================
st.header("Resultados")

with st.expander("Avance por fecha", expanded=False):
    if "FECHA" in df.columns and not df["FECHA"].isna().all():
        tmp = df.copy()
        tmp["fecha_dia"] = pd.to_datetime(tmp["FECHA"], errors="coerce").dt.date
        g_fecha = agg_progress(tmp, by=["fecha_dia"]).sort_values("fecha_dia")
        st.dataframe(g_fecha.rename(columns={
            "fecha_dia": "Fecha",
            "total_qty": "Total",
            "picked_qty": "Pickeado",
            "avance_pct": "Avance %"
        }), use_container_width=True)
    else:
        st.warning(f"No hay columna `{DATE_COL}` o el filtro de fecha está vacío.")

with st.expander("Avance por cliente", expanded=False):
    if "CLIENTE" in df.columns:
        g_cli = agg_progress(df, by=["CLIENTE"]).sort_values("avance_pct", ascending=False)
        st.dataframe(g_cli.rename(columns={
            "CLIENTE": "Cliente",
            "total_qty": "Total",
            "picked_qty": "Pickeado",
            "avance_pct": "Avance %"
        }), use_container_width=True)
    else:
        st.warning("La tabla no tiene columna CLIENTE.")

with st.expander("Avance por SKU", expanded=False):
    if "CODIGO" in df.columns:
        g_sku = agg_progress(df, by=["CODIGO"]).sort_values("avance_pct", ascending=False)
        st.dataframe(g_sku.rename(columns={
            "CODIGO": "SKU",
            "total_qty": "Total",
            "picked_qty": "Pickeado",
            "avance_pct": "Avance %"
        }), use_container_width=True)
    else:
        st.warning("La tabla no tiene columna CODIGO (SKU).")
