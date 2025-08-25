# app.py
import logging
import os
import pandas as pd
import pyreadstat
from dash import Dash, dcc, html, Input, Output
from dash import dash_table
import plotly.express as px
import plotly.graph_objects as go

# ------------------------------------------------------------------
# Logging
# ------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Paths (work locally & in containers)
# ------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
EXCEL_PATH = os.path.join(DATA_DIR, "FIELD DAYS VARIETIES SATISFACTION RATING.xlsx")
SAV_PATH   = os.path.join(DATA_DIR, "FIELD DAYS VARIETIES SATISFACTION RATING.sav")

# ------------------------------------------------------------------
# Variety mapping
# ------------------------------------------------------------------
variety_map = {
    1: "SC 301", 2: "SC 419", 3: "SC 423", 4: "SC 529", 5: "SC 555",
    6: "SC 653", 7: "SC 665", 8: "SC 729", 9: "SC Saga", 10: "SC Signal",
    11: "SC Serenade", 12: "Nerica-4", 13: "Sorghum",
}

def map_varieties(df: pd.DataFrame) -> pd.DataFrame:
    """Map numeric codes to names without changing types unexpectedly."""
    if "VARIETY" in df.columns:
        num = pd.to_numeric(df["VARIETY"], errors="coerce")
        mapped = num.map(variety_map)
        df["VARIETY"] = mapped.combine_first(df["VARIETY"].astype(object))
    if "BUYING" in df.columns:
        num = pd.to_numeric(df["BUYING"], errors="coerce")
        mapped = num.map(variety_map)
        df["BUYING"] = mapped.combine_first(df["BUYING"].astype(object))
    return df

def _clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(axis=1, how="all")
    df.columns = (
        df.columns.astype(str)
        .str.strip()
        .str.replace(r"\s+", " ", regex=True)
        .str.upper()
    )
    # normalize a few common variants
    aliases = {"RATINGS": "RATING", "VARIETIES": "VARIETY", "WILLINGNESS TO BUY": "BUYING"}
    df = df.rename(columns={k: v for k, v in aliases.items() if k in df.columns})
    return df

def load_data() -> pd.DataFrame:
    # Prefer Excel (as you did in Jupyter)
    if os.path.exists(EXCEL_PATH):
        try:
            logger.info("Loading Excel: %s", EXCEL_PATH)
            # Try the FIELD sheet first, else fall back to first sheet
            xls = pd.ExcelFile(EXCEL_PATH, engine="openpyxl")
            sheet = "FIELD" if "FIELD" in xls.sheet_names else xls.sheet_names[0]
            df = pd.read_excel(xls, sheet_name=sheet)
            df = _clean_columns(df)
            df = map_varieties(df)
            logger.info("Excel loaded, shape=%s; columns=%s", df.shape, list(df.columns))
            return df
        except Exception as e:
            logger.exception("Failed to load Excel: %s", e)

    # Fallback: SAV
    if os.path.exists(SAV_PATH):
        try:
            logger.info("Loading SAV: %s", SAV_PATH)
            df, _ = pyreadstat.read_sav(SAV_PATH)
            df = _clean_columns(df)
            df = map_varieties(df)
            logger.info("SAV loaded, shape=%s; columns=%s", df.shape, list(df.columns))
            return df
        except Exception as e:
            logger.exception("Failed to load SAV: %s", e)

    # Last resort: small sample so app still boots
    logger.warning("No data files found; using sample fallback.")
    return pd.DataFrame({
        "VARIETY": ["SC 301", "SC 419", "SC 301", "SC 423"],
        "RATING": [4, 3, 5, 2],
        "BUYING": ["SC 301", "SC 419", "SC 301", "SC 423"],
        "DISTRICT": ["D1", "D2", "D1", "D3"],
    })

# Load once at boot
df = load_data()
logger.info("Preview:\n%s", df.head().to_string())

# ------------------------------------------------------------------
# Dash app
# ------------------------------------------------------------------
app = Dash(__name__)
server = app.server
app.title = "Variety Satisfaction Dashboard"

def empty_figure():
    fig = go.Figure()
    fig.update_layout(plot_bgcolor="#262626", paper_bgcolor="#1e1e1e", font_color="#ffffff")
    return fig

app.layout = html.Div([
    html.H1("🌾 Variety Satisfaction Dashboard",
            style={"textAlign":"center","color":"#ffffff","fontFamily":"Arial Black"}),

    html.Div([
        html.Div([
            html.Label("Select District:", style={"fontWeight":"bold","color":"#ffffff"}),
            dcc.Dropdown(id="district-dropdown", options=[], placeholder="All Districts")
        ], style={"width":"45%","display":"inline-block","padding":"10px"}),
        html.Div([
            html.Label("Select Variety:", style={"fontWeight":"bold","color":"#ffffff"}),
            dcc.Dropdown(id="variety-dropdown", options=[], placeholder="All Varieties")
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

    if df is None or (hasattr(df, "empty") and df.empty):
        return empty_fig, empty_fig, empty_fig, [], [], empty_cards, [], []

    filtered = df.copy()
    if selected_district and 'DISTRICT' in filtered.columns:
        filtered = filtered[filtered['DISTRICT'] == selected_district]
    if selected_variety and 'VARIETY' in filtered.columns:
        filtered = filtered[filtered['VARIETY'] == selected_variety]

    # Graphs (defensive: only plot if columns exist)
    if {"VARIETY", "RATING"}.issubset(filtered.columns):
        avg_rating = filtered.groupby('VARIETY', dropna=False)['RATING'].mean().reset_index()
        bar_fig = px.bar(avg_rating, x='VARIETY', y='RATING', title="Average Rating per Variety", color='VARIETY')
        bar_fig.update_layout(showlegend=False, plot_bgcolor='#262626', paper_bgcolor='#1e1e1e', font_color='#ffffff')

        dist_fig = px.histogram(filtered, x='RATING', color='VARIETY', barmode='overlay', nbins=10,
                                title="Rating Distribution by Variety")
        dist_fig.update_traces(opacity=0.7)
        dist_fig.update_layout(plot_bgcolor='#262626', paper_bgcolor='#1e1e1e', font_color='#ffffff')
    else:
        bar_fig = empty_fig
        dist_fig = empty_fig

    if 'BUYING' in filtered.columns:
        buying_counts = filtered['BUYING'].astype(str).value_counts(dropna=False).reset_index()
        buying_counts.columns = ['VARIETY','Count']
        buy_fig = px.bar(buying_counts, x='VARIETY', y='Count', title="Willingness to Buy by Variety", color='VARIETY')
        buy_fig.update_layout(showlegend=False, plot_bgcolor='#262626', paper_bgcolor='#1e1e1e', font_color='#ffffff')
    else:
        buy_fig = empty_fig

    # Cards
    card_style = {
        "padding":"20px","margin":"15px","border":"2px solid #444","borderRadius":"10px",
        "width":"220px","textAlign":"center","boxShadow":"0px 4px 15px rgba(0,0,0,0.4)",
        "backgroundColor":"#2e2e2e","color":"#f2f2f2"
    }
    total_records = int(len(filtered))
    avg_rating_val = (round(float(filtered['RATING'].mean()), 2)
                      if "RATING" in filtered.columns and not filtered["RATING"].dropna().empty else "—")
    unique_varieties = (int(filtered['VARIETY'].nunique(dropna=True))
                        if "VARIETY" in filtered.columns else "—")
    cards = [
        html.Div([html.H4("Total Records"), html.P(total_records)], style=card_style),
        html.Div([html.H4("Average Rating"), html.P(avg_rating_val)], style=card_style),
        html.Div([html.H4("Unique Varieties"), html.P(unique_varieties)], style=card_style)
    ]

    # Dropdown options (from full df, not filtered)
    district_options = [{"label": d, "value": d} for d in sorted(df['DISTRICT'].dropna().unique())] if 'DISTRICT' in df.columns else []
    variety_options  = [{"label": v, "value": v} for v in sorted(df['VARIETY'].dropna().unique())] if 'VARIETY' in df.columns else []

    # Raw table
    table_cols = [{"name": c, "id": c} for c in filtered.columns]
    table_data = filtered.to_dict("records")

    return bar_fig, dist_fig, buy_fig, district_options, variety_options, cards, table_data, table_cols

if __name__ == "__main__":
    # Railway provides PORT; default to 8080 if missing
    port = int(os.environ.get("PORT", "8080"))
    # debug=False avoids the reloader starting two processes (which can mask crashes)
    app.run_server(debug=False, host="0.0.0.0", port=port)
