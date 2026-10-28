from rest_framework import serializers

from .models import Announcement


class AnnouncementCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Announcement
        fields = [
            "title",
            "body",
            "push_preview",
            "needs_ack",
            "classification_filter",
        ]


class AnnouncementSerializer(serializers.ModelSerializer):
    class Meta:
        model = Announcement
        fields = [
            "id",
            "title",
            "body",
            "push_preview",
            "needs_ack",
            "classification_filter",
            "state",
            "total_count",
            "sent_count",
            "read_count",
            "acked_count",
            "sent_at",
            "created_at",
        ]
        read_only_fields = fields


class InboxItemSerializer(serializers.Serializer):
    announcement_id = serializers.UUIDField(source="announcement.id")
    title = serializers.CharField(source="announcement.title")
    body = serializers.CharField(source="announcement.body")
    needs_ack = serializers.BooleanField(source="announcement.needs_ack")
    delivery_state = serializers.CharField()
    read_at = serializers.DateTimeField()
    acked_at = serializers.DateTimeField()
    rsvp = serializers.CharField()
