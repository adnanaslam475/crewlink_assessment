"""Helpers for the Postgres Row-Level Security session variable.

RLS policies compare `local_id` against `current_setting('app.current_local', true)`.
We set that value per request (and per Celery task) with set_config(..., is_local=true),
so it is scoped to the surrounding transaction and resets automatically.

When the value is unset, current_setting(..., true) returns NULL and every scoped
policy matches zero rows — i.e. RLS fails closed.
"""
from contextlib import contextmanager

from django.db import connections, transaction

from .scope import clear_current_local, set_current_local


def set_rls_local(local_id, using="default"):
    """Set the RLS local for the current transaction on `using`.

    Must be called inside a transaction (ATOMIC_REQUESTS guarantees this for
    requests; use rls_scope() for Celery/management code).
    """
    with connections[using].cursor() as cursor:
        cursor.execute(
            "SELECT set_config('app.current_local', %s, true)", [str(local_id)]
        )


@contextmanager
def rls_scope(local_id, using="default"):
    """Open a transaction, set the RLS local + ORM thread-local, and clean up.

    Use this in Celery tasks and management commands that run as the app role.
    """
    set_current_local(local_id)
    try:
        with transaction.atomic(using=using):
            set_rls_local(local_id, using=using)
            yield
    finally:
        clear_current_local()
