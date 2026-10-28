from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status as http
from rest_framework.response import Response
from rest_framework.views import APIView

from . import ai
from .db import set_rls_local
from .models import Announcement, AnnouncementRecipient
from .permissions import HasAccount, IsLeadership, IsMember
from .scope import set_current_local
from .serializers import (
    AnnouncementCreateSerializer,
    AnnouncementSerializer,
    InboxItemSerializer,
)
from .tasks import enqueue_announcement


class ScopedView(APIView):
    """Base view: after authentication, pin the request to the user's local.

    Sets both the Postgres RLS session var (the authoritative, fail-closed guard)
    and the ORM thread-local (the convenience filter). Because every endpoint
    extends this, a new endpoint added later is local-scoped by default — it would
    have to go out of its way to leak. ATOMIC_REQUESTS wraps the request in a
    transaction, so SET LOCAL here is scoped to it and resets automatically.
    """

    permission_classes = [HasAccount]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)  # runs auth + permissions
        account = getattr(request.user, "account", None)
        if account is not None:
            set_current_local(account.local_id)
            set_rls_local(account.local_id)
        request.account = account


class MeView(ScopedView):
    def get(self, request):
        a = request.account
        return Response(
            {
                "username": request.user.username,
                "role": a.role,
                "local_id": str(a.local_id),
                "local_name": a.local.name,
                "member_id": str(a.member_id) if a.member_id else None,
            }
        )


class AIDraftView(ScopedView):
    permission_classes = [IsLeadership]

    def post(self, request):
        return Response(ai.generate_draft(request.data.get("raw_text", "")))


class AnnouncementListCreateView(ScopedView):
    permission_classes = [IsLeadership]

    def get(self, request):
        qs = Announcement.objects.all().order_by("-created_at")
        return Response(AnnouncementSerializer(qs, many=True).data)

    def post(self, request):
        ser = AnnouncementCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ann = Announcement(
            local_id=request.account.local_id,
            created_by=request.account,
            state=Announcement.State.DRAFT,
            **ser.validated_data,
        )
        ann.save()
        return Response(
            AnnouncementSerializer(ann).data, status=http.HTTP_201_CREATED
        )


class AnnouncementDetailView(ScopedView):
    permission_classes = [IsLeadership]

    def get(self, request, pk):
        ann = get_object_or_404(Announcement.objects.all(), id=pk)
        return Response(AnnouncementSerializer(ann).data)


class ApproveView(ScopedView):
    """Human approval gate: draft -> approved. Only an approved announcement can send."""

    permission_classes = [IsLeadership]

    def post(self, request, pk):
        updated = Announcement.objects.filter(
            id=pk, state=Announcement.State.DRAFT
        ).update(state=Announcement.State.APPROVED)
        if not updated:
            ann = get_object_or_404(Announcement.objects.all(), id=pk)
            return Response(
                {"detail": f"Cannot approve from state '{ann.state}'."},
                status=http.HTTP_409_CONFLICT,
            )
        return Response({"id": str(pk), "state": "approved"})


class SendView(ScopedView):
    """approved -> sending, atomically. The conditional UPDATE is the Rule 2 guard:
    a second Send (the 'looks stuck, click again' case) matches no row and starts no
    second fan-out."""

    permission_classes = [IsLeadership]

    def post(self, request, pk):
        updated = Announcement.objects.filter(
            id=pk, state=Announcement.State.APPROVED
        ).update(state=Announcement.State.SENDING)
        if not updated:
            ann = get_object_or_404(Announcement.objects.all(), id=pk)
            return Response(
                {
                    "detail": (
                        f"Cannot send from state '{ann.state}'. Must be 'approved'. "
                        "A second send is a no-op (Rule 2)."
                    )
                },
                status=http.HTTP_409_CONFLICT,
            )
        local_id = str(request.account.local_id)
        # enqueue only after the request transaction commits, so the worker sees 'sending'
        transaction.on_commit(
            lambda: enqueue_announcement(str(pk), local_id)
        )
        return Response({"id": str(pk), "state": "sending"}, status=http.HTTP_202_ACCEPTED)


class StatusView(ScopedView):
    """Live counts, read as one row and cached per local for a few seconds, so N open
    status screens hit the DB at most once per TTL window."""

    permission_classes = [IsLeadership]

    def get(self, request, pk):
        key = f"ann_status:{request.account.local_id}:{pk}"
        data = cache.get(key)
        if data is None:
            ann = get_object_or_404(Announcement.objects.all(), id=pk)
            data = {
                "id": str(ann.id),
                "state": ann.state,
                "total": ann.total_count,
                "sent": ann.sent_count,
                "read": ann.read_count,
                "acked": ann.acked_count,
            }
            cache.set(key, data, settings.STATUS_CACHE_TTL)
        return Response(data)


class InboxView(ScopedView):
    permission_classes = [IsMember]

    def get(self, request):
        qs = (
            AnnouncementRecipient.objects.filter(
                member_id=request.account.member_id,
                announcement__state__in=[
                    Announcement.State.SENDING,
                    Announcement.State.SENT,
                ],
            )
            .select_related("announcement")
            .order_by("-created_at")
        )
        return Response(InboxItemSerializer(qs, many=True).data)


class InboxReadView(ScopedView):
    permission_classes = [IsMember]

    def post(self, request, pk):  # pk = announcement id
        r = get_object_or_404(
            AnnouncementRecipient.objects.all(),
            announcement_id=pk,
            member_id=request.account.member_id,
        )
        if r.read_at is None:
            if AnnouncementRecipient.objects.filter(
                id=r.id, read_at__isnull=True
            ).update(read_at=timezone.now()):
                Announcement.objects.filter(id=pk).update(
                    read_count=F("read_count") + 1
                )
        return Response({"announcement_id": str(pk), "read": True})


class InboxAckView(ScopedView):
    permission_classes = [IsMember]

    def post(self, request, pk):  # pk = announcement id
        r = get_object_or_404(
            AnnouncementRecipient.objects.all(),
            announcement_id=pk,
            member_id=request.account.member_id,
        )
        now = timezone.now()
        if r.read_at is None:
            if AnnouncementRecipient.objects.filter(
                id=r.id, read_at__isnull=True
            ).update(read_at=now):
                Announcement.objects.filter(id=pk).update(
                    read_count=F("read_count") + 1
                )
        if r.acked_at is None:
            if AnnouncementRecipient.objects.filter(
                id=r.id, acked_at__isnull=True
            ).update(acked_at=now):
                Announcement.objects.filter(id=pk).update(
                    acked_count=F("acked_count") + 1
                )
        return Response({"announcement_id": str(pk), "acknowledged": True})
