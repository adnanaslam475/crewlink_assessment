"""Thread-local 'current local' used by the scoped ORM manager.

This is the belt-and-suspenders layer. The real, fail-closed guard is Postgres
Row-Level Security (see db.set_rls_local + the RLS migration). This just makes the
default Django queryset local-bounded too, so app code reads naturally.
"""
import threading

_state = threading.local()


def set_current_local(local_id):
    _state.local_id = str(local_id) if local_id is not None else None


def get_current_local():
    return getattr(_state, "local_id", None)


def clear_current_local():
    _state.local_id = None
