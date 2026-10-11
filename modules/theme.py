"""Obsidian visual identity: one stylesheet plus a few HTML components.

Design idea: the product's whole job is one comparison, "what staying could
cost" versus "what leaving costs". That comparison is the hero of the page.
Everything else stays quiet, so the verdict is the thing people remember.

Palette (aubergine-black glass, with a violet sheen):
    ink     #14121A   page
    panel   #1D1A26   raised surfaces
    line    #332E42   structure and dividers
    text    #ECE8F2   primary text
    muted   #A39CB6   secondary text
    sheen   #8C7BFF   interaction accent
Verdict colours are semantic only: stay #4FD1B5, review #F2B84B, exit #F0646A.
Every verdict is also spelled out in words, never colour alone.
"""
from html import escape

import streamlit as st

VERDICTS = {
    "STAY": {"word": "Stay", "color": "#4FD1B5",
             "line": "Expected loss is lower than the cost of leaving."},
    "EXIT": {"word": "Exit", "color": "#F0646A",
             "line": "Expected loss is higher than the cost of leaving."},
    "REVIEW": {"word": "Review", "color": "#F2B84B",
               "line": "The model cannot give a safe call with the data it has."},
}

# Reuse these in deps.to_flow_elements so the graph matches the page.
NODE_COLORS = {
    "healthy": "#4FD1B5",
    "warning": "#F2B84B",
    "critical": "#F0646A",
    "unknown": "#6F6883",
}

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=IBM+Plex+Sans:wght@400;500;600&display=swap');

:root {
  --ink: #14121A; --panel: #1D1A26; --line: #332E42;
  --text: #ECE8F2; --muted: #A39CB6; --sheen: #8C7BFF;
  --serif: 'Instrument Serif', 'Iowan Old Style', Georgia, serif;
  --sans: 'IBM Plex Sans', system-ui, -apple-system, 'Segoe UI', sans-serif;
}

html, body, [data-testid="stAppViewContainer"], [data-testid="stSidebar"] {
  font-family: var(--sans);
}
[data-testid="stAppViewContainer"] { background: var(--ink); }
[data-testid="stHeader"] { background: transparent; }
#MainMenu, footer { visibility: hidden; }
.block-container { max-width: 1240px; padding-top: 2.2rem; padding-bottom: 5rem; }

/* Sidebar: a quiet control rail */
[data-testid="stSidebar"] { background: var(--panel); border-right: 1px solid var(--line); }
[data-testid="stSidebar"] h2 { font-family: var(--serif); font-weight: 400; font-size: 1.7rem; letter-spacing: 0; }
[data-testid="stSidebar"] h3 {
  font-family: var(--sans); font-size: .95rem; font-weight: 600; color: var(--text);
  margin-top: 1.4rem; padding-top: 1rem; border-top: 1px solid var(--line);
}
[data-testid="stSidebar"] label p, [data-testid="stSidebar"] [data-testid="stCaptionContainer"] { color: var(--muted); }

/* Inputs and buttons */
input, textarea, [data-baseweb="select"] > div { border-radius: 6px !important; }
.stButton > button {
  border-radius: 6px; border: 1px solid var(--line); background: transparent; color: var(--text);
  transition: border-color .15s, background .15s;
}
.stButton > button:hover { border-color: var(--sheen); background: rgba(140,123,255,.10); color: var(--text); }
:focus-visible { outline: 2px solid var(--sheen) !important; outline-offset: 2px; }

/* Headings in the main column */
.ob-section { margin: 3rem 0 1rem; }
.ob-section h2 { font-family: var(--serif); font-weight: 400; font-size: 2rem; margin: 0; color: var(--text); }
.ob-section p { margin: .25rem 0 0; color: var(--muted); max-width: 70ch; font-size: .95rem; }

/* Metrics: no boxes, just a rule and a number */
[data-testid="stMetric"] { border-top: 1px solid var(--line); padding-top: .8rem; }
[data-testid="stMetricLabel"] p { color: var(--muted); font-size: .85rem; }
[data-testid="stMetricValue"] { font-family: var(--sans); font-variant-numeric: tabular-nums; font-weight: 500; font-size: 1.9rem; }

/* Tables */
[data-testid="stTable"] table { border-collapse: collapse; font-size: .9rem; width: 100%; }
[data-testid="stTable"] th { color: var(--muted); font-weight: 500; text-align: left; border-bottom: 1px solid var(--line); }
[data-testid="stTable"] td { border-bottom: 1px solid rgba(51,46,66,.6); font-variant-numeric: tabular-nums; }
[data-testid="stExpander"] { border: 1px solid var(--line); border-radius: 8px; background: transparent; }
iframe { border-radius: 10px; border: 1px solid var(--line); }

/* Masthead */
.ob-mast { display: flex; align-items: center; gap: 1.1rem; margin-bottom: .6rem; }
.ob-mast svg { flex: none; }
.ob-mast h1 { font-family: var(--serif); font-weight: 400; font-size: 3.4rem; line-height: 1; margin: 0; padding: 0; color: var(--text); }
.ob-mast p { margin: .35rem 0 0; color: var(--muted); font-size: 1rem; max-width: 62ch; }
.ob-chips { display: flex; flex-wrap: wrap; gap: .5rem; margin: 1rem 0 1.6rem; }
.ob-chip { font-size: .82rem; color: var(--muted); border: 1px solid var(--line); border-radius: 999px; padding: .2rem .75rem; }

/* Verdict: the one memorable element */
.ob-verdict {
  display: grid; grid-template-columns: minmax(220px, 1fr) minmax(300px, 1.6fr); gap: 2.5rem;
  border: 1px solid var(--line); border-radius: 14px; background: var(--panel);
  padding: 2rem 2.2rem; margin: .4rem 0 2rem;
}
.ob-verdict .ob-word { font-family: var(--serif); font-size: clamp(4rem, 9vw, 7rem); line-height: .9; margin: 0; }
.ob-verdict .ob-line { margin: .8rem 0 0; color: var(--text); font-size: 1.05rem; max-width: 30ch; }
.ob-verdict .ob-adv { margin: .6rem 0 0; color: var(--muted); font-size: .9rem; font-variant-numeric: tabular-nums; }
.ob-bars { display: flex; flex-direction: column; justify-content: center; gap: 1.3rem; }
.ob-bar-head { display: flex; justify-content: space-between; align-items: baseline; font-size: .92rem; color: var(--muted); margin-bottom: .4rem; }
.ob-bar-head b { color: var(--text); font-weight: 500; font-size: 1.15rem; font-variant-numeric: tabular-nums; }
.ob-track { height: 14px; background: rgba(255,255,255,.05); border-radius: 3px; overflow: hidden; }
.ob-fill { height: 100%; border-radius: 3px; transform-origin: left; animation: ob-grow .45s ease-out both; }
@keyframes ob-grow { from { transform: scaleX(0); } to { transform: scaleX(1); } }
.ob-foot { grid-column: 1 / -1; display: flex; flex-wrap: wrap; gap: 1.5rem; align-items: baseline;
  border-top: 1px solid var(--line); padding-top: 1rem; color: var(--muted); font-size: .88rem; }
.ob-foot details { flex: 1 1 100%; }
.ob-foot summary { cursor: pointer; color: var(--text); }
.ob-foot details p { max-width: 80ch; line-height: 1.55; margin: .6rem 0 0; }

@media (max-width: 820px) {
  .ob-verdict { grid-template-columns: 1fr; gap: 1.5rem; padding: 1.4rem; }
  .ob-mast h1 { font-size: 2.6rem; }
}
@media (prefers-reduced-motion: reduce) { .ob-fill { animation: none; } }
</style>
"""


def _html(markup):
    """Render raw HTML. st.html avoids Markdown/LaTeX mangling of "$" amounts."""
    if hasattr(st, "html"):
        st.html(markup)
    else:  # older Streamlit: escape "$" so it is not read as math
        st.markdown(markup.replace("$", "&#36;"), unsafe_allow_html=True)


def _usd(value):
    return "$" + f"{value:,.0f}"


def inject_css():
    st.markdown(CSS, unsafe_allow_html=True)


SHARD = (
    '<svg width="56" height="56" viewBox="0 0 56 56" aria-hidden="true">'
    '<defs><linearGradient id="sh" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="#8C7BFF"/><stop offset=".55" stop-color="#2A2540"/>'
    '<stop offset="1" stop-color="#4FD1B5"/></linearGradient></defs>'
    '<polygon points="28,3 50,20 43,52 15,52 6,20" fill="url(#sh)" stroke="#ECE8F2" stroke-opacity=".35"/>'
    '<polyline points="28,3 24,28 43,52" fill="none" stroke="#ECE8F2" stroke-opacity=".3"/>'
    '<polyline points="6,20 24,28 15,52" fill="none" stroke="#ECE8F2" stroke-opacity=".3"/>'
    "</svg>"
)


def masthead(chain_name, data_source, position_name):
    chips = "".join(f'<span class="ob-chip">{escape(str(c))}</span>' for c in
                    (position_name, chain_name, "Data: " + str(data_source),
                     "EPFL Hackathon 2026, Belem Capital Challenge"))
    _html(
        f'<div class="ob-mast">{SHARD}<div><h1>Obsidian</h1>'
        "<p>Trace what your position really depends on, then decide whether to stay.</p></div></div>"
        f'<div class="ob-chips">{chips}</div>'
    )


def section(title, note=None):
    sub = f"<p>{escape(note)}</p>" if note else ""
    _html(f'<div class="ob-section"><h2>{escape(title)}</h2>{sub}</div>')


def verdict(result, exit_cost, horizon):
    """Hero: the decision, and the two numbers behind it drawn to one scale."""
    meta = VERDICTS.get(result["decision"], VERDICTS["REVIEW"])
    color = meta["color"]
    stay = float(result["stay_loss_usd"])
    scale = max(stay, float(exit_cost or 0), 1.0)

    def bar(label, value, fill):
        if value is None:
            return (f'<div><div class="ob-bar-head"><span>{label}</span><b>Unavailable</b></div>'
                    '<div class="ob-track"></div></div>')
        width = max(value / scale * 100, 0.8)
        return (f'<div><div class="ob-bar-head"><span>{label}</span><b>{_usd(value)}</b></div>'
                f'<div class="ob-track"><div class="ob-fill" style="width:{width:.1f}%;background:{fill}"></div></div></div>')

    advantage = ""
    if result.get("net_benefit_usd") is not None:
        advantage = f'<p class="ob-adv">Modelled advantage: {_usd(abs(result["net_benefit_usd"]))}</p>'

    be = result.get("break_even_probability")
    if be is None:
        be_text = "No break-even probability for this graph."
    elif be > 1:
        be_text = f"Exit is never cheaper at this severity over {horizon} days."
    else:
        be_text = f"Break-even event probability over {horizon} days: {be:.2%}."

    _html(
        '<div class="ob-verdict" role="group" aria-label="Recommendation">'
        f'<div><p class="ob-word" style="color:{color}">{meta["word"]}</p>'
        f'<p class="ob-line">{meta["line"]}</p>{advantage}</div>'
        '<div class="ob-bars">'
        f'{bar(f"Expected loss if you stay ({horizon} days)", stay, "#8C7BFF")}'
        f'{bar("Cost to exit now", exit_cost, color)}'
        "</div>"
        f'<div class="ob-foot"><span>{be_text}</span>'
        f'<details><summary>Why this call</summary><p>{escape(result["reasoning"])}</p></details></div>'
        "</div>"
    )
