"""Re-runnable seed. Runs against the OWNER connection ('admin') so it can write
across both locals (RLS is bypassed by the owner by design).

Key objects use fixed UUIDs so the README's ## Test Accounts section is stable
across re-runs.
"""
import uuid

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import connections
from django.utils import timezone

from crewlink.models import Account, Announcement, Local, Member

DB = "admin"
PASSWORD = "crewlink-dev"

LOCAL_27 = uuid.UUID("27272727-2727-4727-8727-272727272727")
LOCAL_58 = uuid.UUID("58585858-5858-4858-8858-585858585858")
MEMBER_27 = uuid.UUID("27000000-0000-4000-8000-000000000027")  # Local 27 login member
MEMBER_58 = uuid.UUID("58000000-0000-4000-8000-000000000058")  # Local 58 login + "other local" member
SAMPLE_ANNOUNCEMENT = uuid.UUID("aa270000-0000-4000-8000-000000000001")

CLASSIFICATIONS = [
    "Journeyman Wireman",
    "Apprentice 3rd Year",
    "Apprentice 2nd Year",
    "Foreman",
]


class Command(BaseCommand):
    help = "Seed 2 locals, members, logins, and one already-sent announcement."

    def handle(self, *args, **options):
        self._local(LOCAL_27, "Local 27")
        self._local(LOCAL_58, "Local 58")

        self._fill_members(LOCAL_27, 2000, "27")
        self._fill_members(LOCAL_58, 200, "58")

        m27 = self._member(
            MEMBER_27, LOCAL_27, "Wes Loomis", "wes@local27.test", "Journeyman Wireman"
        )
        m58 = self._member(
            MEMBER_58, LOCAL_58, "Ada Ferro", "ada@local58.test", "Apprentice 3rd Year"
        )

        self._login("lead27", LOCAL_27, Account.Role.LEADERSHIP, None)
        self._login("member27", LOCAL_27, Account.Role.MEMBER, m27)
        self._login("lead58", LOCAL_58, Account.Role.LEADERSHIP, None)
        self._login("member58", LOCAL_58, Account.Role.MEMBER, m58)

        self._sample_announcement()
        self._summary()

    # -- helpers ----------------------------------------------------------
    def _local(self, local_id, name):
        Local.objects.using(DB).update_or_create(id=local_id, defaults={"name": name})

    def _fill_members(self, local_id, target, tag):
        existing = Member.objects.using(DB).filter(local_id=local_id).count()
        if existing >= target:
            return
        objs = [
            Member(
                local_id=local_id,
                full_name=f"Member {i:05d} (L{tag})",
                email=f"m{i:05d}@local{tag}.test",
                classification=CLASSIFICATIONS[i % len(CLASSIFICATIONS)],
                status=Member.Status.ACTIVE,
            )
            for i in range(existing, target)
        ]
        Member.objects.using(DB).bulk_create(objs, batch_size=1000)

    def _member(self, member_id, local_id, name, email, classification):
        member, _ = Member.objects.using(DB).update_or_create(
            id=member_id,
            defaults={
                "local_id": local_id,
                "full_name": name,
                "email": email,
                "classification": classification,
                "status": Member.Status.ACTIVE,
            },
        )
        return member

    def _login(self, username, local_id, role, member):
        user, _ = User.objects.using(DB).get_or_create(username=username)
        user.set_password(PASSWORD)
        user.save(using=DB)
        Account.objects.using(DB).update_or_create(
            user=user,
            defaults={"local_id": local_id, "role": role, "member": member},
        )

    def _sample_announcement(self):
        Announcement.objects.using(DB).update_or_create(
            id=SAMPLE_ANNOUNCEMENT,
            defaults={
                "local_id": LOCAL_27,
                "title": "Emergency meeting Thursday 6pm",
                "body": (
                    "Contractor is pulling crews off the westside job. Everyone needs "
                    "to be at the hall Thursday 6pm — this is the third time."
                ),
                "push_preview": "Emergency mtg Thu 6pm at the hall — westside contractor issue",
                "needs_ack": True,
                "state": Announcement.State.SENT,
                "sent_at": timezone.now(),
            },
        )
        with connections[DB].cursor() as cur:
            cur.execute(
                """
                INSERT INTO crewlink_announcementrecipient
                    (id, announcement_id, member_id, local_id, delivery_state, sent_at, created_at)
                SELECT gen_random_uuid(), %s, m.id, m.local_id, 'sent', now(), now()
                FROM crewlink_member m
                WHERE m.local_id = %s AND m.status = 'active'
                ON CONFLICT (announcement_id, member_id) DO NOTHING
                """,
                [str(SAMPLE_ANNOUNCEMENT), str(LOCAL_27)],
            )
            cur.execute(
                "UPDATE crewlink_announcementrecipient SET read_at = now() "
                "WHERE announcement_id = %s AND read_at IS NULL AND random() < 0.6",
                [str(SAMPLE_ANNOUNCEMENT)],
            )
            cur.execute(
                "UPDATE crewlink_announcementrecipient "
                "SET acked_at = now(), read_at = COALESCE(read_at, now()) "
                "WHERE announcement_id = %s AND acked_at IS NULL AND random() < 0.4",
                [str(SAMPLE_ANNOUNCEMENT)],
            )
            cur.execute(
                """
                UPDATE crewlink_announcement a SET
                    total_count = (SELECT count(*) FROM crewlink_announcementrecipient r WHERE r.announcement_id = a.id),
                    sent_count  = (SELECT count(*) FROM crewlink_announcementrecipient r WHERE r.announcement_id = a.id AND r.delivery_state = 'sent'),
                    read_count  = (SELECT count(*) FROM crewlink_announcementrecipient r WHERE r.announcement_id = a.id AND r.read_at IS NOT NULL),
                    acked_count = (SELECT count(*) FROM crewlink_announcementrecipient r WHERE r.announcement_id = a.id AND r.acked_at IS NOT NULL)
                WHERE a.id = %s
                """,
                [str(SAMPLE_ANNOUNCEMENT)],
            )

    def _summary(self):
        self.stdout.write(self.style.SUCCESS("Seed complete."))
        self.stdout.write(f"  Local 27 id             : {LOCAL_27}")
        self.stdout.write(f"  Local 58 id             : {LOCAL_58}")
        self.stdout.write(f"  Sample announcement id  : {SAMPLE_ANNOUNCEMENT}")
        self.stdout.write(f"  Member in Local 58 id   : {MEMBER_58}")
        self.stdout.write("  Logins (password 'crewlink-dev'): lead27, member27, lead58, member58")
