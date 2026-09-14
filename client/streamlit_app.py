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

with st.sidebar:
    st.subheader("Session")
    st.code(st.session_state.session_id)
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
