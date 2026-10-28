from django.urls import path

from . import views

urlpatterns = [
    path("me/", views.MeView.as_view()),
    path("ai/draft/", views.AIDraftView.as_view()),
    path("announcements/", views.AnnouncementListCreateView.as_view()),
    path("announcements/<uuid:pk>/", views.AnnouncementDetailView.as_view()),
    path("announcements/<uuid:pk>/approve/", views.ApproveView.as_view()),
    path("announcements/<uuid:pk>/send/", views.SendView.as_view()),
    path("announcements/<uuid:pk>/status/", views.StatusView.as_view()),
    path("inbox/", views.InboxView.as_view()),
    path("inbox/<uuid:pk>/read/", views.InboxReadView.as_view()),
    path("inbox/<uuid:pk>/ack/", views.InboxAckView.as_view()),
]
