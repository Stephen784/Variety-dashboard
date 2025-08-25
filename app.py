# app.py
import os
import logging
import re
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
# Exact variety mapping (the 13 you provided)
# -------------------------
variety_map = {
    1: "SC 301", 2: "SC 419", 3: "SC 423", 4: "SC 529", 5: "SC 555",
    6: "SC 653", 7: "SC 665", 8: "SC 729", 9: "SC Saga", 10: "SC Signal",
    11: "SC Serenade", 12: "Nerica-4", 13: "Sorghum",
}

# -------------------------
# Rating text ↔ code maps
# -------------------------
rating_text_to_code = {
    "very dissatisfied": 1,
    "dissatisfied": 2,
    "neutral": 3,
    "satisfied": 4,
    "very satisfied": 5,
}

rating_code_to_text = {v: k.title() for k, v in rating_text_to_code.items()}

# -------------------------
# Helpers for loading + cleaning
# -------------------------
def normalize_str(x):
    if pd.isna(x):
        return None
    if isinstance(x, str):
        return x.strip().lower()
    return str(x).strip().lower()

def map_varieties(df: pd.DataFrame) -> pd.DataFrame:
    """Map numeric codes to names but keep existing names if present."""
    if "VARIETY" in df.columns:
        numeric = pd.to_numeric(df["VARIETY"], errors="coerce")
        mapped = numeric.map(variety_map)
        df["VARIETY"] = mapped.combine_first(df["VARIETY"].astype(object))
    if "BUYING" in df.columns:
        numeric = pd.to_numeric(df["BUYING"], errors="coerce")
        mapped = numeric.map(variety_map)
        df["BUYING"] = mapped.combine_first(df["BUYING"].astype(object))
    return df

def coerce_ratings(df: pd.DataFrame) -> pd.DataFrame:
    """
    Create:
      - RATING_RAW (original)
      - RATING_CODE (1-5 numeric if can determine)
      - RATING_TEXT (canonical text from code if code exists)
    Do not drop rows.
    """
    if "RATING" not in df.columns:
        df["RATING_RAW"] = pd.NA
        df["RATING_CODE"] = pd.NA
        df["RATING_TEXT"] = pd.NA
        return df

    df["RATING_RAW"] = df["RATING"]

    # 1) Try numeric conversion first
    codes = pd.to_numeric(df["RATING"], errors="coerce")

    # 2) For non-numeric, map common text labels
    mask_nonnum = codes.isna()
    if mask_nonnum.any():
        mapped = df.loc[mask_nonnum, "RATING"].map(lambda x: rating_text_to_code.get(normalize_str(x), pd.NA))
        codes.loc[mask_nonnum] = pd.to_numeric(mapped, errors="coerce")

    # 3) If still NaN, try extracting first integer in string (e.g., "5 (Very satisfied)")
    mask_still_na = codes.isna()
    if mask_still_na.any():
        extracted = df.loc[mask_still_na, "RATING"].astype(str).str.extract(r"(\d+)", expand=False)
        codes.loc[mask_still_na] = pd.to_numeric(extracted, errors="coerce")

    # Keep codes as floats (NaN for unknown); that's fine for grouping/mean calculations
    df["RATING_CODE"] = codes

    # Create canonical text from code when available; else keep original text (title-cased)
    df["RATING_TEXT"] = df["RATING_CODE"].map(lambda v: rating_code_to_text.get(int(v), pd.NA) if pd.notna(v) else pd.NA)
    # If RATING_TEXT is NA, fill with original (cleaned)
    df["RATING_TEXT"] = df["RATING_TEXT"].fillna(df["RATING_RAW"].astype(str))

    return df

def _clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    # drop fully empty columns and normalize whitespace in column names
    df = df.dropna(axis=1, how="all")
    df.columns = df.columns.astype(str).str.strip()
    return df

def _detect_header_and_read(path):
    """Try header=0 first, then fallback to scanning header rows (first 15)"""
    try:
        xls = pd.ExcelFile(path, engine="openpyxl")
    except Exception as e:
        raise

    # prefer FIELD sheet if present
    sheet_order = (["FIELD"] if "FIELD" in xls.sheet_names else []) + [s for s in xls.sheet_names if s != "FIELD"]

    # try header=0
    for s in sheet_order:
        try:
            tmp = pd.read_excel(path, sheet_name=s, engine="openpyxl", header=0)
            tmp = _clean_columns(tmp)
            if not tmp.dropna(how="all").empty:
                # if we have at least one column that looks like 'VARIETY' or 'RATING', accept
                cols = [c.strip().upper() for c in tmp.columns]
                if any("VARIETY" in c.upper() for c in tmp.columns) or any("RATING" in c.upper() for c in tmp.columns):
                    logger.info("Read sheet '%s' with header=0 (shape=%s)", s, tmp.shape)
                    return tmp
                # else still accept because user wants all rows, we'll fallback to broader detection if needed
                return tmp
        except Exception:
            continue

    # fallback: read header=None and detect the header row
    for s in sheet_order:
        try:
            raw = pd.read_excel(path, sheet_name=s, engine="openpyxl", header=None)
            # scan first 15 rows for a header-like row
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

    # if nothing worked, try first sheet header=0 to at least get something
    try:
        tmp = pd.read_excel(path, sheet_name=0, engine="openpyxl", header=0)
        tmp = _clean_columns(tmp)
        logger.info("Fallback read first sheet header=0 (shape=%s)", tmp.shape)
        return tmp
    except Exception:
        # final fallback
        return pd.DataFrame()

def load_data() -> pd.DataFrame:
    # Prefer Excel
    if os.path.exists(EXCEL_PATH):
        try:
            logger.info("Loading Excel: %s", EXCEL_PATH)
            df = _detect_header_and_read(EXCEL_PATH)
            if df is not None and not df.empty:
                df = map_varieties(df)
                df = coerce_ratings(df)
                logger.info("Excel loaded, final shape=%s", df.shape)
                return df
            else:
                logger.warning("Excel read returned empty dataframe; falling back to SAV")
        except Exception as e:
            logger.exception("Excel load failed: %s", e)

    # Try SAV
    if os.path.exists(SAV_PATH):
        try:
            logger.info("Loading SAV: %s", SAV_PATH)
            df, _meta = pyreadstat.read_sav(SAV_PATH)
            df = _clean_columns(df)
            df = map_varieties(df)
            # For SAV, RATING likely numeric codes already
            if "RATING" in df.columns:
                df["RATING_CODE"] = pd.to_numeric(df["RATING"], errors="coerce")
                df["RATING_TEXT"] = df["RATING_CODE"].map(lambda v: rating_code_to_text.get(int(v), pd.NA) if pd.notna(v) else pd.NA)
            else:
                df = coerce_ratings(df)
            logger.info("SAV loaded, shape=%s", df.shape)
            return df
        except Exception as e:
            logger.exception("SAV load failed: %s", e)

    # Final fallback (tiny sample so the app boots)
    logger.warning("No data files found; using sample fallback.")
    sample = pd.DataFrame({
        "VARIETY": ["SC 301", "SC 419", "SC 301", "SC 423"],
        "RATING": [4, 3, 5, 2],
        "BUYING": ["SC 301", "SC 419", "SC 301", "SC 423"],
        "DISTRICT": ["D1", "D2", "D1", "D3"],
    })
    sample = map_varieties(sample)
    sample = coerce_ratings(sample)
    return sample

# Load once
df = load_data()
logger.info("Columns present: %s", list(df.columns))
logger.info("First 5 rows:\n%s", df.head(8).to_string())

# -------------------------
# Dash app
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
            dcc.Dropdown(id="variety-dropdown", options=[], placeholder="Select Variety", clearable=True)
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

    # Bar: average by variety (use numeric codes)
    if 'VARIETY' in filtered.columns and 'RATING_CODE' in filtered.columns and not filtered['RATING_CODE'].dropna().empty:
        avg_rating = filtered.groupby('VARIETY', dropna=False)['RATING_CODE'].mean().reset_index()
        bar_fig = px.bar(avg_rating, x='VARIETY', y='RATING_CODE', title="Average Rating per Variety", color='VARIETY')
        bar_fig.update_layout(showlegend=False, plot_bgcolor='#262626', paper_bgcolor='#1e1e1e', font_color='#ffffff')
    else:
        bar_fig = empty_fig

    # Distribution: numeric rating distribution
    if 'RATING_CODE' in filtered.columns and not filtered['RATING_CODE'].dropna().empty:
        dist_fig = px.histogram(filtered, x='RATING_CODE', nbins=5, title="Rating Distribution (codes 1-5)")
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
    avg_rating_val = (round(float(filtered['RATING_CODE'].mean()), 2) if 'RATING_CODE' in filtered.columns and not filtered['RATING_CODE'].dropna().empty else "—")
    unique_varieties = int(filtered['VARIETY'].nunique(dropna=True)) if 'VARIETY' in filtered.columns else "—"

    card_style = {"padding":"20px","margin":"15px","border":"2px solid #444","borderRadius":"10px","width":"220px","textAlign":"center","boxShadow":"0px 4px 15px rgba(0,0,0,0.4)","backgroundColor":"#2e2e2e","color":"#f2f2f2"}
    cards = [
        html.Div([html.H4("Total Records"), html.P(total_records)], style=card_style),
        html.Div([html.H4("Average Rating"), html.P(avg_rating_val)], style=card_style),
        html.Div([html.H4("Unique Varieties"), html.P(unique_varieties)], style=card_style)
    ]

    district_options = [{"label": d, "value": d} for d in sorted(df['DISTRICT'].dropna().unique())] if 'DISTRICT' in df.columns else []
    variety_options  = [{"label": v, "value": v} for v in sorted(df['VARIETY'].dropna().unique())] if 'VARIETY' in df.columns else []

    # Raw table
    table_cols = [{"name": c, "id": c} for c in filtered.columns]
    table_data = filtered.to_dict("records")

    return bar_fig, dist_fig, buy_fig, district_options, variety_options, cards, table_data, table_cols

# Entrypoint
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run_server(debug=False, host="0.0.0.0", port=port)
