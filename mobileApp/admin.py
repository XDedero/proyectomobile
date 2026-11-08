from django.contrib import admin
from .access import is_administrator

# Django's own user/group pages must obey the same administrator boundary.
admin.site.has_permission = lambda request: is_administrator(request.user)

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
