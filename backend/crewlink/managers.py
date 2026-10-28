from django.db import models

from .scope import get_current_local


class LocalScopedManager(models.Manager):
    """Default manager that bounds the queryset to the current local when one is set.

    This mirrors the Postgres RLS guard at the ORM layer so application code reads
    naturally (`Member.objects.all()` already means "this local's members"). RLS is
    the authoritative, fail-closed enforcement; this is the convenience layer on top.

    When no current local is set (migrations, seed, admin role), it returns everything
    — those paths run as the DB owner, which bypasses RLS by design.
    """

    def get_queryset(self):
        qs = super().get_queryset()
        local_id = get_current_local()
        if local_id is not None:
            qs = qs.filter(local_id=local_id)
        return qs
