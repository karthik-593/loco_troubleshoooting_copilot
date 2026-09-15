"""Streamlit thin client — chat UI over ``POST /diagnose`` (BUILD_PLAN §3).

Run the API first (``uvicorn api.server:app``), then ``streamlit run client/streamlit_app.py``.
The client holds no diagnostic logic: it sends pilot turns and renders replies plus the
engine trace (terminal kind, tool path, TSD citation) so the reflex short-circuit and
tool-path divergence are visible in the demo.
"""
from __future__ import annotations

import os
import uuid

import httpx
import streamlit as st

API_URL = os.environ.get("COPILOT_API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="Loco Troubleshooting Copilot", page_icon="🚂", layout="centered")
st.title("Loco Troubleshooting Verification Copilot")
st.caption("Conventional AC locos · verification against the SCR/ETTC TSD · **demonstrator, not certified**")

if "session_id" not in st.session_state:
    st.session_state.session_id = uuid.uuid4().hex[:12]
    st.session_state.messages = []

TYPES = ["unknown", "wag7", "wag5", "wap4"]
TYPE_LABEL = {"unknown": "unknown", "wag7": "WAG-7", "wag5": "WAG-5", "wap4": "WAP-4"}
CONFIGS = ["unknown", "siv", "arno"]
CONFIG_LABEL = {"unknown": "unknown", "siv": "SIV", "arno": "ARNO"}


def _loco_row(label: str, key: str, current: dict) -> dict:
    st.markdown(f"**{label}**")
    c1, c2, c3 = st.columns([1.2, 1, 1])
    num = c1.text_input("Loco no.", value=current.get("loco_number", ""), key=f"{key}_num")
    typ = c2.selectbox("Type", TYPES, index=TYPES.index(current.get("type", "unknown")),
                       format_func=lambda v: TYPE_LABEL[v], key=f"{key}_type")
    cfg = c3.selectbox("Config", CONFIGS, index=CONFIGS.index(current.get("config", "unknown")),
                       format_func=lambda v: CONFIG_LABEL[v], key=f"{key}_cfg")
    return {"loco_number": num, "type": typ, "config": cfg}


with st.sidebar:
    st.subheader("Session")
    st.code(st.session_state.session_id)

    # ---- session bar: locos (three fields each; single or leading + trailing; swap) ----
    st.subheader("Loco(s)")
    try:
        cur = httpx.get(f"{API_URL}/session/{st.session_state.session_id}", timeout=10).json()
        cur_locos, cur_active = cur.get("locos", [{}]), cur.get("active_loco", 0)
    except (httpx.HTTPError, ValueError):
        cur_locos, cur_active = [{}], 0
    multi = st.toggle("Multi (leading + trailing)", value=len(cur_locos) == 2)
    rows = [_loco_row("Leading", "lead", cur_locos[0] if cur_locos else {})]
    if multi:
        rows.append(_loco_row("Trailing", "trail", cur_locos[1] if len(cur_locos) > 1 else {}))
        active = st.radio("Fault is on", [0, 1], index=min(cur_active, 1), horizontal=True,
                          format_func=lambda i: ["leading", "trailing"][i])
    else:
        active = 0
    b1, b2 = st.columns(2)
    try:
        if b1.button("Apply"):
            httpx.put(f"{API_URL}/session/{st.session_state.session_id}/locos",
                      json={"locos": rows, "active": active}, timeout=10).raise_for_status()
            st.rerun()
        if multi and b2.button("Swap ⇄"):
            httpx.post(f"{API_URL}/session/{st.session_state.session_id}/swap", timeout=10).raise_for_status()
            st.rerun()
    except httpx.HTTPError as exc:
        st.error(f"API not reachable at {API_URL} — start it with `uvicorn api.server:app` ({exc})")
    if st.button("New session"):
        try:
            httpx.delete(f"{API_URL}/session/{st.session_state.session_id}", timeout=10)
        except httpx.HTTPError:
            pass
        st.session_state.session_id = uuid.uuid4().hex[:12]
        st.session_state.messages = []
        st.rerun()
    st.subheader("Engine state")
    try:
        state = httpx.get(f"{API_URL}/session/{st.session_state.session_id}", timeout=10).json()
        st.json({k: state[k] for k in ("matched_fault", "fault_confirmed", "steps_claimed_done",
                                       "history_facts", "intended_action", "stuck_at", "reflex_verdict")})
    except (httpx.HTTPError, ValueError, KeyError):
        st.warning(f"API not reachable at {API_URL}")

for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["text"])
        if m.get("trace"):
            t = m["trace"]
            st.caption(f"`{t['terminal']}` · path: {' → '.join(t['tool_path']) or '—'} · "
                       f"stop: {t['stop_reason']} · reflex×{t['reflex_runs']} · {t['source']}"
                       + (" · *KB text (phrase fallback)*" if t.get("phrase_fallback") else ""))

if prompt := st.chat_input("Describe the fault and what you have done…"):
    st.session_state.messages.append({"role": "user", "text": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    try:
        r = httpx.post(f"{API_URL}/diagnose",
                       json={"session_id": st.session_state.session_id, "pilot_turn": prompt}, timeout=120)
        r.raise_for_status()
        data = r.json()
        trace = {k: data[k] for k in ("terminal", "tool_path", "stop_reason", "reflex_runs", "source", "phrase_fallback")}
        st.session_state.messages.append({"role": "assistant", "text": data["reply"], "trace": trace})
    except httpx.HTTPError as exc:
        st.session_state.messages.append({"role": "assistant", "text": f"⚠️ API error: {exc}"})
    st.rerun()
