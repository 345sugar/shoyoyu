"""A quiet editorial identity with a bundled, original evening illustration."""

from base64 import b64encode
from functools import lru_cache
from html import escape
from pathlib import Path

import streamlit as st

APP_NAME = "Mouse Park Magic"
TAGLINE = "Less waiting. More magic."


@lru_cache(maxsize=1)
def _hero_image() -> str:
    image = Path(__file__).with_name("assets") / "evening-harbor.jpg"
    return "data:image/jpeg;base64," + b64encode(image.read_bytes()).decode("ascii")


def apply_theme() -> None:
    st.markdown("""
    <style>
    :root {--mpm-ink:#202e36; --mpm-muted:#656963; --mpm-paper:#faf8f3;
      --mpm-line:#d8d0c3; --mpm-gold:#9a7944; --mpm-serif:Georgia,"Yu Mincho","Hiragino Mincho ProN","Noto Serif JP",serif;}
    .stApp {background:var(--mpm-paper); color:var(--mpm-ink);}
    .block-container {max-width:1100px; padding:3.8rem 3rem 4rem;}
    [data-testid="stHeader"] {background:rgba(250,248,243,.96);}
    [data-testid="stSidebar"] {background:#f0ece3;}
    [data-testid="stMarkdownContainer"] h2,
    [data-testid="stMarkdownContainer"] h3,
    [data-testid="stMarkdownContainer"] h4 {font-family:var(--mpm-serif); font-weight:500; letter-spacing:.04em;}
    [data-testid="stCaptionContainer"] {color:var(--mpm-muted); line-height:1.8;}
    .mpm-masthead {text-align:center; border-top:1px solid var(--mpm-ink);
      padding:1.5rem 0 1.25rem; margin-bottom:1.6rem; border-bottom:1px solid var(--mpm-line);}
    .mpm-edition {font-size:.61rem; letter-spacing:.25em; color:#73634c; margin:0 0 .65rem;}
    .mpm-masthead h1 {font-family:var(--mpm-serif); color:var(--mpm-ink); font-size:clamp(2rem,4.8vw,3.3rem);
      font-weight:400; line-height:1.12; letter-spacing:-.04em; padding:0; margin:0;}
    .mpm-masthead .mpm-motto {font-family:var(--mpm-serif); font-style:italic; color:#6d6b60;
      font-size:.82rem; letter-spacing:.06em; margin:.65rem 0 0;}
    .mpm-cover {position:relative; isolation:isolate; overflow:hidden; min-height:350px;
      display:flex; align-items:center; background:#102430; margin:0;}
    .mpm-cover img.mpm-cover-image {position:absolute; inset:0; width:100%; height:100%; object-fit:cover;
      object-position:center; z-index:-2;}
    .mpm-cover::after {content:''; position:absolute; inset:0; z-index:-1;
      background:linear-gradient(90deg,rgba(9,25,35,.88),rgba(9,25,35,.46) 46%,rgba(9,25,35,.08));}
    .mpm-cover-copy {padding:2.7rem 3rem; color:#fffaf0; max-width:75%;}
    .mpm-eyebrow {font-size:.65rem; letter-spacing:.23em; color:#e5c999; margin-bottom:1.4rem;
      display:flex; align-items:center; gap:.8rem;}
    .mpm-eyebrow::before {content:''; width:25px; height:1px; background:#c7a56d;}
    .mpm-cover h2 {color:#fffaf0; font-family:var(--mpm-serif); font-weight:400;
      font-size:clamp(1.65rem,3.5vw,2.65rem); letter-spacing:.12em; line-height:1.7;
      padding:0; margin:0 0 1.3rem;}
    .mpm-cover p {font-size:.81rem; line-height:1.9; margin:0; color:#ebe7df; letter-spacing:.05em; white-space:pre-line;}
    .mpm-credit {font-size:.6rem; text-align:right; color:#6b6a64; margin:.4rem 0 1.4rem; letter-spacing:.04em;}
    .mpm-section {border-top:1px solid var(--mpm-line); margin:1.8rem 0 .5rem; padding-top:1.6rem;}
    .mpm-kicker {color:#81613a; font-size:.62rem; letter-spacing:.23em; margin:0 0 .6rem;}
    .mpm-section h2 {font-family:var(--mpm-serif); font-size:clamp(1.35rem,2.7vw,1.8rem);
      font-weight:500; line-height:1.55; letter-spacing:.07em; color:var(--mpm-ink); padding:0; margin:0;}
    .mpm-section p {font-size:.82rem; color:var(--mpm-muted); line-height:1.9; margin:.65rem 0 .5rem;}
    [data-testid="stRadio"] [role="radiogroup"] {gap:.5rem 1.5rem; padding:.55rem 0; flex-wrap:wrap;}
    [data-testid="stRadio"] label {font-size:.86rem;}
    [data-testid="stWidgetLabel"] p {font-size:.78rem; font-weight:500; letter-spacing:.04em;}
    [data-testid="stMultiSelect"] [data-baseweb="select"] > div,
    [data-testid="stSelectbox"] [data-baseweb="select"] > div,
    [data-testid="stDateInput"] input {border-radius:3px;}
    [data-baseweb="tag"] {background:#e8e1d5 !important; color:#293840 !important; border-radius:2px !important;}
    [data-baseweb="tag"] span {color:#293840 !important;}
    [data-testid="stButton"] button {border-radius:2px;}
    [data-testid="stMetric"] {border-top:1px solid var(--mpm-line); border-bottom:1px solid var(--mpm-line);
      padding:1rem .1rem; margin:.4rem 0 .8rem;}
    [data-testid="stMetricLabel"] {color:var(--mpm-muted);}
    [data-testid="stMetricValue"] {font-family:var(--mpm-serif); font-weight:400; color:var(--mpm-ink);}
    [data-testid="stAlert"] {border-radius:2px; border:1px solid #ded3ba; background:#f4efe3; color:#4e483d;}
    [data-testid="stAlert"] p {font-size:.8rem; line-height:1.8;}
    [data-testid="stExpander"] {border-radius:2px; border-color:var(--mpm-line);}
    [data-testid="stTabs"] [role="tablist"] {border-bottom:1px solid var(--mpm-line); gap:2rem;}
    .mpm-pick {position:relative; border-top:2px solid #a88b5c; border-bottom:1px solid #d6c9b1;
      padding:1.7rem 1.8rem; background:#f0eade; color:#25323a; margin:1rem 0 1.8rem;}
    .mpm-pick .mpm-label {font-size:.68rem; color:#766144; letter-spacing:.1em;}
    .mpm-pick h3 {font-family:var(--mpm-serif); color:#202e36; margin:.7rem 0; padding:0;
      font-weight:400; font-size:1.5rem; line-height:1.4;}
    .mpm-pick p {margin:.4rem 0 0; font-size:.82rem; line-height:1.8;}
    .mpm-ride {display:flex; align-items:center; gap:1.6rem; padding:1.45rem 0;
      border-bottom:1px solid var(--mpm-line);}
    .mpm-wait {min-width:5rem; text-align:center; font-size:.66rem; line-height:1.55; color:#6b655b;}
    .mpm-wait b {font-family:var(--mpm-serif); font-size:2.1rem; font-weight:400; line-height:1.1; color:#263944;}
    .mpm-name {font-family:var(--mpm-serif); font-size:1.1rem; font-weight:500; line-height:1.6; color:#23333c;}
    .mpm-meta {font-size:.73rem; color:#6b6c64; margin-top:.25rem; line-height:1.8;}
    .mpm-tag {display:inline-block; font-size:.64rem; padding:.1rem .4rem;
      border:1px solid #cbbd9e; color:#6b542f; margin-left:.4rem; vertical-align:middle;}
    @media(max-width:700px) {
      .block-container {padding:3.5rem 1rem 2.5rem;}
      .mpm-masthead {padding:1.15rem 0 1rem; margin-bottom:1rem;}
      .mpm-edition {font-size:.53rem; letter-spacing:.17em;}
      .mpm-cover {min-height:295px;}
      .mpm-cover-copy {padding:2rem 1.5rem; max-width:100%;}
      .mpm-cover img.mpm-cover-image {object-position:60% center;}
      .mpm-cover::after {background:linear-gradient(90deg,rgba(9,25,35,.9),rgba(9,25,35,.25));}
      .mpm-cover h2 {font-size:1.7rem; letter-spacing:.08em;}
      .mpm-cover p {font-size:.75rem;}
      .mpm-pick {padding:1.3rem 1.2rem;}
      .mpm-pick h3 {font-size:1.3rem;}
      .mpm-ride {gap:.85rem;} .mpm-wait {min-width:4rem;}
      .mpm-name {font-size:1rem;} .mpm-credit {font-size:.56rem;}
    }
    </style>
    """, unsafe_allow_html=True)


def hero(section: str, subtitle: str) -> None:
    st.markdown(
        '<header class="mpm-masthead">'
        '<div class="mpm-edition">THE ART OF A LEISURELY DAY</div>'
        f'<h1>{APP_NAME}</h1><p class="mpm-motto">{TAGLINE}</p></header>'
        '<section class="mpm-cover">'
        f'<img class="mpm-cover-image" src="{_hero_image()}" alt="" />'
        '<div class="mpm-cover-copy">'
        f'<div class="mpm-eyebrow">{escape(section)}</div>'
        '<h2>余白まで、<br>楽しむ一日。</h2>'
        f'<p>{escape(subtitle)}</p></div></section>'
        '<div class="mpm-credit">Visual: AI-generated / 架空の風景イメージ</div>',
        unsafe_allow_html=True,
    )


def section_intro(kicker: str, title: str, description: str) -> None:
    st.markdown(
        f'<section class="mpm-section"><div class="mpm-kicker">{escape(kicker)}</div>'
        f'<h2>{escape(title)}</h2><p>{escape(description)}</p></section>',
        unsafe_allow_html=True,
    )
