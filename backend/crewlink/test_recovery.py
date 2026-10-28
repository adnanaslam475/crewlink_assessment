import threading
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import connections
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from kombu.exceptions import OperationalError as BrokerOperationalError
from rest_framework.test import APIClient

from config.celery import recover_sends_on_worker_start
from .db import rls_scope
from .models import Account, Announcement, AnnouncementRecipient, Local, Member, PushLog
from .scope import clear_current_local
from .tasks import (
    _dispatch_chunk,
    _finish_if_complete,
    _materialize,
    enqueue_announcement,
    fan_out_announcement,
    recover_pending_announcements,
)


class RecoveryQueueTests(SimpleTestCase):
    def test_failed_publish_is_left_for_recovery(self):
        with patch(
            "crewlink.tasks.fan_out_announcement.apply_async",
            side_effect=BrokerOperationalError("broker unavailable"),
        ):
            self.assertFalse(enqueue_announcement("announcement", "local"))

    def test_worker_start_requests_an_immediate_recovery_scan(self):
        with patch("crewlink.tasks.recover_pending_announcements.apply_async") as publish:
            recover_sends_on_worker_start()
        publish.assert_called_once_with(retry=False)

    def test_unavailable_broker_does_not_break_worker_startup(self):
        with patch(
            "crewlink.tasks.recover_pending_announcements.apply_async",
            side_effect=BrokerOperationalError("broker unavailable"),
        ):
            recover_sends_on_worker_start()


@override_settings(FANOUT_CHUNK_SIZE=1)
class SendRecoveryTests(TransactionTestCase):
    databases = {"default", "admin"}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command("grant_app_privileges", verbosity=0)

    def setUp(self):
        clear_current_local()
        self.addCleanup(clear_current_local)
        self.local = Local.objects.using("admin").create(name="Recovery local")
        self.other_local = Local.objects.using("admin").create(name="Other local")
        for index in range(3):
            Member.objects.using("admin").create(
                local=self.local,
                full_name=f"Member {index}",
                email=f"member{index}@example.test",
                classification="Wireman",
            )
        self.ann = Announcement.objects.using("admin").create(
            local=self.local, title="Recovery", body="Test", state="sending"
        )

    def assert_delivered_once(self, announcement=None, expected=3):
        announcement = announcement or self.ann
        announcement.refresh_from_db(using="admin")
        rows = AnnouncementRecipient.objects.using("admin").filter(
            announcement=announcement
        )
        logs = PushLog.objects.using("admin").filter(announcement=announcement)
        self.assertEqual(announcement.state, "sent")
        self.assertEqual(announcement.total_count, expected)
        self.assertEqual(announcement.sent_count, expected)
        self.assertEqual(rows.filter(delivery_state="sent").count(), expected)
        self.assertEqual(logs.count(), expected)
        self.assertEqual(logs.values("recipient_id").distinct().count(), expected)

    def test_crash_between_chunks_resumes_without_resending_committed_chunk(self):
        dispatch = _dispatch_chunk
        calls = 0

        def crash_after_first_chunk(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("simulated process crash")
            return dispatch(*args)

        with patch("crewlink.tasks._dispatch_chunk", side_effect=crash_after_first_chunk):
            with self.assertRaisesRegex(RuntimeError, "simulated process crash"):
                fan_out_announcement.run(str(self.ann.id), str(self.local.id))

        self.assertEqual(PushLog.objects.using("admin").count(), 1)
        self.assertEqual(
            AnnouncementRecipient.objects.using("admin").filter(
                announcement=self.ann, delivery_state="pending"
            ).count(),
            2,
        )
        with patch("crewlink.tasks.enqueue_announcement", return_value=True) as publish:
            self.assertEqual(recover_pending_announcements.run(), {"queued": 1})
        publish.assert_called_once_with(self.ann.id, self.local.id)

        fan_out_announcement.run(str(self.ann.id), str(self.local.id))
        self.assert_delivered_once()
        # A redelivered task after completion cannot create more recipients/pushes.
        fan_out_announcement.run(str(self.ann.id), str(self.local.id))
        self.assert_delivered_once()

    def test_failed_chunk_rolls_back_even_after_a_mock_push_was_inserted(self):
        self.assertTrue(_materialize(str(self.ann.id), str(self.local.id)))
        real_cursor = connections["default"].cursor

        class FailingCursor:
            def __init__(self):
                self.cursor = real_cursor()

            def __enter__(self):
                self.cursor.__enter__()
                return self

            def __exit__(self, *args):
                return self.cursor.__exit__(*args)

            def __getattr__(self, name):
                return getattr(self.cursor, name)

            def executemany(self, sql, params):
                self.cursor.executemany(sql, params[:1])
                raise RuntimeError("crash inside chunk")

        with patch("crewlink.tasks.connections", {"default": SimpleNamespace(cursor=FailingCursor)}):
            with self.assertRaisesRegex(RuntimeError, "crash inside chunk"):
                _dispatch_chunk(str(self.ann.id), str(self.local.id), "crashed", 3)
        self.assertEqual(PushLog.objects.using("admin").count(), 0)
        self.assertEqual(
            AnnouncementRecipient.objects.using("admin").filter(
                announcement=self.ann, delivery_state="pending"
            ).count(),
            3,
        )
        fan_out_announcement.run(str(self.ann.id), str(self.local.id))
        self.assert_delivered_once()

    def test_recovery_discovers_both_locals_but_dispatch_stays_scoped(self):
        Member.objects.using("admin").create(
            local=self.other_local, full_name="Other member", email="other@example.test",
            classification="Wireman",
        )
        other = Announcement.objects.using("admin").create(
            local=self.other_local, title="Other", body="Test", state="sending"
        )
        with patch("crewlink.tasks.enqueue_announcement", return_value=True) as publish:
            self.assertEqual(recover_pending_announcements.run(), {"queued": 2})
        self.assertEqual(
            {call.args for call in publish.call_args_list},
            {(self.ann.id, self.local.id), (other.id, self.other_local.id)},
        )
        fan_out_announcement.run(str(self.ann.id), str(self.local.id))
        fan_out_announcement.run(str(other.id), str(self.other_local.id))
        self.assert_delivered_once()
        self.assert_delivered_once(other, expected=1)
        self.assertEqual(
            AnnouncementRecipient.objects.using("admin").filter(
                announcement=self.ann, member__local=self.other_local
            ).count(),
            0,
        )

    def test_recovery_does_not_send_unapproved_announcements(self):
        self.ann.state = "draft"
        self.ann.save(using="admin", update_fields=["state"])
        with patch("crewlink.tasks.enqueue_announcement") as publish:
            self.assertEqual(recover_pending_announcements.run(), {"queued": 0})
        publish.assert_not_called()
        fan_out_announcement.run(str(self.ann.id), str(self.local.id))
        self.assertEqual(AnnouncementRecipient.objects.using("admin").count(), 0)

    def test_resume_reuses_audience_snapshot(self):
        _materialize(str(self.ann.id), str(self.local.id))
        Member.objects.using("admin").create(
            local=self.local, full_name="Joined later", email="later@example.test",
            classification="Wireman",
        )
        fan_out_announcement.run(str(self.ann.id), str(self.local.id))
        self.assert_delivered_once(expected=3)

    def test_broker_failure_after_commit_can_be_recovered(self):
        user = User.objects.using("admin").create(username="recovery-leader")
        Account.objects.using("admin").create(user=user, local=self.local, role="leadership")
        self.ann.state = "approved"
        self.ann.save(using="admin", update_fields=["state"])
        client = APIClient()
        client.force_authenticate(user)
        with patch(
            "crewlink.tasks.fan_out_announcement.apply_async",
            side_effect=BrokerOperationalError("broker unavailable"),
        ):
            response = client.post(f"/api/announcements/{self.ann.id}/send/")
        self.assertEqual(response.status_code, 202)
        self.ann.refresh_from_db(using="admin")
        self.assertEqual(self.ann.state, "sending")
        clear_current_local()
        with patch("crewlink.tasks.enqueue_announcement", return_value=True) as publish:
            self.assertEqual(recover_pending_announcements.run(), {"queued": 1})
        publish.assert_called_once_with(self.ann.id, self.local.id)
        fan_out_announcement.run(str(self.ann.id), str(self.local.id))
        self.assert_delivered_once()

    def test_locked_pending_rows_do_not_mark_send_complete(self):
        _materialize(str(self.ann.id), str(self.local.id))
        locked = threading.Event()
        release = threading.Event()
        errors = []

        def hold_recipient_locks():
            try:
                with rls_scope(str(self.local.id)):
                    with connections["default"].cursor() as cur:
                        cur.execute(
                            "SELECT id FROM crewlink_announcementrecipient "
                            "WHERE announcement_id = %s FOR UPDATE",
                            [str(self.ann.id)],
                        )
                        locked.set()
                        if not release.wait(5):
                            raise RuntimeError("Timed out waiting to release recipient locks")
            except Exception as exc:
                errors.append(exc)
                locked.set()
            finally:
                connections["default"].close()

        thread = threading.Thread(target=hold_recipient_locks)
        thread.start()
        try:
            self.assertTrue(locked.wait(5))
            self.assertFalse(errors)
            self.assertEqual(
                _dispatch_chunk(str(self.ann.id), str(self.local.id), "other-worker", 3), 0
            )
            _finish_if_complete(str(self.ann.id), str(self.local.id))
            self.ann.refresh_from_db(using="admin")
            self.assertEqual(self.ann.state, "sending")
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors)
        fan_out_announcement.run(str(self.ann.id), str(self.local.id))
        self.assert_delivered_once()
