import uuid

from django.contrib.auth.models import User
from django.db import models

from .managers import LocalScopedManager


class Local(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Member(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active"
        RETIRED = "retired"
        SUSPENDED = "suspended"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    local = models.ForeignKey(Local, on_delete=models.CASCADE, related_name="members")
    full_name = models.TextField()
    email = models.TextField()
    classification = models.TextField()
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = LocalScopedManager()

    class Meta:
        indexes = [
            models.Index(fields=["local", "classification"]),
            models.Index(fields=["local", "status"]),
        ]

    def __str__(self):
        return self.full_name


class Account(models.Model):
    """Links a Django auth user to a local and a role. The authenticated account's
    local_id is the scoping key for Rule 1; role separates member vs leadership.
    Not local-scoped itself: it is read (via request.user) to DERIVE the local
    before the RLS var is set."""

    class Role(models.TextChoices):
        MEMBER = "member"
        LEADERSHIP = "leadership"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="account"
    )
    local = models.ForeignKey(Local, on_delete=models.CASCADE, related_name="accounts")
    role = models.CharField(max_length=20, choices=Role.choices)
    member = models.OneToOneField(
        Member,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="account",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def is_leadership(self):
        return self.role == self.Role.LEADERSHIP

    def __str__(self):
        return f"{self.user.username} ({self.role})"


class Announcement(models.Model):
    class State(models.TextChoices):
        DRAFT = "draft"        # created, editable
        APPROVED = "approved"  # a human approved it; only this can be sent
        SENDING = "sending"    # fan-out in progress
        SENT = "sent"          # fan-out dispatched to every recipient

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    local = models.ForeignKey(
        Local, on_delete=models.CASCADE, related_name="announcements"
    )
    title = models.TextField()
    body = models.TextField()
    push_preview = models.CharField(max_length=120, blank=True, default="")
    needs_ack = models.BooleanField(default=False)
    # null => whole local; otherwise restrict audience to this classification
    classification_filter = models.TextField(null=True, blank=True)
    state = models.CharField(
        max_length=20, choices=State.choices, default=State.DRAFT
    )
    created_by = models.ForeignKey(
        Account,
        null=True,
        on_delete=models.SET_NULL,
        related_name="created_announcements",
    )
    # Denormalized counters for cheap status reads (no GROUP BY over 22k rows).
    total_count = models.IntegerField(default=0)
    sent_count = models.IntegerField(default=0)
    read_count = models.IntegerField(default=0)
    acked_count = models.IntegerField(default=0)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = LocalScopedManager()

    def __str__(self):
        return self.title


class AnnouncementRecipient(models.Model):
    """One row per member per announcement: the fan-out ledger.

    delivery_state is the machine's view (did we send?); read_at/acked_at/rsvp are
    the human's view. They are orthogonal on purpose — a member can read without
    acking, or ack without RSVPing.

    `local` is denormalized from the announcement so one uniform RLS policy
    (local_id = current local) covers this table too. UNIQUE(announcement, member)
    is the backbone of Rule 2.
    """

    class Delivery(models.TextChoices):
        PENDING = "pending"
        SENDING = "sending"
        SENT = "sent"
        FAILED = "failed"

    class Rsvp(models.TextChoices):
        COMING = "coming"
        CANT = "cant"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    announcement = models.ForeignKey(
        Announcement, on_delete=models.CASCADE, related_name="recipients"
    )
    member = models.ForeignKey(
        Member, on_delete=models.CASCADE, related_name="deliveries"
    )
    local = models.ForeignKey(
        Local, on_delete=models.CASCADE, related_name="recipient_rows"
    )
    delivery_state = models.CharField(
        max_length=20, choices=Delivery.choices, default=Delivery.PENDING
    )
    rsvp = models.CharField(
        max_length=10, choices=Rsvp.choices, null=True, blank=True
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    acked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = LocalScopedManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["announcement", "member"],
                name="uniq_recipient_per_member",
            )
        ]
        indexes = [
            models.Index(fields=["announcement", "delivery_state"]),
        ]


class PushLog(models.Model):
    """Every real push dispatch, logged. Feeds Rule 2 detection: the number of
    'sent' rows per announcement must never exceed distinct recipients."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    announcement = models.ForeignKey(
        Announcement, on_delete=models.CASCADE, related_name="push_logs"
    )
    recipient = models.ForeignKey(
        AnnouncementRecipient, on_delete=models.CASCADE, related_name="push_logs"
    )
    member = models.ForeignKey(
        Member, on_delete=models.CASCADE, related_name="push_logs"
    )
    worker = models.TextField(blank=True, default="")  # which instance/worker sent it
    result = models.CharField(max_length=20, default="sent")  # sent | failed
    created_at = models.DateTimeField(auto_now_add=True)
