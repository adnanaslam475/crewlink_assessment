from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_safe


@transaction.non_atomic_requests
@require_safe
def health(request):
    """Public liveness check; does not open a database transaction."""
    return JsonResponse({"status": "ok"})
