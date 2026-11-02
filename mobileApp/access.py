"""Shared capability rules for the API and its user interface."""

from functools import wraps

from django.contrib.auth.models import Permission
from django.http import JsonResponse
from django.views.decorators.cache import never_cache

VIEW_PERMISSIONS = ('mobileApp.view_plan', 'mobileApp.view_motor')


def capabilities(user):
    can_view = user.is_authenticated and user.is_active
    can_administer = False
    if user.is_authenticated and user.is_active and user.is_staff:
        can_administer = user.is_superuser or user.has_perms(
            f'{permission.content_type.app_label}.{permission.codename}'
            for permission in Permission.objects.select_related('content_type').all()
        )
    return {
        'can_view': bool(can_view),
        'can_import': bool(can_view),
        'can_assign': bool(can_view),
        'can_change_excel': bool(can_view and (
            user.has_perm('mobileApp.change_workbookdocument') or user.has_perm('mobileApp.change_motor')
        )),
        'can_change_plan': bool(can_view and user.has_perm('mobileApp.change_plan')),
        'can_delete_plan': bool(can_view and user.has_perm('mobileApp.delete_plan')),
        'can_change_pdf_plan': bool(can_view and (user.has_perm('mobileApp.change_pdfplan') or user.has_perm('mobileApp.change_plan'))),
        'can_delete_pdf_plan': bool(can_view and (user.has_perm('mobileApp.delete_pdfplan') or user.has_perm('mobileApp.delete_plan'))),
        'can_administer': bool(can_administer),
    }


def api_permissions(*permissions):
    def decorate(view):
        @wraps(view)
        @never_cache
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated or not request.user.is_active:
                return JsonResponse({'error': 'Inicie sesión con su usuario de Django.'}, status=401)
            if not request.user.has_perms(permissions):
                return JsonResponse({'error': 'No tiene permisos para realizar esta acción.'}, status=403)
            return view(request, *args, **kwargs)
        return wrapped
    return decorate
