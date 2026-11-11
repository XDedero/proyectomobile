from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import PermissionDenied
from .accounts import can_edit_user, is_primary_administrator
from .access import is_administrator

# Django's own user/group pages must obey the same administrator boundary.
admin.site.has_permission = lambda request: is_administrator(request.user)
admin.site.index_template = 'mobileApp/django_admin_index.html'


class ApplicationUserAdmin(UserAdmin):
    def delete_queryset(self, request, queryset):
        if queryset.filter(app_profile__cloud_id__isnull=False).exists():
            raise PermissionDenied('Gestione las cuentas compartidas desde el panel de la aplicación.')
        return super().delete_queryset(request, queryset)

    def has_add_permission(self, request):
        return is_primary_administrator(request.user) and super().has_add_permission(request)

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if not is_primary_administrator(request.user):
            fields.extend(('is_staff', 'is_superuser', 'groups', 'user_permissions'))
        if obj and is_primary_administrator(obj):
            fields.extend(('username', 'is_active', 'is_staff', 'is_superuser'))
        return tuple(dict.fromkeys(fields))

    def has_change_permission(self, request, obj=None):
        if obj and getattr(getattr(obj, 'app_profile', None), 'cloud_id', None):
            return False
        return super().has_change_permission(request, obj) and (obj is None or can_edit_user(request.user, obj))

    def has_delete_permission(self, request, obj=None):
        if obj and getattr(getattr(obj, 'app_profile', None), 'cloud_id', None):
            return False
        return super().has_delete_permission(request, obj) and (
            obj is None or (obj.pk != request.user.pk and can_edit_user(request.user, obj)))


admin.site.unregister(get_user_model())
admin.site.register(get_user_model(), ApplicationUserAdmin)

from .models import ButtonCategory, Motor, Plan, PdfPlan, PdfMarker, WorkbookDocument


@admin.register(ButtonCategory)
class ButtonCategoryAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)


class ReadOnlyEquipmentAdmin(admin.ModelAdmin):
    """Keep all file mutations in the validated application endpoints."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Plan)
class PlanAdmin(ReadOnlyEquipmentAdmin):
    list_display = ('name', 'imported_by', 'imported_at')
    search_fields = ('name', 'imported_by__username')
    fields = readonly_fields = ('name', 'imported_by', 'size', 'imported_at')


@admin.register(Motor)
class MotorAdmin(ReadOnlyEquipmentAdmin):
    list_display = ('name', 'plan', 'x', 'y', 'excel_name')
    search_fields = ('name', 'plan__name', 'excel_name')
    fields = readonly_fields = ('name', 'plan', 'entity_key', 'x', 'y', 'z', 'excel_name', 'excel_sheets')


@admin.register(PdfPlan)
class PdfPlanAdmin(ReadOnlyEquipmentAdmin):
    list_display = ('name', 'imported_by', 'imported_at')
    search_fields = ('name',)
    fields = readonly_fields = ('name', 'size', 'imported_by', 'imported_at', 'pages')


@admin.register(PdfMarker)
class PdfMarkerAdmin(ReadOnlyEquipmentAdmin):
    list_display = ('id', 'label', 'plan', 'page', 'workbook')
    fields = readonly_fields = ('plan', 'page', 'x', 'y', 'label', 'workbook')


@admin.register(WorkbookDocument)
class WorkbookDocumentAdmin(ReadOnlyEquipmentAdmin):
    list_display = ('name', 'revision', 'updated_by', 'updated_at')
    search_fields = ('name',)
    fields = readonly_fields = ('name', 'revision', 'updated_by', 'updated_at')
