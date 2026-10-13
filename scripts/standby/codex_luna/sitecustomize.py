"""Explicit standby launcher propagates the same model route to Python helpers."""
import os
if os.environ.get('CODEX_LUNA_ADAPTER') == '1':
    import luna_transport
    import luna_session
    luna_transport.install()
    luna_session.install()
