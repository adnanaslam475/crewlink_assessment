from rest_framework.permissions import BasePermission


class HasAccount(BasePermission):
    """Authenticated and linked to an Account (local + role)."""

    message = "No CrewLink account is linked to this user."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user and user.is_authenticated and getattr(user, "account", None)
        )


class IsLeadership(HasAccount):
    message = "Leadership role required."

    def has_permission(self, request, view):
        return (
            super().has_permission(request, view)
            and request.user.account.is_leadership
        )


class IsMember(HasAccount):
    message = "Member role required."

    def has_permission(self, request, view):
        return (
            super().has_permission(request, view)
            and request.user.account.role == "member"
        )
