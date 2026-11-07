"""Minimal private-pilot access screen; no third-party authentication service."""
import time
import threading
import streamlit as st
import pilot_storage

_FAILURES = {}
_LOCK = threading.Lock()


def require_access():
    if not pilot_storage.enabled():
        return
    try:
        records = pilot_storage.users()
        if st.session_state.get('_pilot_identity'):
            pilot_storage.workspace_root()
            return
    except PermissionError:
        st.session_state.clear()
    except (ValueError, OSError):
        st.error('Private pilot access is not configured. Please contact the organiser')
        st.stop()
    st.title('Private pilot access')
    st.caption('Enter the tester name and private access code provided by the organiser')
    with st.form('pilot_sign_in'):
        name = st.text_input('Tester name', max_chars=64)
        code = st.text_input('Access code', type='password', max_chars=128)
        submit = st.form_submit_button('Open workspace', type='primary')
    if submit:
        failure_key = name if name in records else '__unknown__'
        with _LOCK:
            failures, blocked_until = _FAILURES.get(failure_key, (0, 0))
            blocked = time.monotonic() < blocked_until
            identity = None if blocked else pilot_storage.authenticate(name, code)
            if not identity and not blocked:
                failures += 1
                _FAILURES[failure_key] = (failures, time.monotonic() + 60 if failures >= 5 else 0)
            if identity:
                _FAILURES.pop(failure_key, None)
        if identity:
            st.session_state.clear()
            st.session_state['_pilot_identity'] = identity
            st.rerun()
        st.error('Unable to sign in. Check your access details or try again later')
    st.stop()
