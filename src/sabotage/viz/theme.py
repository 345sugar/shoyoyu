"""Small shared visual identity; no remote fonts, images, or scripts."""

from html import escape

import streamlit as st

APP_NAME = "Mouse Park Magic"
TAGLINE = "Less waiting. More magic."


def apply_theme() -> None:
    st.markdown("""
    <style>
    .block-container {max-width: 960px; padding-top: 2rem; padding-bottom: 3rem;}
    .mpm-hero {position:relative; overflow:hidden; padding:2rem; margin:0 0 1.1rem;
      border-radius:24px; color:#fff; background:linear-gradient(120deg,#21234c,#51488a);}
    .mpm-hero::after {content:'✦'; position:absolute; right:1.7rem; top:.4rem;
      font-size:8rem; color:#ffda82; opacity:.85; line-height:1;}
    .mpm-eyebrow {font-size:.74rem; letter-spacing:.17em; font-weight:700;
      color:#e2d9ff; margin-bottom:.6rem;}
    .mpm-hero h1 {color:#fff; font-size:clamp(1.8rem,5vw,2.8rem); line-height:1.15;
      padding:0; margin:0 0 .6rem; max-width:80%; font-weight:800;}
    .mpm-hero p {color:#f0ebff; margin:0; font-size:.95rem;}
    .mpm-pick {border:1px solid #87bca9; border-radius:18px; padding:1.15rem 1.3rem;
      background:#edf8f2; color:#193e33; margin:.6rem 0 1rem;}
    .mpm-pick .mpm-label {font-size:.76rem; font-weight:700; letter-spacing:.07em; color:#3b6b58;}
    .mpm-pick h3 {color:#193e33; margin:.35rem 0; padding:0; font-size:1.25rem;}
    .mpm-pick p {margin:.3rem 0 0; font-size:.88rem;}
    .mpm-ride {display:flex; align-items:center; gap:1rem; padding:1rem 0;
      border-bottom:1px solid rgba(128,128,128,.2);}
    .mpm-wait {min-width:5rem; text-align:center; font-size:.72rem; line-height:1.4;}
    .mpm-wait b {font-size:1.85rem; line-height:1.1;}
    .mpm-name {font-size:1rem; font-weight:650; line-height:1.4;}
    .mpm-meta {font-size:.78rem; opacity:.75; margin-top:.2rem;}
    .mpm-tag {display:inline-block; font-size:.7rem; border-radius:6px;
      padding:.1rem .35rem; background:#eee9fc; color:#51488a; margin-left:.25rem;}
    @media(max-width:600px) {
      .block-container {padding:1.2rem 1rem 2rem;}
      .mpm-hero {padding:1.4rem 1.2rem; border-radius:18px;}
      .mpm-hero::after {font-size:5rem; right:.8rem; top:.9rem;}
      .mpm-ride {gap:.6rem;} .mpm-wait {min-width:4rem;}
    }
    </style>
    """, unsafe_allow_html=True)


def hero(section: str, subtitle: str) -> None:
    st.markdown(
        '<div class="mpm-hero">'
        f'<div class="mpm-eyebrow">{escape(section)}</div>'
        f'<h1>{APP_NAME}</h1><p><strong>{TAGLINE}</strong><br>{escape(subtitle)}</p></div>',
        unsafe_allow_html=True,
    )
