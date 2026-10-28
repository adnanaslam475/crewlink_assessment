"""Data-level invariant checks for Rule 1 and Rule 2.

Runs against the OWNER connection so it can see ALL rows across locals (the whole
point is to audit for leaks/doubles that per-request RLS would otherwise hide).
Exits non-zero if any invariant is violated — usable in CI or by hand.
"""
from django.core.management.base import BaseCommand
from django.db import connections


class Command(BaseCommand):
    help = "Verify Rule 1 (isolation) and Rule 2 (no double delivery) across all data."

    def handle(self, *args, **options):
        cur = connections["admin"].cursor()

        # Rule 1: no recipient may belong to a different local than its announcement.
        cur.execute(
            """
            SELECT count(*)
            FROM crewlink_announcementrecipient r
            JOIN crewlink_member m       ON m.id = r.member_id
            JOIN crewlink_announcement a ON a.id = r.announcement_id
            WHERE m.local_id <> a.local_id
            """
        )
        cross_member = cur.fetchone()[0]

        cur.execute(
            """
            SELECT count(*)
            FROM crewlink_announcementrecipient r
            JOIN crewlink_announcement a ON a.id = r.announcement_id
            WHERE r.local_id <> a.local_id
            """
        )
        cross_denorm = cur.fetchone()[0]

        rule1_ok = cross_member == 0 and cross_denorm == 0

        # Rule 2: no recipient may have been pushed more than once.
        cur.execute(
            """
            SELECT count(*) FROM (
                SELECT recipient_id
                FROM crewlink_pushlog
                WHERE result = 'sent'
                GROUP BY recipient_id
                HAVING count(*) > 1
            ) dups
            """
        )
        doubled = cur.fetchone()[0]

        cur.execute(
            "SELECT count(*), count(DISTINCT recipient_id) FROM crewlink_pushlog WHERE result = 'sent'"
        )
        total_push, distinct_recipients = cur.fetchone()

        rule2_ok = doubled == 0 and total_push == distinct_recipients

        self.stdout.write(
            f"Rule 1 — cross-local recipients: {cross_member} "
            f"(denormalized mismatch: {cross_denorm}) => "
            + ("PASS" if rule1_ok else "FAIL")
        )
        self.stdout.write(
            f"Rule 2 — recipients delivered >1x: {doubled}; "
            f"total pushes={total_push}, distinct recipients={distinct_recipients} => "
            + ("PASS" if rule2_ok else "FAIL")
        )

        if rule1_ok and rule2_ok:
            self.stdout.write(self.style.SUCCESS("All invariants hold."))
        else:
            self.stderr.write("INVARIANT VIOLATION")
            raise SystemExit(1)
