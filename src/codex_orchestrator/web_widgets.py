"""Small shared rendering helpers without workflow decisions."""

import streamlit as st


def bullet_list(items):
    for item in items:
        st.markdown(f"- {item}")
