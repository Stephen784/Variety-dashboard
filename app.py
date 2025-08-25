# app.py
import os
import logging
import pandas as pd
import pyreadstat
from dash import Dash, dcc, html, Input, Output, dash_table
import plotly.express as px
import plotly.graph_objects as go

# -------------------------
# Logging
# -------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------------
# Paths
# -------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
EXCEL_PATH = os.path.join(DATA_DIR, "FIELD DAYS VARIETIES SATISFACTION RATING.xlsx")
SAV_PATH   = os.path.join(DATA_DIR, "FIELD DAYS VARIETIES SATISFACTION RATING.sav")

# -------------------------
# Exact variety mapping (13 varieties)
# -------------------------
variety_map = {
    1: "SC 301", 2: "SC 419", 3: "SC 423", 4: "SC 529", 5: "SC 555",
    6: "SC 653", 7: "SC 665", 8: "SC 729", 9: "SC Saga", 10: "SC Signal",
    11: "SC Serenade", 12: "Nerica-4", 13: "Sorghum",
}

# -------------------------
# Rating text -> numeric map
# -------------------------
rating_text_to_code = {
    "very dissatisfied": 1,
    "dissatisfied": 2,
    "neutral": 3,
    "satisfied": 4,
    "very satisfied": 5,
}

# -------------------------
# Helpers
# -------------------------
def normalize_str(x):
    if pd.isna(x):
        return None
    if isinstance(x, str):
        return x.strip().lower()
    return str(x).strip().lower()

def _clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(axis=1, how="all")
    df.columns = df.columns.astype(str).str.strip()
    return df

def map_varieties(df: pd.DataFrame) -> pd.DataFrame:
    if "VARIETY" in df.columns:
        numeric = pd.to_numeric(df["VARIETY"], errors="coerce")
        mapped = numeric.map(variety_map)
        df["VARIETY"] = mapped.combine_first(df["VARIETY"].astype(object))
    if "BUYING" in df.columns:
        numeric = pd.to_numeric(df["BUYING"], errors="coerce")
        mapped = numeric.map(variety_map)
        df["BUYING"] = mapped.combine_first(df["BUYING"].astype(object))
    return df

def normalize_rating_column(df: pd.DataFrame) -> pd.DataFrame:
    """
    Replace the RATING column with numeric codes (Int64 nullable).
    Rules:
      1) If already numeric -> keep (coerced)
      2) Else, map common text labels to codes
      3) Else, extract first integer from string (e.g. '5 (Very satisfied)')
      4) Final: coerce to Int64, filter to 1..5 (out-of-range -> NA)
    """
    if "RATING" not in df.columns:
        return df

    # 1) try numeric conversion
    codes = pd.to_numeric(df["RATING"], errors="coerce")

    # 2) map text labels for non-numeric entries
    mask_nonnum = codes.isna()
    if mask_nonnum.any():
        mapped = df.loc[mask_nonnum, "RATING"].map(lambda x: rating_text_to_code.get(normalize_str(x), pd.NA))
        codes.loc[mask_nonnum] = pd.to_numeric(mapped, errors="coerce")

    # 3) still-na: extract digits from strings like "5 (Very satisfied)"
    mask_still_na = codes.isna()
    if mask_still_na.any():
        extracted = df.loc[mask_still_na, "RATING"].astype(str).str.extract(r"(\d+)", expand=False)
        codes.loc[mask_still_na] = pd.to_numeric(extracted, errors="coerce")

    # 4) enforce range 1..5, else set to NA
    codes = codes.where(codes.between(1, 5), other=pd.NA)

    # convert to nullable Int64 (keeps NA if present)
    try:
        df["RATING"] = codes.astype("Int64")
    except Exception:
        # fallback: keep as numeric float with NaNs if Int64 conversion fails
        df["RATING"] = pd.to_numeric(codes, errors="coerce")

    return df

def _detect_header_and_read(path):
    """Try header=0 first, then fallback to scanning header rows (first 15)"""
    try:
        xls = pd.ExcelFile(path, engine="openpyxl")
    except Exception as e:
        logger.exception("ExcelFile open failed: %s", e)
        raise

    sheet_order = (["FIELD"] if "FIELD" in xls.sheet_names else []) + [s for s in xls.sheet_names if s != "FIELD"]

    # Try header=0 first
    for s in sheet_order:
        try:
            tmp = pd.read_excel(path, sheet_name=s, engine="openpyxl", header=0)
            tmp = _clean_columns(tmp)
            if not tmp.dropna(how="all").empty:
                logger.info("Read sheet '%s' with header=0 (shape=%s)", s, tmp.shape)
                return tmp
        except Exception:
            continue

    # Fallback: header=None and detect header row
    for s in sheet_order:
        try:
            raw = pd.read_excel(path, sheet_name=s, engine="openpyxl", header=None)
            max_scan = min(15, len(raw))
            for idx in range(max_scan):
                rowvals = raw.iloc[idx].astype(str).str.strip().str.upper().tolist()
                rowset = set([v for v in rowvals if v and v != "NAN"])
                if ("VARIETY" in rowset) or ("RATING" in rowset) or ("DISTRICT" in rowset):
                    df = raw.iloc[idx+1:].copy()
                    df.columns = raw.iloc[idx].astype(str).str.strip().tolist()
                    df = _clean_columns(df)
                    logger.info("Detected header on row %s in sheet '%s' (shape=%s)", idx, s, df.shape)
                    return df
        except Exception:
            continue

    # Last-resort: first sheet header=0
    try:
        tmp = pd.read_excel(path, sheet_name=0, engine="openpyxl", header=0)
        tmp = _clean_columns(tmp)
        logger.info("Fallback read first sheet header=0 (shape=%s)", tmp.shape)
        return tmp
    except Exception:
        return pd.DataFrame()

def load_data() -> pd.DataFrame:
    # 1) Excel
    if os.path.exists(EXCEL_PATH):
        try:
            logger.info("Loading Excel: %s", EXCEL_PATH)
            df = _detect_header_and_read(EXCEL_PATH)
            if df is not None and not df.empty:
                df = map_varieties(df)
                df = normalize_rating_column(df)   # <--- overwrite RATING with numeric codes here
                logger.info("Excel loaded, final shape=%s", df.shape)
                return df
            else:
                logger.warning("Excel read returned empty; falling back to SAV.")
        except Exception as e:
            logger.exception("Excel load failed: %s", e)

    # 2) SAV fallback
    if os.path.exists(SAV_PATH):
        try:
            logger.info("Loading SAV: %s", SAV_PATH)
            df, _meta = pyreadstat.read_sav(SAV_PATH)
            df = _clean_columns(df)
            df = map_varieties(df)
            # For SAV most likely RATING is already numeric codes -> ensure Int64
            if "RATING" in df.columns:
                df["RATING"] = pd.to_numeric(df["RATING"], errors="coerce").where(lambda s: s.between(1,5), other=pd.NA)
                try:
                    df["RATING"] = df["RATING"].astype("Int64")
                except Exception:
                    df["RATING"] = pd.to_numeric(df["RATING"], errors="coerce")
            else:
                df = normalize_rating_column(df)
            logger.info("SAV loaded, final shape=%s", df.shape)
            return df
        except Exception as e:
            logger.exception("SAV load failed: %s", e)

    # 3) Sample fallback
    logger.warning("No data files found; using sample fallback.")
    sample = pd.DataFrame({
        "VARIETY": ["SC 301", "SC 419", "SC 301", "SC 423"],
        "RATING": [4, 3, 5, 2],
        "BUYING": ["SC 301", "SC 419", "SC 301", "SC 423"],
        "DISTRICT": ["D1", "D2", "D1", "D3"],
    })
    sample = map_varieties(sample)
    sample = normalize_rating_column(sample)
    return sample

# Load dataframe once at boot
df = load_data()
logger.info("Columns present: %s", list(df.columns))
logger.info("First 8 rows:\n%s", df.head(8).to_string())

# -------------------------
# Dash App
# -------------------------
app = Dash(__name__)
server = app.server
app.title = "Variety Satisfaction Dashboard"

def empty_figure():
    fig = go.Figure()
    fig.update_layout(plot_bgcolor="#262626", paper_bgcolor="#1e1e1e", font_color="#ffffff")
    return fig

# Layout
app.layout = html.Div([
    html.H1("🌾 Variety Satisfaction Dashboard", style={"textAlign":"center","color":"#ffffff","fontFamily":"Arial Black"}),
    html.Div([
        html.Div([
            html.Label("Select District:", style={"fontWeight":"bold","color":"#ffffff"}),
            dcc.Dropdown(id="district-dropdown", options=[] , placeholder="All Districts", clearable=True)
        ], style={"width":"45%","display":"inline-block","padding":"10px"}),
        html.Div([
            html.Label("Select Variety:", style={"fontWeight":"bold","color":"#ffffff"}),
            dcc.Dropdown(id="variety-dropdown", options=[], placeholder="All Varieties", clearable=True)
        ], style={"width":"45%","display":"inline-block","padding":"10px"})
    ], style={"marginTop":"20px","textAlign":"center"}),
    html.Br(),
    html.Div(id="summary-cards", style={"display":"flex","justifyContent":"center","flexWrap":"wrap"}),
    html.Br(),
    dcc.Graph(id="bar-chart", config={"displayModeBar": False}),
    dcc.Graph(id="rating-distribution", config={"displayModeBar": False}),
    dcc.Graph(id="variety-buying-bar", config={"displayModeBar": False}),
    html.Hr(),
    html.H3("Raw Data (all rows)"),
    dash_table.DataTable(
        id="raw-table",
        data=[],
        columns=[],
        page_size=15,
        style_table={"overflowX": "auto"},
        style_header={"backgroundColor": "#2e2e2e", "color": "#fff", "fontWeight": "bold"},
        style_cell={"backgroundColor": "#1e1e1e", "color": "#fff", "border": "1px solid #333"},
    )
], style={"backgroundColor":"#1e1e1e","padding":"30px"})

# Callback
@app.callback(
    [
        Output('bar-chart','figure'),
        Output('rating-distribution','figure'),
        Output('variety-buying-bar','figure'),
        Output('district-dropdown','options'),
        Output('variety-dropdown','options'),
        Output('summary-cards','children'),
        Output('raw-table','data'),
        Output('raw-table','columns'),
    ],
    [Input('district-dropdown','value'), Input('variety-dropdown','value')]
)
def update_dashboard(selected_district, selected_variety):
    global df
    empty_fig = empty_figure()
    empty_cards = []

    if df is None or (hasattr(df,"empty") and df.empty):
        return empty_fig, empty_fig, empty_fig, [], [], empty_cards, [], []

    filtered = df.copy()
    if selected_district and 'DISTRICT' in filtered.columns:
        filtered = filtered[filtered['DISTRICT'] == selected_district]
    if selected_variety and 'VARIETY' in filtered.columns:
        filtered = filtered[filtered['VARIETY'] == selected_variety]

    # Average rating per variety (uses numeric RATING)
    if 'VARIETY' in filtered.columns and 'RATING' in filtered.columns and not filtered['RATING'].dropna().empty:
        avg_rating = filtered.groupby('VARIETY', dropna=False)['RATING'].mean().reset_index()
        bar_fig = px.bar(avg_rating, x='VARIETY', y='RATING', title="Average Rating per Variety", color='VARIETY')
        bar_fig.update_layout(showlegend=False, plot_bgcolor='#262626', paper_bgcolor='#1e1e1e', font_color='#ffffff')
    else:
        bar_fig = empty_fig

    # Distribution: numeric rating distribution
    if 'RATING' in filtered.columns and not filtered['RATING'].dropna().empty:
        dist_fig = px.histogram(filtered, x='RATING', nbins=5, title="Rating Distribution (codes 1-5)")
        dist_fig.update_traces(opacity=0.85)
        dist_fig.update_layout(xaxis_title="Rating (1–5)", yaxis_title="Count", plot_bgcolor='#262626', paper_bgcolor='#1e1e1e', font_color='#ffffff')
    else:
        dist_fig = empty_fig

    # Buying counts
    if 'BUYING' in filtered.columns:
        buying_counts = filtered['BUYING'].astype(str).value_counts(dropna=False).reset_index()
        buying_counts.columns = ['VARIETY','Count']
        buy_fig = px.bar(buying_counts, x='VARIETY', y='Count', title="Willingness to Buy by Variety", color='VARIETY')
        buy_fig.update_layout(showlegend=False, plot_bgcolor='#262626', paper_bgcolor='#1e1e1e', font_color='#ffffff')
    else:
        buy_fig = empty_fig

    # Cards
    total_records = int(len(filtered))
    avg_rating_val = (round(float(filtered['RATING'].mean()), 2) if 'RATING' in filtered.columns and not filtered['RATING'].dropna().empty else "—")
    unique_varieties = int(filtered['VARIETY'].nunique(dropna=True)) if 'VARIETY' in filtered.columns else "—"

    card_style = {"padding":"20px","margin":"15px","border":"2px solid #444","borderRadius":"10px","width":"220px","textAlign":"center","boxShadow":"0px 4px 15px rgba(0,0,0,0.4)","backgroundColor":"#2e2e2e","color":"#f2f2f2"}
    cards = [
        html.Div([html.H4("Total Records"), html.P(total_records)], style=card_style),
        html.Div([html.H4("Average Rating"), html.P(avg_rating_val)], style=card_style),
        html.Div([html.H4("Unique Varieties"), html.P(unique_varieties)], style=card_style)
    ]

    district_options = [{"label": d, "value": d} for d in sorted(df['DISTRICT'].dropna().unique())] if 'DISTRICT' in df.columns else []
    variety_options  = [{"label": v, "value": v} for v in sorted(df['VARIETY'].dropna().unique())] if 'VARIETY' in df.columns else []

    # Raw table data/columns
    table_cols = [{"name": c, "id": c} for c in filtered.columns]
    table_data = filtered.to_dict("records")

    return bar_fig, dist_fig, buy_fig, district_options, variety_options, cards, table_data, table_cols

# Entrypoint
if __name__ == "__main__":
    app.run_server(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)
