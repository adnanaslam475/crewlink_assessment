"""Celery fan-out: materialize recipients, then dispatch pushes in resumable,
exactly-once chunks.

Rule 2 lives here, in shared DB state (never in worker memory):
- materialize is `INSERT ... SELECT ... ON CONFLICT DO NOTHING` — re-running creates
  no duplicate recipient rows.
- each dispatch chunk claims `pending` rows with `FOR UPDATE SKIP LOCKED`, marks them
  `sent`, writes the (mock) push, and bumps the counter — all in one transaction. A
  crash rolls the chunk back (rows stay `pending`); a restart or a second worker only
  ever sees `pending` rows, so nobody is sent twice, even across two instances.
"""
import logging
import socket

from celery import shared_task
from django.conf import settings
from django.db import InterfaceError, OperationalError, connections
from django.utils import timezone
from kombu.exceptions import OperationalError as BrokerOperationalError

from .db import rls_scope
from .models import Announcement

DEFAULT = "default"
logger = logging.getLogger(__name__)


def enqueue_announcement(announcement_id, local_id):
    """Best-effort fast path; the committed 'sending' row survives a lost publish."""
    try:
        fan_out_announcement.apply_async(
            args=(str(announcement_id), str(local_id)), retry=False
        )
    except BrokerOperationalError:
        logger.warning(
            "Could not queue announcement %s; automatic recovery will retry it.",
            announcement_id,
        )
        return False
    return True


@shared_task
def recover_pending_announcements():
    """Recover even when the original task was never published or Redis lost it.

    Only discovery uses the owner connection, to see sending IDs across locals.
    Actual recipient creation/dispatch still uses the app connection and RLS.
    Repeated/overlapping recovery tasks are safe: dispatch claims pending rows only.
    """
    pending = (
        Announcement._base_manager.using("admin")
        .filter(state=Announcement.State.SENDING)
        .values_list("id", "local_id")
    )
    queued = 0
    for announcement_id, local_id in pending.iterator(chunk_size=100):
        if not enqueue_announcement(announcement_id, local_id):
            break  # Broker unavailable; the next scheduled scan will try again.
        queued += 1
    return {"queued": queued}


@shared_task(
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    autoretry_for=(OperationalError, InterfaceError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=None,
)
def fan_out_announcement(self, announcement_id, local_id):
    worker = f"{socket.gethostname()}#{self.request.id or 'eager'}"
    if not _materialize(announcement_id, local_id):
        return {"announcement_id": str(announcement_id), "skipped": True}
    while _dispatch_chunk(announcement_id, local_id, worker, settings.FANOUT_CHUNK_SIZE):
        pass
    _finish_if_complete(announcement_id, local_id)
    return {"announcement_id": str(announcement_id), "worker": worker}


def _materialize(announcement_id, local_id):
    with rls_scope(local_id):
        # Serialize materialization with completion. A stale recovery task must not
        # reopen a completed announcement or bypass the human approval gate.
        ann = Announcement.objects.select_for_update().filter(id=announcement_id).first()
        if ann is None or ann.state != Announcement.State.SENDING:
            return False
        if ann.total_count > 0:
            return True  # Resume the committed audience snapshot, without adding new members.
        cls = ann.classification_filter
        with connections[DEFAULT].cursor() as cur:
            cur.execute(
                """
                INSERT INTO crewlink_announcementrecipient
                    (id, announcement_id, member_id, local_id, delivery_state, created_at)
                SELECT gen_random_uuid(), %s, m.id, m.local_id, 'pending', now()
                FROM crewlink_member m
                WHERE m.local_id = %s AND m.status = 'active'
                  AND (%s IS NULL OR m.classification = %s)
                ON CONFLICT (announcement_id, member_id) DO NOTHING
                """,
                [str(announcement_id), str(local_id), cls, cls],
            )
            cur.execute(
                """
                UPDATE crewlink_announcement
                SET total_count = (
                    SELECT count(*) FROM crewlink_announcementrecipient
                    WHERE announcement_id = %s
                )
                WHERE id = %s
                """,
                [str(announcement_id), str(announcement_id)],
            )
    return True


def _finish_if_complete(announcement_id, local_id):
    with rls_scope(local_id):
        # SKIP LOCKED can return no rows while another worker owns a chunk. Those
        # uncommitted rows still appear pending here, so do not announce completion.
        ann = Announcement.objects.select_for_update().filter(
            id=announcement_id, state=Announcement.State.SENDING
        ).first()
        if ann is not None and not ann.recipients.filter(delivery_state="pending").exists():
            ann.state = Announcement.State.SENT
            ann.sent_at = timezone.now()
            ann.save(update_fields=["state", "sent_at"])


def _dispatch_chunk(announcement_id, local_id, worker, chunk_size):
    with rls_scope(local_id):
        with connections[DEFAULT].cursor() as cur:
            cur.execute(
                """
                SELECT id, member_id FROM crewlink_announcementrecipient
                WHERE announcement_id = %s AND delivery_state = 'pending'
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT %s
                """,
                [str(announcement_id), chunk_size],
            )
            rows = cur.fetchall()
            if not rows:
                return 0
            ids = [r[0] for r in rows]

            # mark sent BEFORE the (mock) push, so a crash never re-sends.
            cur.execute(
                """
                UPDATE crewlink_announcementrecipient
                SET delivery_state = 'sent', sent_at = now()
                WHERE id = ANY(%s)
                """,
                [ids],
            )
            # the mock "push" == a push_log row, in the same transaction => exactly-once.
            cur.executemany(
                """
                INSERT INTO crewlink_pushlog
                    (id, announcement_id, recipient_id, member_id, worker, result, created_at)
                VALUES (gen_random_uuid(), %s, %s, %s, %s, 'sent', now())
                """,
                [(str(announcement_id), r[0], r[1], worker) for r in rows],
            )
            cur.execute(
                "UPDATE crewlink_announcement SET sent_count = sent_count + %s WHERE id = %s",
                [len(ids), str(announcement_id)],
            )
    return len(rows)
