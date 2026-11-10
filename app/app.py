"""Contract Court: AI agents negotiate data contracts.

The data comes from snapshot.json, produced by measuring real consumers' SQL against changed copies
of the tables (on Databricks, or on a local stand-in when run offline). Nothing about tables,
consumers or impact is typed into this file. Decisions are deterministic rules; an AI model's
precomputed negotiations can be shown for some changes.
"""
import html
import json
import os
import re
import sys

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (HERE, os.path.dirname(HERE)):  # deployed: engine next to app; local: one level up
    if p not in sys.path:
        sys.path.insert(0, p)

from engine import (Change, Column, Contract, Producer, check_change, compat_view_sql,  # noqa: E402
                    day_to_date, negotiate, profiles, serialize)

st.set_page_config(page_title="Contract Court", page_icon="⚖️", layout="wide",
                   initial_sidebar_state="collapsed")

NAVY, CORAL, GREEN, RED, AMBER, GREY, PURPLE = (
    "#1C232B", "#E5484D", "#1D7A55", "#C82132", "#B25E00", "#98A2B3", "#6B2F8F")
TONES = {"propose": (NAVY, "Proposes"), "object": (RED, "Objects"), "accept": (GREEN, "Agrees"),
         "counter": (AMBER, "Counter-Offer"), "escalate": (PURPLE, "Escalates To A Human"), "info": (GREY, "")}
OUTCOME = {"error": ("BREAKS", RED), "silent": ("SILENTLY WRONG", AMBER), "ok": ("OK", GREEN)}
KIND_NAME = {"dashboard": "Dashboard", "ml_model": "Model Input", "report": "Report", "stream_job": "Monitor"}
VERB = {"rename_column": "Rename", "drop_column": "Drop", "change_type": "Change Type", "change_meaning": "Change Meaning"}

# editable columns of the Team Needs table
C_REP, C_KIND, C_NOT, C_OVL, C_FZA, C_FZB, C_VIEW, C_SNAP = (
    "Report", "Kind", "Notice (Days)", "Needs Old Shape For (Days)", "Freeze From (Day)", "Freeze To (Day)",
    "Can Use A View", "Can Use A Snapshot")

CSS = """
<style>
html, body, [class*="css"] { font-family: Inter, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
[data-testid="stToolbar"], [data-testid="stDecoration"], footer, #MainMenu { display: none !important; }
header[data-testid="stHeader"] { background: transparent; height: 2.2rem; }
.block-container { max-width: 1120px !important; margin: 0 auto; padding: 3rem 1.5rem 3rem; }
.stApp { background: #F4F5F7; }
.topbar { display:flex; align-items:center; justify-content:space-between; gap:20px; padding:28px 32px;
  background:#1C232B; border-radius:12px; margin-bottom:16px; }
.topbar .brand { font-size:32px; font-weight:800; letter-spacing:-.8px; color:#fff; }
.topbar .brand span { color:#FF5C6C; }
.topbar .sub { font-size:14px; color:#AAB4C0; margin-top:5px; }
.topbar .pills { text-align:right; max-width:46%; line-height:2.1; }
.pill { display:inline-block; font-size:11.5px; font-weight:600; padding:4px 11px; border-radius:20px;
  background:#2C3641; color:#D6DDE5; margin-left:6px; white-space:nowrap; }
.kpis { display:grid; grid-template-columns:repeat(5,1fr); gap:12px; margin-bottom:18px; }
.kpicard { background:#fff; border:1px solid #E3E6EA; border-radius:10px; padding:13px 16px; }
.kpicard .v { font-size:28px; font-weight:750; line-height:1.05; letter-spacing:-1px; color:#1C232B; }
.kpicard .l { font-size:11px; color:#667085; text-transform:uppercase; letter-spacing:.8px; margin-top:5px; font-weight:600; }
.card { background:#fff; border:1px solid #E3E6EA; border-radius:10px; padding:15px 18px; margin-bottom:12px; }
.card h4 { margin:0 0 8px; font-size:12px; text-transform:uppercase; letter-spacing:1px; color:#667085; font-weight:700; }
.kpi-label { font-size:11px; color:#667085; text-transform:uppercase; letter-spacing:.8px; font-weight:700; }
.kpi-value { font-size:28px; font-weight:750; line-height:1.1; letter-spacing:-1px; margin-top:4px; }
.kpi-sub { font-size:12px; color:#667085; margin-top:2px; }
.msg { font-size:15px; line-height:1.55; border-left:4px solid #C82132; }
.verdict { font-size:14.5px; line-height:1.55; }
.verdict.act { border-left:4px solid #1D7A55; } .verdict.esc { border-left:4px solid #6B2F8F; }
.verdict.none { border-left:4px solid #98A2B3; } .verdict.bad { border-left:4px solid #C82132; }
.note { background:#FAFBFC; font-size:13.5px; line-height:1.55; color:#475467; }
.flow { display:flex; align-items:center; gap:14px; margin:30px 0 14px; }
.flow .num { display:inline-flex; align-items:center; justify-content:center; width:32px; height:32px;
  border-radius:50%; background:#1C232B; color:#fff; font-weight:700; font-size:15px; flex:none; }
.flow b { font-size:18px; letter-spacing:-.2px; } .flow small { display:block; color:#667085; font-size:13px; margin-top:1px; }
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:10px; margin-bottom:14px; }
.tiles div { background:#fff; border:1px solid #E3E6EA; border-radius:8px; padding:10px 12px; }
.tiles b { display:block; font-size:22px; letter-spacing:-.5px; } .tiles span { font-size:11px; color:#667085; }
.bubble { background:#fff; border:1px solid #E3E6EA; border-left:5px solid var(--c); border-radius:8px;
  padding:10px 14px; margin-bottom:9px; }
.bubble .who { font-weight:700; font-size:.95rem; }
.bubble .tag { display:inline-block; font-size:.7rem; font-weight:700; color:#fff; background:var(--c);
  border-radius:6px; padding:2px 8px; margin-left:8px; }
.bubble .subh { font-size:.78rem; color:#667085; margin-top:2px; }
.bubble ul { margin:7px 0 0 18px; padding:0; font-size:.92rem; line-height:1.45; color:#2B3245; }
.bubble code, .card code { background:#F1F3F5; border-radius:5px; padding:1px 5px; font-size:.88em; }
.sys { text-align:center; font-size:.84rem; color:#667085; background:#ECEEF1; border-radius:8px;
  padding:7px 12px; margin-bottom:9px; }
.badge { display:inline-block; border-radius:6px; padding:3px 9px; font-size:.74rem; font-weight:700; color:#fff; }
.cmp { width:100%; border-collapse:collapse; font-size:.9rem; }
.cmp th { text-align:left; color:#667085; font-weight:600; padding:8px 10px; border-bottom:2px solid #E3E6EA; }
.cmp td { padding:9px 10px; border-bottom:1px solid #F1F3F5; vertical-align:top; }
.sect { font-size:1.1rem; font-weight:750; margin:22px 0 8px 0; letter-spacing:-.01em; }
.sect small { font-weight:500; color:#667085; margin-left:8px; font-size:.85rem; }
div[data-testid="stButton"] > button { border-radius:8px; border:1px solid #D0D5DD; background:#fff; padding:.5rem 1rem; font-weight:600; }
/* top-level tabs: bold, capitalised, well spaced */
[data-testid="stTab"], button[data-baseweb="tab"] { margin-right:34px; padding:10px 2px; }
[data-testid="stTab"] p, button[data-baseweb="tab"] p { font-weight:800 !important; text-transform:uppercase; letter-spacing:1px; font-size:.85rem; }
[role="tabpanel"] [data-testid="stTab"], [role="tabpanel"] button[data-baseweb="tab"] { margin-right:22px; padding:8px 2px; }
[role="tabpanel"] [data-testid="stTab"] p, [role="tabpanel"] button[data-baseweb="tab"] p
  { font-weight:700 !important; text-transform:none; letter-spacing:0; font-size:.95rem; }
/* one consistent light look, even if the browser prefers dark mode */
.stApp [data-testid="stMarkdownContainer"] *, .stApp [data-testid="stWidgetLabel"] *,
.stApp [data-testid="stTab"] *, .stApp [data-testid="stRadio"] *, .stApp [data-testid="stExpander"] *,
.stApp [data-testid="stButton"] button * { color: #1C232B; }
.stApp [data-testid="stCaptionContainer"] * { color: #667085; }
.stApp .kpicard .l, .stApp .kpi-label, .stApp .kpi-sub, .stApp .sys, .stApp .sect small, .stApp .flow small,
.stApp .tiles span, .stApp .card h4, .stApp .topbar .sub, .stApp .bubble .subh, .stApp .note { color: #667085; }
.stApp .note { color: #475467; }
.stApp .topbar .brand, .stApp .pill { color: #fff; } .stApp .topbar .brand span { color: #FF5C6C; }
.stApp .topbar .sub { color: #AAB4C0; } .stApp .pill { color: #D6DDE5; }
.stApp .badge, .stApp .bubble .tag, .stApp .flow .num { color: #fff; }
.stApp [data-testid="stTab"][aria-selected="true"] * { color: #C82132; }
.stApp code { background: #F1F3F5; color: #1C232B; }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ------------------------------------------------------------------- helpers
@st.cache_data
def load_snapshot():
    path = os.path.join(HERE, "data", "snapshot.json")
    return json.load(open(path)) if os.path.exists(path) else None


def md_code(text):
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", html.escape(text))


def kpi(label, value, sub, color):
    return (f'<div class="card"><div class="kpi-label">{label}</div>'
            f'<div class="kpi-value" style="color:{color}">{value}</div>'
            f'<div class="kpi-sub">{sub}</div></div>')


def badge(text, color):
    return f'<span class="badge" style="background:{color}">{text}</span>'


def html_table(df):
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in df.columns)
    body = "".join("<tr>" + "".join(f"<td>{md_code(str(v))}</td>" for v in row) + "</tr>"
                   for row in df.itertuples(index=False))
    return f'<div class="card" style="padding:8px 12px"><table class="cmp"><tr>{head}</tr>{body}</table></div>'


def section(title, hint=""):
    st.markdown(f'<div class="sect">{title}<small>{hint}</small></div>', unsafe_allow_html=True)


def render_html(markup, height):
    """Embed an HTML/SVG snippet. Newer Streamlit has st.iframe; older versions use components.html."""
    if hasattr(st, "iframe"):
        st.iframe(markup, height=height)
    else:
        import streamlit.components.v1 as components
        components.html(markup, height=height, scrolling=False)


def flow_head(n, title, sub):
    st.markdown(f'<div class="flow"><span class="num">{n}</span><div><b>{title}</b><small>{sub}</small></div></div>',
                unsafe_allow_html=True)


def tiles_html(items):
    return '<div class="tiles">' + "".join(f"<div><b>{v}</b><span>{l}</span></div>" for v, l in items) + "</div>"


LABEL = {"agg": "Summary: ", "trend": "Trend: ", "features": "Features: ", "join": "Join: ",
         "top": "Top 10 of ", "latest": "Latest: "}


def nice(name):
    """top_agg_booking_updates_by_status -> Top 10 of booking updates by status"""
    first = name.split("_")[0] if name.split("_")[0] in LABEL else ""
    while re.match(r"^(agg|trend|features|join|top|latest)_", name):
        name = re.sub(r"^(agg|trend|features|join|top|latest)_", "", name, count=1)
    name = name.replace("_by_", " by ").replace("_", " ")
    return LABEL.get(first, "") + (name if first == "top" else name[:1].lower() + name[1:] if first else name)


def escalation_text(res):
    """'6 team(s) report: I have no replacement for `x`; dropping it breaks me permanently.' (one line, no repeats)"""
    reasons = [(i.partition(": ")[2] or i).rstrip(".") for i in res.open_issues]
    unique = list(dict.fromkeys(reasons))
    return f"{len(reasons)} team(s) report: " + "; ".join(unique[:2]) + "."


def issue_text_of(issue):
    who, sep, rest = issue.partition(": ")
    return (nice(who) + sep + rest if sep else who).rstrip(".")


# ------------------------------------------------- the dependency diagram (left to right)
def _shape(kind, cx, cy, color):
    if kind == "dashboard":
        return f'<rect x="{cx - 5}" y="{cy - 5}" width="10" height="10" fill="{color}"/>'
    if kind == "ml_model":
        return f'<polygon points="{cx},{cy - 6} {cx + 6},{cy} {cx},{cy + 6} {cx - 6},{cy}" fill="{color}"/>'
    if kind == "stream_job":
        return f'<polygon points="{cx},{cy - 6} {cx + 6},{cy + 5} {cx - 6},{cy + 5}" fill="{color}"/>'
    return f'<circle cx="{cx}" cy="{cy}" r="5.5" fill="{color}"/>'


def lineage_html(hits, n_others, by_name, table, column, verb, state, agreed):
    """Layered, left-to-right diagram: the changed column, the reports that read it, the reports built on those."""
    names = [r["consumer"] for r in hits]
    reads = {n: [p for p in by_name[n]["reads"] if p != n] for n in names}
    layer = {}

    def depth(n, seen=()):
        if n not in layer:
            parents = [p for p in reads[n] if p in names and p not in seen]
            layer[n] = 1 + max((depth(p, seen + (n,)) for p in parents), default=0)
        return layer[n]

    for n in names:
        depth(n)
    top = max(layer.values())
    cols = {i: sorted(n for n in names if layer[n] == i) for i in range(1, top + 1)}
    idx = {n: i for i, n in enumerate(cols[1])}
    for l in range(2, top + 1):  # order each layer by its parents, so connectors cross as little as possible
        cols[l].sort(key=lambda n: (sum(idx[p] for p in reads[n] if p in idx) / max(1, sum(p in idx for p in reads[n])), n))
        idx.update({n: i for i, n in enumerate(cols[l])})

    CW, CH, GAP, SW, SH, XG, PAD, FOOT = 310, 78, 14, 230, 96, 80, 14, 46
    rows = max(len(v) for v in cols.values())
    body_h = rows * (CH + GAP) - GAP
    W = PAD * 2 + SW + top * (CW + XG)
    H = PAD + body_h + FOOT
    x_of = lambda l: PAD + SW + XG + (l - 1) * (CW + XG)  # noqa: E731
    y_of = {}
    for l, members in cols.items():
        y0 = PAD + (body_h - (len(members) * (CH + GAP) - GAP)) / 2
        for i, n in enumerate(members):
            y_of[n] = y0 + i * (CH + GAP)
    sy = PAD + body_h / 2

    def style(n):
        o = next(r["outcome"] for r in hits if r["consumer"] == n)
        if state == "lands":
            return (RED, "Crashes") if o == "error" else (AMBER, "Wrong numbers")
        return (GREEN, "Fixed by the contract") if agreed else (PURPLE, "Blocked")

    edges, cards = [], []
    for n in names:
        color = style(n)[0]
        sources = [(x_of(layer[p]) + CW, y_of[p] + CH / 2) for p in reads[n] if p in names]
        if table in reads[n] or not sources:
            sources.append((PAD + SW, sy))
        for x1, y1 in sources:
            x2, y2 = x_of(layer[n]), y_of[n] + CH / 2
            mid = (x2 - x1) / 2
            edges.append(f'<path d="M{x1},{y1} C{x1 + mid},{y1} {x2 - mid},{y2} {x2},{y2}" fill="none" '
                         f'stroke="{color}" stroke-width="2" opacity=".75"/>')
    for n in names:
        color, status = style(n)
        x, y = x_of(layer[n]), y_of[n]
        c = by_name[n]
        title = nice(n)
        title = title if len(title) <= 38 else title[:37] + "…"
        sub = f"{KIND_NAME.get(c['kind'], c['kind'])}  ·  Table: {', '.join(c['base_tables'])}"
        sub = sub if len(sub) <= 56 else sub[:55] + "…"
        cards.append(
            f'<g><rect x="{x}" y="{y}" width="{CW}" height="{CH}" rx="8" fill="#fff" stroke="#E3E6EA"/>'
            f'<rect x="{x}" y="{y}" width="6" height="{CH}" rx="3" fill="{color}"/>'
            f'<text x="{x + 20}" y="{y + 25}" font-size="13.5" font-weight="700" fill="#1C232B">{html.escape(title)}</text>'
            f'{_shape(c["kind"], x + 24, y + 44, "#98A2B3")}'
            f'<text x="{x + 38}" y="{y + 48}" font-size="11.5" fill="#667085">{html.escape(sub)}</text>'
            f'<text x="{x + 20}" y="{y + 67}" font-size="12" font-weight="700" fill="{color}">● {html.escape(status)}</text></g>')
    src = (f'<g><rect x="{PAD}" y="{sy - SH / 2}" width="{SW}" height="{SH}" rx="10" fill="#1C232B"/>'
           f'<text x="{PAD + 18}" y="{sy - 20}" font-size="10.5" font-weight="700" fill="#AAB4C0" letter-spacing="1">TABLE · {html.escape(table.upper())}</text>'
           f'<text x="{PAD + 18}" y="{sy + 6}" font-size="19" font-weight="800" fill="#fff">{html.escape(column)}</text>'
           f'<rect x="{PAD + 18}" y="{sy + 17}" width="{12 + 7.2 * len(verb)}" height="22" rx="11" fill="#2C3641"/>'
           f'<text x="{PAD + 30}" y="{sy + 32}" font-size="11.5" font-weight="600" fill="#D6DDE5">{html.escape(verb)}</text></g>')
    foot = (f'<text x="{PAD}" y="{H - 16}" font-size="12.5" fill="#667085">{n_others} other reports were tested and are not affected.</text>')
    legend, lx = [], W - PAD
    for k in sorted({by_name[n]["kind"] for n in names}, reverse=True):
        label = KIND_NAME.get(k, k)
        lx -= 22 + 7 * len(label)
        legend.append(f'{_shape(k, lx + 6, H - 21, "#98A2B3")}<text x="{lx + 18}" y="{H - 17}" font-size="12" fill="#667085">{label}</text>')
        lx -= 14
    svg = ('<style>html,body{margin:0;padding:0;overflow:hidden;background:transparent}svg{display:block}</style>'
           f'<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg" '
           f'font-family="Inter, -apple-system, Segoe UI, Roboto, Arial, sans-serif">'
           + "".join(edges) + src + "".join(cards) + foot + "".join(legend) + "</svg>")
    return svg, H


# ------------------------------------------------------------------- negotiation, grouped by round
def message_card(m, by_name, table, res=None):
    if m.role == "system":
        return f'<div class="sys">{md_code(m.text)}</div>'
    color, tag = TONES.get(m.kind, (GREY, ""))
    if m.role == "consumer":
        c = by_name.get(m.speaker, {})
        head = html.escape(nice(m.speaker))
        sub = f"Table: {', '.join(c.get('base_tables', []))}  ·  {KIND_NAME.get(c.get('kind'), '')}"
    else:
        head, sub = "Table Owners", f"Producer  ·  Table: {table}"
    tag_html = f'<span class="tag">{tag}</span>' if tag else ""
    text = m.text
    if m.role == "producer" and m.kind == "escalate" and res is not None and res.open_issues:
        text = "I cannot resolve this with the agents alone. " + escalation_text(res)
    return (f'<div class="bubble" style="--c:{color}"><span class="who">{head}</span>{tag_html}'
            f'<div class="subh">{html.escape(sub)}</div><ul><li>{md_code(text)}</li></ul></div>')


def negotiation_view(res, by_name, table):
    st.markdown("".join(message_card(m, by_name, table, res) for m in res.messages if m.round == 0), unsafe_allow_html=True)
    for rd in res.rounds:
        r, resp = rd["round"], rd["responses"]
        n_obj = sum(1 for x in resp if not x.accept)
        reply = next((m for m in res.messages if m.round == r and m.role == "producer"), None)
        title = f"Round {r}:  " + (f"{n_obj} of {len(resp)} teams objected" if n_obj else "All teams agreed")
        if reply is not None:
            title += "  ·  " + ("The owners made a counter-offer" if reply.kind == "counter" else "Escalated to a human")
        with st.expander(title, expanded=(r == 1)):
            st.markdown("".join(message_card(m, by_name, table, res) for m in res.messages if m.round == r),
                        unsafe_allow_html=True)


# ------------------------------------------------------------------- header
snap = load_snapshot()
if snap is None:
    st.error("No snapshot found. Run the Databricks notebooks (or copy tools/standin_snapshot.json to app/data/snapshot.json).")
    st.stop()

source_label = ("Measured on Databricks" if snap["source"] == "databricks"
                else "Local Stand-In Data (Not Measured on Databricks)")
drop_hits = {}
for r in snap["impact"]:
    if r["kind"] == "drop" and r["mode"] == "no_contract" and r["outcome"] != "ok":
        drop_hits.setdefault((r["table"], r["column"]), set()).add(r["consumer"])
n_ai = len(snap.get("llm_negotiations", {}))

st.markdown(f"""
<div class="topbar">
  <div><div class="brand">Contract <span>Court</span></div>
  <div class="sub">Change a data column, and the teams that depend on it negotiate before anything breaks.</div></div>
  <div class="pills"><span class="pill">{html.escape(source_label)}</span>
  <span class="pill">{len(snap['tables'])} Tables · {len(snap['consumers'])} Reports</span>
  <span class="pill">AI Model: {html.escape(snap.get('llm_endpoint', 'None')) if n_ai else 'None'}</span></div>
</div>
<div class="kpis">
  <div class="kpicard"><div class="v">{len(snap['tables'])}</div><div class="l">Tables Introspected</div></div>
  <div class="kpicard"><div class="v">{len(snap['consumers'])}</div><div class="l">Downstream Reports</div></div>
  <div class="kpicard"><div class="v">{len(snap['impact']):,}</div><div class="l">Impact Tests Run</div></div>
  <div class="kpicard"><div class="v">{len(snap['unread'])}</div><div class="l">Columns Nobody Reads</div></div>
  <div class="kpicard"><div class="v">{n_ai}</div><div class="l">Changes Negotiated By AI</div></div>
</div>
""", unsafe_allow_html=True)

tab_live, tab_proof, tab_how = st.tabs(["Negotiate a Change", "What Was Measured", "How It Works"])


# ==================================================================== NEGOTIATE
def live():
    impact = snap["impact"]
    s1, s2, s3 = st.container(), st.container(), st.container()

    # ---------------------------------------------------------------- 1. what you have
    with s1:
        flow_head(1, "What You Have", "Pick a table, a column and the change its owners want to make.")
        c1, c2, c3 = st.columns(3)
        best = max(drop_hits, key=lambda k: len(drop_hits[k]), default=None)
        tnames = list(snap["tables"])
        table = c1.selectbox("Table", tnames, index=tnames.index(best[0]) if best else 0)
        cols = snap["tables"][table]
        cnames = [n for n, _ in cols]
        best_col = max(cnames, key=lambda n: len(drop_hits.get((table, n), ())))
        labels = [f"{n}  ({len(drop_hits.get((table, n), ()))} reports read it)" for n in cnames]
        column = cnames[labels.index(c2.selectbox("Column", labels, index=cnames.index(best_col)))]
        ctype = dict(cols)[column]
        retype_targets = sorted({r["param"] for r in impact if (r["table"], r["column"], r["kind"]) == (table, column, "retype")})
        has_meaning = any((r["table"], r["column"], r["kind"]) == (table, column, "meaning") for r in impact)
        kinds = {"Rename the column": "rename_column", "Drop the column": "drop_column"}
        if retype_targets:
            kinds["Change its data type"] = "change_type"
        if has_meaning:
            kinds["Change its meaning (units)"] = "change_meaning"
        kind = kinds[c3.selectbox("Kind of Change", list(kinds))]

        # the second row always has the same three boxes, whatever the kind of change
        p1, p2, p3 = st.columns(3)
        new_name = new_dtype = factor = None
        if kind == "rename_column":
            new_name = p1.text_input("New Name", f"{column}_v2")
        elif kind == "change_type":
            new_dtype = p1.selectbox("New Type", retype_targets)
        elif kind == "change_meaning":
            factor = p1.number_input("Values Are Now Multiplied By", value=100.0, step=1.0)
        else:
            p1.text_input("Change Details", "Nothing more is needed to drop a column.", disabled=True)
        eff_day = int(p2.number_input("Wants to Ship in (Days)", min_value=0, max_value=365, value=3))
        readers = len(drop_hits.get((table, column), ()))
        p3.text_input("Reports That Read This Column", f"{readers} of {len(snap['consumers'])}", disabled=True)

        valid = not (kind == "rename_column" and (not new_name or new_name in dict(cols))) and \
            not (kind == "change_meaning" and (factor in (None, 0, 1)))
        if not valid:
            st.warning("Please give a new name that is not already used, or a factor other than 0 or 1.")
            return

        change = Change(kind, column, eff_day, new_name=new_name, new_dtype=new_dtype, factor=factor)
        hits = profiles.affected(snap, table, change)
        by_name = {c["name"]: c for c in snap["consumers"]}
        affected_names = [r["consumer"] for r in hits]
        evidence = {r["consumer"]: r for r in hits}
        others = [c["name"] for c in snap["consumers"] if c["name"] not in affected_names]
        st.markdown(
            f'<div class="card msg"><h4>The Proposal</h4>The owners of <code>{table}</code> want to '
            f'{md_code(change.describe())}, shipping in {eff_day} day(s). '
            f'<b>{readers}</b> of {len(snap["consumers"])} downstream reports read this column.</div>',
            unsafe_allow_html=True)

    # ---------------------------------------------------------------- 2. what will happen
    n_break = sum(1 for r in hits if r["outcome"] == "error")
    n_silent = sum(1 for r in hits if r["outcome"] == "silent")
    with s2:
        flow_head(2, "What Will Happen", "If the change ships today with no contract.")
        if not hits:
            st.markdown(f'<div class="card verdict none"><h4>Result · Nothing Breaks</h4>We re-ran all '
                        f'{len([c for c in snap["consumers"] if table in c["base_tables"]])} reports that depend on '
                        f'<code>{table}</code> with this change applied. None of them broke or changed.</div>',
                        unsafe_allow_html=True)
            st.markdown(tiles_html([(0, "Reports Affected"), (0, "Crash"), (0, "Silently Wrong"),
                                    (len(snap["consumers"]), "Tested and Fine")]), unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="card verdict bad"><h4>Result · It Breaks Things</h4><b>{len(hits)}</b> of '
                        f'{len(snap["consumers"])} reports would be hit: <b>{n_break}</b> crash and <b>{n_silent}</b> '
                        'silently give wrong numbers.</div>', unsafe_allow_html=True)
            st.markdown(tiles_html([(len(hits), "Reports Affected"), (n_break, "Crash"), (n_silent, "Silently Wrong"),
                                    (len(snap["consumers"]) - len(hits), "Tested and Fine")]), unsafe_allow_html=True)
            t_map, t_res = st.tabs(["Impact Map", "Per-Team Results"])

    # ---------------------------------------------------------------- 3. the answer
    with s3:
        flow_head(3, "What We Have as the Answer", "The negotiation, the signed contract and how it is enforced.")
        if not hits:
            st.markdown('<div class="card verdict act"><h4>Result · Approved</h4>No report is affected by this change, '
                        'so there is nothing to negotiate. There is no change in impact, and the change can move ahead.</div>',
                        unsafe_allow_html=True)
            st.markdown(tiles_html([(0, "Rounds"), (0, "Teams Negotiating"), (day_to_date(eff_day), "Can Go Live"),
                                    ("None", "Old Shape Needed")]), unsafe_allow_html=True)
            return
        mode_c, decision_c, tiles_c = st.container(), st.container(), st.container()
        t_conv, t_deal, t_needs, t_sql = st.tabs(["Negotiation", "Signed Contract", "Team Needs", "SQL"])

    # ---- Team Needs: defaults come from measurable facts, all editable
    with t_needs:
        st.caption("What each affected team requires: how much notice it needs and how long it needs the old column. "
                   "Defaults come from how often each report refreshes and how many reports are built on it. "
                   "Edit any cell and the deal updates.")
        a1, a2, a3 = st.columns(3)
        max_overlap = int(a1.number_input("Producer Keeps the Old Column At Most (Days)", 0, 365, 30))
        max_view = int(a2.number_input("Producer Keeps a Compatibility View At Most (Days)", 0, 365, 90))
        deadline = int(a3.number_input("Release Deadline (Days From Today)", 1, 730, 60))
        base_rows = []
        for name in affected_names:
            d = profiles.defaults(by_name[name])
            base_rows.append({C_REP: name, C_KIND: by_name[name]["kind"], C_NOT: d["notice_days"],
                              C_OVL: d["min_overlap_days"], C_FZA: None, C_FZB: None,
                              C_VIEW: d["can_use_view"], C_SNAP: d["can_use_snapshot"]})
        key = f"ed_{table}_{column}_{kind}_{new_dtype}"
        edited = st.data_editor(pd.DataFrame(base_rows), key=key, hide_index=True, disabled=[C_REP, C_KIND])

    no_repl = (frozenset([column]) if kind == "drop_column" and not profiles.has_replacement(cols, column)
               else frozenset())
    agents = []
    for _, row in edited.iterrows():
        fz = None
        if pd.notna(row[C_FZA]) and pd.notna(row[C_FZB]):
            fz = (int(row[C_FZA]), int(row[C_FZB]))
        agents.append(profiles.build_consumer(
            by_name[row[C_REP]], {"notice_days": row[C_NOT], "min_overlap_days": row[C_OVL], "freeze": fz,
                                  "can_use_view": row[C_VIEW], "can_use_snapshot": row[C_SNAP]}, no_repl))
    contract = Contract(table, 1, {n: Column(n, t) for n, t in cols})
    res = negotiate(contract, change, agents, others, Producer(f"{table} owners", max_overlap, max_view, deadline))

    # ---- who negotiates: the AI model by default, when its transcript exists for this exact change
    pre = snap.get("llm_negotiations", {})
    sig = f"{table}|{column}|{profiles.KIND_OF[kind]}|{new_dtype or ''}"
    untouched = (
        eff_day == 3 and (max_overlap, max_view, deadline) == (30, 90, 60)
        and (kind != "rename_column" or new_name == f"{column}_v2")
        and (kind != "change_meaning" or factor == 100)
        and all(pd.isna(r[C_FZA]) and r[C_NOT] == b[C_NOT] and r[C_OVL] == b[C_OVL]
                and r[C_VIEW] == b[C_VIEW] and r[C_SNAP] == b[C_SNAP]
                for (_, r), b in zip(edited.iterrows(), base_rows)))
    with mode_c:
        if sig in pre and untouched:
            model = snap.get("llm_endpoint", "Databricks Model")
            mode = st.radio("Who Negotiates?", [f"AI Model ({model})", "Fixed Rules"], horizontal=True)
            if mode.startswith("AI Model"):
                res = serialize.result_from_dict(pre[sig]["result"])
            st.markdown(
                '<div class="card note"><b>Why two options?</b> <b>Fixed Rules</b> always follow the same order of '
                'concessions: a later date, a longer overlap, a compatibility view, then a snapshot. The <b>AI Model</b> '
                "writes each team's lines and chooses the owners' offers in its own way, so the path can differ. "
                'The fixed rules still check every deal, so both end in a valid result.</div>', unsafe_allow_html=True)
        elif sig in pre:
            st.caption("The AI model negotiated this change with the default settings. You changed a setting, so the fixed rules "
                       "negotiated this one. Reset your edits to see the AI version.")
        elif pre:
            st.caption(f"The AI model negotiated {len(pre)} of the highest-impact changes in advance. "
                       "This change uses the fixed rules.")

    wc = profiles.with_contract_rows(snap, table, change)
    agreed, a = res.status == "agreed", res.agreement

    with decision_c:
        if agreed:
            sentence = (f"After <b>{len(res.rounds)} round(s)</b>, all <b>{len(res.affected)}</b> affected teams signed. "
                        f"The change ships on <b>{day_to_date(a.effective_day)}</b>"
                        + (f"; the old column stays for <b>{a.overlap_days} days</b>" if a.overlap_days else "")
                        + (f"; a compatibility view keeps the old shape for <b>{a.view_days} days</b>" if a.compat_view else "")
                        + ("; a frozen snapshot covers teams that need longer" if a.snapshot else "")
                        + ". With the contract, none of the reports break.")
            st.markdown(f'<div class="card verdict act"><h4>Result · Agreed</h4>{sentence}</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="card verdict esc"><h4>Result · Needs a Human</h4>The agents could not agree on '
                        f'their own. {md_code(escalation_text(res) if res.open_issues else "No agreement.")} '
                        'The change stays blocked until a person decides.</div>', unsafe_allow_html=True)
    with tiles_c:
        tiles = [(len(res.rounds), "Rounds"), (len(res.affected), "Teams Negotiating")]
        if agreed:
            tiles += [(day_to_date(a.effective_day), "Goes Live"), (f"{a.overlap_days or a.view_days} Days", "Old Shape Kept"),
                      (a.new_contract.version, "Contract Version")]
        else:
            tiles += [(len(res.open_issues), "Blockers")]
        st.markdown(tiles_html(tiles), unsafe_allow_html=True)

    # ---- the tabs of part 2
    with t_map:
        st.caption("Read it from left to right: the column being changed, the reports that read it directly, "
                   "and the reports that are built on top of those.")
        view = st.radio("View", ["Without a Contract", "After the Negotiation"], horizontal=True, label_visibility="collapsed")
        if view.startswith("Without"):
            st.caption("Without a contract, the owners just ship the change. Red reports crash; amber reports keep running "
                       "but silently give wrong numbers.")
        elif agreed:
            st.caption("After the negotiation, the signed contract keeps the old column available, so every report "
                       "from the first view keeps working while its team migrates.")
        else:
            st.caption("After the negotiation, the agents could not agree, so the change is blocked until a person decides. "
                       "Nothing ships and nothing breaks.")
        svg, height = lineage_html(hits, len(others), by_name, table, column, VERB[kind],
                                   "lands" if view.startswith("Without") else "deal", agreed)
        render_html(svg, int(height * 1.06) + 10)
    with t_res:
        st.caption("Measured by running each report's own SQL on a sandbox copy of the table, with and without the signed contract.")
        rows = ""
        for name in affected_names:
            e = evidence[name]
            lab, col = OUTCOME[e["outcome"]]
            left_cell = badge(lab, col) + f'<div style="color:#667085;font-size:.8rem;margin-top:3px">{html.escape(e["detail"][:90])}</div>'
            if not agreed:
                right_cell = "-"
            elif name in wc:
                lab2, col2 = OUTCOME[wc[name]["outcome"]]
                right_cell = badge(lab2, col2)
            else:
                right_cell = badge("OLD COLUMN KEPT", GREEN)
            c = by_name[name]
            rows += (f"<tr><td><b>{html.escape(nice(name))}</b><div style='color:#667085;font-size:.78rem'>"
                     f"Table: {html.escape(', '.join(c['base_tables']))}  ·  {KIND_NAME.get(c['kind'], '')}</div></td>"
                     f"<td>{left_cell}</td><td>{right_cell}</td></tr>")
        st.markdown('<div class="card"><table class="cmp"><tr><th>Report</th><th>Without a Contract</th>'
                    f'<th>With the Signed Contract</th></tr>{rows}</table></div>', unsafe_allow_html=True)

    # ---- the tabs of part 3
    with t_conv:
        st.caption("The conversation, one round at a time. Each message shows the report, its table and what the team asks for.")
        negotiation_view(res, by_name, table)
    with t_deal:
        st.caption("The agreed terms, and a test of what happens when someone ignores them.")
        if agreed:
            old = a.new_contract.columns[column]
            facts = [("Goes Live", day_to_date(a.effective_day)),
                     ("Old Shape Kept Until", day_to_date(old.deprecated_until) if old.deprecated_until is not None
                      else day_to_date(a.effective_day + a.view_days) if a.compat_view else "-"),
                     ("Compatibility View", "Yes" if a.compat_view else "No"),
                     ("Frozen Snapshot", "Yes" if a.snapshot else "No"), ("Signed By", f"{len(a.signed_by)} Teams")]
            d1, d2 = st.columns([3, 2])
            d1.markdown('<div class="card">' + "".join(
                f'<div style="display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid #F1F3F5">'
                f'<span style="color:#667085">{k}</span><b>{v}</b></div>' for k, v in facts) + "</div>", unsafe_allow_html=True)
            rogue_key = f"rogue_{table}_{column}_{kind}"
            with d2:
                st.markdown('<div class="card"><h4>Try to Break the Contract</h4>Pretend someone ships the breaking '
                            'change early.</div>', unsafe_allow_html=True)
                if st.button("Simulate a Rogue Push", key=f"rogue_btn_{rogue_key}"):
                    st.session_state[rogue_key] = check_change(a.new_contract, Change("drop_column", column, 5), today=5)
                rogue = st.session_state.get(rogue_key)
                if rogue:
                    color = GREEN if rogue[0] else RED
                    st.markdown(f'<div class="card" style="border-color:{color}">{badge("ALLOWED" if rogue[0] else "BLOCKED", color)}'
                                f' &nbsp; {md_code(" ".join(rogue[1]))}</div>', unsafe_allow_html=True)
        else:
            for issue in res.open_issues:
                st.markdown(f'<div class="card verdict esc"><h4>A Person Must Decide</h4>{md_code(issue_text_of(issue))}</div>',
                            unsafe_allow_html=True)
    with t_sql:
        st.caption("The SQL for the compatibility view that keeps the old shape alive while teams migrate.")
        if kind != "drop_column":
            st.code(compat_view_sql(table, change, ctype), language="sql")
        else:
            st.info("Dropping a column needs no compatibility view.")


with tab_live:
    live()


# ==================================================================== WHAT WAS MEASURED
with tab_proof:
    st.markdown("#### What Was Measured")
    st.markdown(
        f"**{source_label}.** Every column of every table was dropped, renamed, retyped and rescaled in a sandbox copy. "
        "After each change, every downstream report was re-run with its own SQL and compared with its baseline. "
        "Nothing here was typed in by hand.")
    n_read = sum(len(c) for c in snap["tables"].values()) - len(snap["unread"])
    m1, m2, m3, m4 = st.columns(4)
    m1.markdown(kpi("Tables", f"{len(snap['tables'])}", "Introspected, not coded", NAVY), unsafe_allow_html=True)
    m2.markdown(kpi("Reports", f"{len(snap['consumers'])}", f"{sum(1 for c in snap['consumers'] if c['depth'] > 0)} feed other reports", NAVY), unsafe_allow_html=True)
    m3.markdown(kpi("Impact Tests", f"{len(snap['impact']):,}", "Each is a real query comparison", NAVY), unsafe_allow_html=True)
    m4.markdown(kpi("Unread Columns", f"{len(snap['unread'])}", f"Of {n_read + len(snap['unread'])} columns", NAVY), unsafe_allow_html=True)

    section("Blast-Radius Leaderboard", "How many reports break if this column is dropped.")
    top = sorted(((len(v), f"{t}.{c}") for (t, c), v in drop_hits.items()), reverse=True)[:15]
    fig = go.Figure(go.Bar(x=[n for n, _ in top][::-1], y=[c for _, c in top][::-1], orientation="h",
                           marker=dict(color=CORAL)))
    fig.update_layout(height=60 + 28 * len(top), margin=dict(l=0, r=0, t=0, b=0), plot_bgcolor="rgba(0,0,0,0)",
                      paper_bgcolor="rgba(0,0,0,0)", font=dict(color=NAVY, size=12), xaxis=dict(gridcolor="#E3E6EA"))
    st.plotly_chart(fig, config={"displayModeBar": False})

    section("The Reports", "Generated from the schema; their needs are derived, not invented.")
    reg = pd.DataFrame([{"Report": nice(c["name"]), "Kind": KIND_NAME.get(c["kind"], c["kind"]),
                         "Reads Table": ", ".join(c["base_tables"]), "Refresh (Hours)": c["cadence_hours"],
                         "Reports Built On It": c["depth"], "Default Notice (Days)": profiles.defaults(c)["notice_days"],
                         "Default Overlap (Days)": profiles.defaults(c)["min_overlap_days"]}
                        for c in snap["consumers"]])
    st.markdown(html_table(reg), unsafe_allow_html=True)

    if snap.get("uc_lineage"):
        measured = {(t, c["name"]) for c in snap["consumers"] for t in c["base_tables"]}
        uc = {tuple(x) for x in snap["uc_lineage"]}
        agree = len(measured & uc) / max(len(measured), 1)
        section("Cross-Check with Unity Catalog Lineage")
        st.markdown(f'<div class="card">{badge(f"{agree:.0%} of measured table dependencies also appear in Unity Catalog lineage", GREEN if agree > .8 else AMBER)}</div>',
                    unsafe_allow_html=True)

# ==================================================================== HOW IT WORKS
with tab_how:
    h1, h2, h3 = st.columns(3)
    h1.markdown('<div class="card"><b>1. Measure</b><br><br>For every column, we apply each kind of change to a sandbox '
                'copy of the table and re-run every downstream report. A report is <i>affected</i> if it errors '
                'or silently returns different numbers.</div>', unsafe_allow_html=True)
    h2.markdown('<div class="card"><b>2. Negotiate</b><br><br>Each affected report becomes an agent with its own '
                'needs: notice, overlap and freeze windows. The producer concedes in order: a later date, a longer '
                'overlap, a compatibility view, a snapshot. With no workaround, the agents escalate to a human.</div>',
                unsafe_allow_html=True)
    h3.markdown('<div class="card"><b>3. Enforce</b><br><br>The deal becomes contract v2. Every migration must pass '
                'a contract check first, so one that ignores the deal is blocked.</div>', unsafe_allow_html=True)
    section("What Is Measured and What Is Assumed")
    st.markdown(
        "- **Measured:** Which reports break, whether they crash or silently return wrong numbers, and that the "
        "agreed plan (old shape kept next to the new one) really keeps them working.\n"
        "- **Derived:** Each team's default needs, from its refresh rate and how many reports are built on it "
        "(notice is two refresh cycles, overlap is three, plus a week per layer of reports above it).\n"
        "- **Your Input:** Any team's needs can be edited in the Team Needs tab, and so can the producer's limits. A real "
        "team's needs cannot be observed from data, so they are explicit inputs.\n"
        "- **Generated:** The reports themselves. Real organisations already have them; this project builds a "
        "workload from the schema so nothing is hand-picked.\n"
        "- **AI Model (Precomputed on Databricks):** A Databricks model writes each team's lines and proposes the "
        "producer's counter-offers. The rule engine referees: it decides whether a deal is acceptable, and any invalid "
        "model output falls back to the rule-based offer.\n"
        "- **Not Measured:** Change kinds beyond rename, drop, retype and rescale; real freeze windows; real usage logs.\n"
        "- Not endorsed by Databricks.")
