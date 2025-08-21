# app.py
import os
import logging
import base64
import tempfile
from io import BytesIO

import pandas as pd
import pyreadstat
from dash import Dash, dcc, html, Input, Output, State
import plotly.express as px
import plotly.graph_objects as go

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Relative data paths
EXCEL_PATH = os.path.join("data", "FIELD DAYS VARIETIES SATISFACTION RATING.xlsx")
SAV_PATH = os.path.join("data", "FIELD DAYS VARIETIES SATISFACTION RATING.sav")

# Variety mapping (from your notebook)
variety_map = {
    1: "SC 301", 2: "SC 419", 3: "SC 423", 4: "SC 529", 5: "SC 555",
    6: "SC 653", 7: "SC 665", 8: "SC 729", 9: "SC Saga", 10: "SC Signal",
    11: "SC Serenade", 12: "Nerica-4", 13: "Sorghum",
}

def map_varieties(df):
    if "VARIETY" in df.columns:
        try:
            df["VARIETY"] = pd.to_numeric(df["VARIETY"], errors="coerce").astype("Int64")
        except Exception:
            pass
        df["VARIETY"] = df["VARIETY"].map(variety_map).fillna(df["VARIETY"])
    if "BUYING" in df.columns:
        try:
            df["BUYING"] = pd.to_numeric(df["BUYING"], errors="coerce").astype("Int64")
        except Exception:
            pass
        df["BUYING"] = df["BUYING"].map(variety_map).fillna(df["BUYING"])
    return df

def load_data():
    # Prefer Excel sheet 'FIELD' then SAV, else sample
    if os.path.exists(EXCEL_PATH):
        try:
            logger.info("Loading Excel: %s (sheet=FIELD)", EXCEL_PATH)
            df = pd.read_excel(EXCEL_PATH, sheet_name="FIELD", engine="openpyxl")
            df.columns = df.columns.str.strip()
            df = map_varieties(df)
            if "RATING" in df.columns:
                df["RATING"] = pd.to_numeric(df["RATING"], errors="coerce")
                df = df[df["RATING"].between(1, 5)]
            logger.info("Excel loaded, shape=%s", df.shape)
            return df
        except Exception as e:
            logger.exception("Failed to load Excel: %s", e)

    if os.path.exists(SAV_PATH):
        try:
            logger.info("Loading SAV: %s", SAV_PATH)
            df, meta = pyreadstat.read_sav(SAV_PATH)
            df.columns = df.columns.str.strip()
            df = map_varieties(df)
            if "RATING" in df.columns:
                df["RATING"] = pd.to_numeric(df["RATING"], errors="coerce")
                df = df[df["RATING"].between(1, 5)]
            logger.info("SAV loaded, shape=%s", df.shape)
            return df
        except Exception as e:
            logger.exception("Failed to load SAV: %s", e)

    # Fallback sample
    logger.warning("No data files found; using sample fallback.")
    sample = {
        "VARIETY": ["SC 301", "SC 419", "SC 301", "SC 423"],
        "RATING": [4, 3, 5, 2],
        "BUYING": ["SC 301", "SC 419", "SC 301", "SC 423"],
        "DISTRICT": ["D1", "D2", "D1", "D3"]
    }
    return pd.DataFrame(sample)

# Global dataframe
df = load_data()

# Debug prints (visible in Render logs)
logger.info("Columns present: %s", list(df.columns))
logger.info("First 5 rows:\n%s", df.head().to_string())

# Dash app
app = Dash(__name__)
server = app.server
app.title = "Variety Satisfaction Dashboard"

def empty_figure():
    fig = go.Figure()
    fig.update_layout(plot_bgcolor="#262626", paper_bgcolor="#1e1e1e", font_color="#ffffff")
    return fig

app.layout = html.Div([
    html.H1("🌾 Variety Satisfaction Dashboard", style={"textAlign":"center","color":"#ffffff","fontFamily":"Arial Black"}),
    html.Div([
        html.Div([
            html.Label("Select District:", style={"fontWeight":"bold","color":"#ffffff"}),
            dcc.Dropdown(id="district-dropdown", options=[] , placeholder="All Districts")
        ], style={"width":"45%","display":"inline-block","padding":"10px"}),
        html.Div([
            html.Label("Select Variety:", style={"fontWeight":"bold","color":"#ffffff"}),
            dcc.Dropdown(id="variety-dropdown", options=[], placeholder="Select Variety")
        ], style={"width":"45%","display":"inline-block","padding":"10px"})
    ], style={"marginTop":"20px","textAlign":"center"}),
    html.Br(),
    html.Div(id="summary-cards", style={"display":"flex","justifyContent":"center","flexWrap":"wrap"}),
    html.Br(),
    dcc.Graph(id="bar-chart", config={"displayModeBar": False}),
    dcc.Graph(id="rating-distribution", config={"displayModeBar": False}),
    dcc.Graph(id="variety-buying-bar", config={"displayModeBar": False})
], style={"backgroundColor":"#1e1e1e","padding":"30px"})

@app.callback(
    [
        Output('bar-chart','figure'),
        Output('rating-distribution','figure'),
        Output('variety-buying-bar','figure'),
        Output('district-dropdown','options'),
        Output('variety-dropdown','options'),
        Output('summary-cards','children')
    ],
    [Input('district-dropdown','value'), Input('variety-dropdown','value')]
)
def update_dashboard(selected_district, selected_variety):
    global df
    empty_fig = empty_figure()
    empty_cards = []

    if df is None or (hasattr(df,"empty") and df.empty):
        return empty_fig, empty_fig, empty_fig, [], [], empty_cards

    filtered = df.copy()
    if selected_district and 'DISTRICT' in df.columns:
        filtered = filtered[filtered['DISTRICT'] == selected_district]
    if selected_variety and 'VARIETY' in df.columns:
        filtered = filtered[filtered['VARIETY'] == selected_variety]

    if filtered.empty:
        return empty_fig, empty_fig, empty_fig, [], [], empty_cards

    # avg rating bar
    avg_rating = filtered.groupby('VARIETY', dropna=False)['RATING'].mean().reset_index()
    bar_fig = px.bar(avg_rating, x='VARIETY', y='RATING', title="Average Rating per Variety",
                     color='VARIETY')
    bar_fig.update_layout(showlegend=False, plot_bgcolor='#262626', paper_bgcolor='#1e1e1e', font_color='#ffffff')

    # distribution
    dist_fig = px.histogram(filtered, x='RATING', color='VARIETY', barmode='overlay', nbins=5, title="Rating Distribution by Variety")
    dist_fig.update_traces(opacity=0.7)
    dist_fig.update_layout(plot_bgcolor='#262626', paper_bgcolor='#1e1e1e', font_color='#ffffff')

    # buying counts
    if 'BUYING' in filtered.columns:
        buying_counts = filtered['BUYING'].value_counts(dropna=False).reset_index()
        buying_counts.columns = ['VARIETY','Count']
        buy_fig = px.bar(buying_counts, x='VARIETY', y='Count', title="Willingness to Buy by Variety", color='VARIETY')
        buy_fig.update_layout(showlegend=False, plot_bgcolor='#262626', paper_bgcolor='#1e1e1e', font_color='#ffffff')
    else:
        buy_fig = empty_fig

    # cards
    card_style = {"padding":"20px","margin":"15px","border":"2px solid #444","borderRadius":"10px","width":"220px","textAlign":"center","boxShadow":"0px 4px 15px rgba(0,0,0,0.4)","backgroundColor":"#2e2e2e","color":"#f2f2f2"}
    cards = [
        html.Div([html.H4("Total Records"), html.P(int(len(filtered)))], style=card_style),
        html.Div([html.H4("Average Rating"), html.P(round(float(filtered['RATING'].mean()),2))], style=card_style),
        html.Div([html.H4("Unique Varieties"), html.P(int(filtered['VARIETY'].nunique(dropna=True)))], style=card_style)
    ]

    district_options = [{"label": d, "value": d} for d in sorted(df['DISTRICT'].dropna().unique())] if 'DISTRICT' in df.columns else []
    variety_options = [{"label": v, "value": v} for v in sorted(df['VARIETY'].dropna().unique())] if 'VARIETY' in df.columns else []

    return bar_fig, dist_fig, buy_fig, district_options, variety_options, cards

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8051))
    app.run_server(debug=True, host="0.0.0.0", port=port)
