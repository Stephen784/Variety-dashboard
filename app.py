# app.py
import os
import logging
import base64
import io

import pandas as pd
import pyreadstat
import dash
from dash import dcc, html, Input, Output, State
import plotly.express as px
import plotly.graph_objects as go

# -------------------------
# Logging
# -------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------------
# Paths (relative)
# -------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
EXCEL_PATH = os.path.join(DATA_DIR, "FIELD DAYS VARIETIES SATISFACTION RATING.xlsx")
SAV_PATH = os.path.join(DATA_DIR, "FIELD DAYS VARIETIES SATISFACTION RATING.sav")

# -------------------------
# Exact 13-variety mapping
# -------------------------
variety_map = {
    1: "SC 301", 2: "SC 419", 3: "SC 423", 4: "SC 529", 5: "SC 555",
    6: "SC 653", 7: "SC 665", 8: "SC 729", 9: "SC Saga", 10: "SC Signal",
    11: "SC Serenade", 12: "Nerica-4", 13: "Sorghum",
}
ALLOWED_VARIETIES = [
    "SC 301","SC 419","SC 423","SC 529","SC 555",
    "SC 653","SC 665","SC 729","SC Saga","SC Signal",
    "SC Serenade","Nerica-4","Sorghum"
]

# -------------------------
# Rating mapping (text -> code)
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
def normalize_str_keep_none(x):
    if pd.isna(x):
        return None
    if isinstance(x, str):
        return x.strip()
    return str(x).strip()

def normalize_for_matching(x):
    if pd.isna(x):
        return None
    return str(x).strip().lower()

def _clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(axis=1, how="all")
    df.columns = df.columns.astype(str).str.strip()
    return df

def map_varieties(df: pd.DataFrame) -> pd.DataFrame:
    # Map numeric codes to names; keep existing names if already present
    if "VARIETY" in df.columns:
        numeric = pd.to_numeric(df["VARIETY"], errors="coerce")
        mapped = numeric.map(variety_map)
        df["VARIETY"] = mapped.combine_first(df["VARIETY"].astype(object))
    if "BUYING" in df.columns:
        numeric = pd.to_numeric(df["BUYING"], errors="coerce")
        mapped = numeric.map(variety_map)
        df["BUYING"] = mapped.combine_first(df["BUYING"].astype(object))
    return df

def normalize_rating_column_inplace(df: pd.DataFrame) -> pd.DataFrame:
    """Replace RATING column in-place with numeric codes (Int64 if possible)."""
    if "RATING" not in df.columns:
        return df

    # 1) try numeric conversion
    codes = pd.to_numeric(df["RATING"], errors="coerce")

    # 2) map text labels for non-numeric entries
    mask_nonnum = codes.isna()
    if mask_nonnum.any():
        mapped = df.loc[mask_nonnum, "RATING"].map(lambda x: rating_text_to_code.get(normalize_for_matching(x), pd.NA))
        codes.loc[mask_nonnum] = pd.to_numeric(mapped, errors="coerce")

    # 3) extract digits from strings like "5 (Very satisfied)"
    mask_still_na = codes.isna()
    if mask_still_na.any():
        extracted = df.loc[mask_still_na, "RATING"].astype(str).str.extract(r"(\d+)", expand=False)
        codes.loc[mask_still_na] = pd.to_numeric(extracted, errors="coerce")

    # 4) enforce range 1..5 (out-of-range -> NA)
    codes = codes.where(codes.between(1, 5), other=pd.NA)

    # 5) convert to nullable Int64 if possible
    try:
        df["RATING"] = codes.astype("Int64")
    except Exception:
        df["RATING"] = pd.to_numeric(codes, errors="coerce")

    return df

def normalize_text_columns_to_str(df: pd.DataFrame, cols):
    for c in cols:
        if c in df.columns:
            df[c] = df[c].apply(lambda x: normalize_str_keep_none(x) if pd.notna(x) else None)
    return df

def get_dropdown_options_sorted(df: pd.DataFrame, col: str):
    """Robust sort: numeric-like values sorted numerically (as strings), then text sorted alpha."""
    if col not in df.columns:
        return []
    vals = df[col].dropna().unique().tolist()
    nums = []
    strs = []
    for v in vals:
        try:
            n = float(v)
            nums.append((n, v))
        except Exception:
            strs.append(str(v))
    nums_sorted = [str(orig) for _, orig in sorted(nums, key=lambda t: t[0])]
    strs_sorted = sorted(set(strs), key=lambda s: s.lower())
    ordered = nums_sorted + strs_sorted
    return [{"label": v, "value": v} for v in ordered]

def get_allowed_variety_options(df: pd.DataFrame):
    present = set([str(v) for v in df.get("VARIETY", pd.Series([], dtype=object)).dropna().unique()])
    options = [{"label": v, "value": v} for v in ALLOWED_VARIETIES if v in present]
    return options

# -------------------------
# Excel header detection & read (robust)
# -------------------------
def _detect_header_and_read(path):
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

    # fallback: try to detect header row in first 15 rows
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

    # final fallback
    try:
        tmp = pd.read_excel(path, sheet_name=0, engine="openpyxl", header=0)
        tmp = _clean_columns(tmp)
        logger.info("Fallback read first sheet header=0 (shape=%s)", tmp.shape)
        return tmp
    except Exception:
        return pd.DataFrame()

# -------------------------
# Load data once at boot (Excel preferred, then SAV)
# -------------------------
def load_data():
    # Excel first
    if os.path.exists(EXCEL_PATH):
        try:
            logger.info("Loading Excel: %s", EXCEL_PATH)
            df_local = _detect_header_and_read(EXCEL_PATH)
            if df_local is not None and not df_local.empty:
                df_local = map_varieties(df_local)
                df_local = normalize_rating_column_inplace(df_local)
                df_local = normalize_text_columns_to_str(df_local, ["VARIETY", "BUYING", "DISTRICT"])
                logger.info("Excel loaded, final shape=%s", df_local.shape)
                return df_local
            else:
                logger.warning("Excel read returned empty; falling back to SAV.")
        except Exception as e:
            logger.exception("Excel load failed: %s", e)

    # SAV fallback
    if os.path.exists(SAV_PATH):
        try:
            logger.info("Loading SAV: %s", SAV_PATH)
            df_sav, _meta = pyreadstat.read_sav(SAV_PATH)
            df_sav = _clean_columns(df_sav)
            df_sav = map_varieties(df_sav)
            if "RATING" in df_sav.columns:
                df_sav["RATING"] = pd.to_numeric(df_sav["RATING"], errors="coerce").where(lambda s: s.between(1,5), other=pd.NA)
                try:
                    df_sav["RATING"] = df_sav["RATING"].astype("Int64")
                except Exception:
                    df_sav["RATING"] = pd.to_numeric(df_sav["RATING"], errors="coerce")
            else:
                df_sav = normalize_rating_column_inplace(df_sav)
            df_sav = normalize_text_columns_to_str(df_sav, ["VARIETY", "BUYING", "DISTRICT"])
            logger.info("SAV loaded, shape=%s", df_sav.shape)
            return df_sav
        except Exception as e:
            logger.exception("SAV load failed: %s", e)

    # fallback sample so app still boots
    logger.warning("No data files found; using sample fallback.")
    sample = pd.DataFrame({
        "VARIETY": ["SC 301", "SC 419", "SC 301", "SC 423"],
        "RATING": [4, 3, 5, 2],
        "BUYING": ["SC 301", "SC 419", "SC 301", "SC 423"],
        "DISTRICT": ["D1", "D2", "D1", "D3"]
    })
    sample = map_varieties(sample)
    sample = normalize_rating_column_inplace(sample)
    sample = normalize_text_columns_to_str(sample, ["VARIETY", "BUYING", "DISTRICT"])
    return sample

df = load_data()
logger.info("Columns present: %s", list(df.columns))
logger.info("First 6 rows:\n%s", df.head(6).to_string())

# -------------------------
# Dash app & layout
# -------------------------
app = dash.Dash(__name__)
app.title = "Variety Satisfaction Dashboard"

app.layout = html.Div([
    html.H1("\U0001F33E Variety Satisfaction Dashboard",
            style={"textAlign": "center", "color": "#ffffff", "fontFamily": "Arial Black"}),

    html.Div([
        dcc.Upload(
            id='upload-data',
            children=html.Button("Upload .sav", id="upload-btn", n_clicks=0,
                                 style={"backgroundColor": "#00bfff", "color": "#1e1e1e",
                                        "border": "none", "padding": "8px 12px",
                                        "borderRadius": "8px", "cursor": "pointer", "fontWeight": "bold"}),
            style={'display': 'inline-block', 'borderWidth': '0px', 'borderStyle': 'none',
                   'borderRadius': '8px', 'textAlign': 'left', 'margin': '10px'},
            accept='.sav', multiple=False
        )
    ], style={"textAlign": "left", "paddingLeft": "18px", "paddingTop": "6px"}),

    html.Div([
        html.Div([
            html.Label("Select District:", style={"fontWeight": "bold", "color": "#ffffff"}),
            dcc.Dropdown(
                id='district-dropdown',
                options=[],
                placeholder="All Districts",
                clearable=True,
                style={'backgroundColor': '#1E1E1E', 'color': '#00BFFF', 'border': '1px solid #00BFFF'}
            )
        ], style={"width": "45%", "display": "inline-block", "padding": "10px"}),

        html.Div([
            html.Label("Select Variety:", style={"fontWeight": "bold", "color": "#ffffff"}),
            dcc.Dropdown(
                id='variety-dropdown',
                options=[],
                placeholder="Select Variety",
                clearable=True,
                style={'backgroundColor': '#1E1E1E', 'color': '#00BFFF', 'border': '1px solid #00BFFF'}
            )
        ], style={"width": "45%", "display": "inline-block", "padding": "10px"})
    ], style={"marginTop": "20px", "textAlign": "center"}),

    html.Br(),

    html.Div(id='summary-cards', style={"display": "flex", "justifyContent": "center", "flexWrap": "wrap"}),

    html.Br(),

    dcc.Graph(id='bar-chart', config={"displayModeBar": False}),
    dcc.Graph(id='rating-distribution', config={"displayModeBar": False}),
    dcc.Graph(id='variety-buying-bar', config={"displayModeBar": False})
], style={"backgroundColor": "#1e1e1e", "padding": "30px"})

# -------------------------
# Callback
# -------------------------
@app.callback(
    [
        Output('bar-chart', 'figure'),
        Output('rating-distribution', 'figure'),
        Output('variety-buying-bar', 'figure'),
        Output('district-dropdown', 'options'),
        Output('variety-dropdown', 'options'),
        Output('summary-cards', 'children')
    ],
    [
        Input('upload-data', 'contents'),
        Input('district-dropdown', 'value'),
        Input('variety-dropdown', 'value')
    ],
    State('upload-data', 'filename')
)
def update_dashboard(contents, selected_district, selected_variety, filename):
    global df

    # base empty figure
    empty_fig = go.Figure()
    empty_fig.update_layout(plot_bgcolor='#262626', paper_bgcolor='#1e1e1e', font_color='#ffffff')
    empty_cards = []

    # handle uploaded SAV - replace df in-memory for the session
    if contents:
        try:
            content_type, content_string = contents.split(',', 1)
            decoded = base64.b64decode(content_string)
            tmp_path = "uploaded_file.sav"
            with open(tmp_path, "wb") as f:
                f.write(decoded)
            df_new, _ = pyreadstat.read_sav(tmp_path)
            df_new = _clean_columns(df_new)
            df_new = map_varieties(df_new)
            df_new = normalize_rating_column_inplace(df_new)
            df_new = normalize_text_columns_to_str(df_new, ["VARIETY", "BUYING", "DISTRICT"])
            df = df_new
        except Exception as e:
            err = html.Div(f"Upload error: {e}", style={"color": "#ff6b6b"})
            return empty_fig, empty_fig, empty_fig, [], [], [err]

    # if no data -> return placeholders
    if df is None or df.empty:
        return empty_fig, empty_fig, empty_fig, [], [], empty_cards

    # Filtering: selected values are strings; compare normalized
    filtered_df = df.copy()
    if selected_district and 'DISTRICT' in filtered_df.columns:
        filtered_df = filtered_df[filtered_df['DISTRICT'].apply(lambda x: normalize_for_matching(x) == normalize_for_matching(selected_district))]
    if selected_variety and 'VARIETY' in filtered_df.columns:
        filtered_df = filtered_df[filtered_df['VARIETY'].apply(lambda x: normalize_for_matching(x) == normalize_for_matching(selected_variety))]

    # Charts
    if filtered_df.empty:
        bar_fig, dist_fig, buy_fig = empty_fig, empty_fig, empty_fig
        cards = empty_cards
    else:
        # Average rating per variety (use numeric RATING)
        if 'VARIETY' in filtered_df.columns and 'RATING' in filtered_df.columns and not filtered_df['RATING'].dropna().empty:
            avg_rating = filtered_df.groupby('VARIETY', dropna=False)['RATING'].mean().reset_index()
            bar_fig = px.bar(avg_rating, x='VARIETY', y='RATING',
                             title="Average Rating per Variety",
                             color='VARIETY', labels={'RATING': 'Average Rating'},
                             color_discrete_sequence=px.colors.sequential.Darkmint)
            bar_fig.update_layout(showlegend=False, plot_bgcolor='#262626', paper_bgcolor='#1e1e1e', font_color='#ffffff')
        else:
            bar_fig = empty_fig

        # Distribution by numeric RATING
        if 'RATING' in filtered_df.columns and not filtered_df['RATING'].dropna().empty:
            dist_fig = px.histogram(filtered_df, x='RATING', color='VARIETY', barmode='overlay', nbins=5,
                                    title="Rating Distribution by Variety",
                                    color_discrete_sequence=px.colors.sequential.Tealgrn_r)
            dist_fig.update_traces(opacity=0.7)
            dist_fig.update_layout(plot_bgcolor='#262626', paper_bgcolor='#1e1e1e', font_color='#ffffff')
        else:
            dist_fig = empty_fig

        # Buying counts
        if 'BUYING' in filtered_df.columns:
            buying_counts = filtered_df['BUYING'].astype(str).value_counts(dropna=False).reset_index()
            buying_counts.columns = ['VARIETY', 'Count']
            buy_fig = px.bar(buying_counts, x='VARIETY', y='Count', title="Willingness to Buy by Variety",
                             color='VARIETY', color_discrete_sequence=px.colors.sequential.Magma_r)
            buy_fig.update_layout(showlegend=False, plot_bgcolor='#262626', paper_bgcolor='#1e1e1e', font_color='#ffffff')
        else:
            buy_fig = empty_fig

        # summary cards
        card_style = {
            "padding": "20px", "margin": "15px", "border": "2px solid #444",
            "borderRadius": "10px", "width": "220px", "textAlign": "center",
            "boxShadow": "0px 4px 15px rgba(0,0,0,0.4)", "backgroundColor": "#2e2e2e", "color": "#f2f2f2"
        }
        cards = [
            html.Div([html.H4("Total Records"), html.P(int(len(filtered_df)))], style=card_style),
            html.Div([html.H4("Average Rating"), html.P(round(float(filtered_df['RATING'].mean()), 2))], style=card_style) if 'RATING' in filtered_df.columns and not filtered_df['RATING'].dropna().empty else html.Div([html.H4("Average Rating"), html.P("—")], style=card_style),
            html.Div([html.H4("Unique Varieties"), html.P(int(filtered_df['VARIETY'].nunique(dropna=True)))], style=card_style) if 'VARIETY' in filtered_df.columns else html.Div([html.H4("Unique Varieties"), html.P("—")], style=card_style)
        ]

    # Options for dropdowns (districts: robust sorted; variety: only allowed 13 in set order)
    district_options = get_dropdown_options_sorted(df, 'DISTRICT') if 'DISTRICT' in df.columns else []
    variety_options = get_allowed_variety_options(df)

    return bar_fig, dist_fig, buy_fig, district_options, variety_options, cards

# Entrypoint
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run_server(debug=False, host="0.0.0.0", port=port)
