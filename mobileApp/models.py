from uuid import uuid4

from django.conf import settings
from django.db import models


def plan_upload_path(instance, filename):
    return f'plans/{uuid4().hex}.dxf'


def excel_upload_path(instance, filename):
    return f'motor_excel/{uuid4().hex}.xlsx'


def pdf_upload_path(instance, filename):
    return f'pdf_plans/{uuid4().hex}.pdf'


class Plan(models.Model):
    imported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='imported_plans',
    )
    name = models.CharField(max_length=255)
    source = models.FileField(upload_to=plan_upload_path)
    size = models.PositiveIntegerField()
    imported_at = models.DateTimeField(auto_now_add=True)
    svg = models.TextField()
    listing = models.ForeignKey('WorkbookDocument', null=True, blank=True, on_delete=models.SET_NULL, related_name='cad_listings')
    workbook = models.ForeignKey('WorkbookDocument', null=True, blank=True, on_delete=models.SET_NULL, related_name='cad_workspaces')

    class Meta:
        ordering = ['-imported_at']


class Motor(models.Model):
    sheet_name = models.CharField(max_length=31, blank=True)
    plan = models.ForeignKey(Plan, related_name='motors', on_delete=models.CASCADE)
    entity_key = models.CharField(max_length=2048)
    name = models.CharField(max_length=255)
    x = models.FloatField()
    y = models.FloatField()
    z = models.FloatField(default=0)
    svg_x = models.FloatField()
    svg_y = models.FloatField()
    svg_bounds = models.JSONField(default=list)
    excel = models.FileField(upload_to=excel_upload_path, blank=True)
    excel_name = models.CharField(max_length=255, blank=True)
    excel_sheets = models.JSONField(default=list)
    workbook = models.ForeignKey(
        'WorkbookDocument', null=True, blank=True, related_name='motors',
        on_delete=models.SET_NULL,
    )

    class Meta:
        ordering = ['id']


class PdfPlan(models.Model):
    name = models.CharField(max_length=255)
    source = models.FileField(upload_to=pdf_upload_path)
    size = models.PositiveIntegerField()
    imported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='imported_pdf_plans',
    )
    imported_at = models.DateTimeField(auto_now_add=True)
    pages = models.JSONField(default=list)
    listing = models.ForeignKey('WorkbookDocument', null=True, blank=True, on_delete=models.SET_NULL, related_name='pdf_listings')
    workbook = models.ForeignKey('WorkbookDocument', null=True, blank=True, on_delete=models.SET_NULL, related_name='pdf_workspaces')

    class Meta:
        ordering = ['-imported_at']


class WorkbookDocument(models.Model):
    plan_mode = models.CharField(max_length=12, blank=True)
    name = models.CharField(max_length=255)
    content = models.BinaryField()
    revision = models.PositiveIntegerField(default=1)
    is_motor = models.BooleanField(default=False)
    origin_uuid = models.UUIDField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='updated_pdf_workbooks',
    )

    class Meta:
        ordering = ['-updated_at']


class PdfMarker(models.Model):
    sheet_name = models.CharField(max_length=31, blank=True)
    plan = models.ForeignKey(PdfPlan, related_name='markers', on_delete=models.CASCADE)
    page = models.PositiveIntegerField()
    x = models.FloatField()
    y = models.FloatField()
    label = models.CharField(max_length=120, blank=True)
    workbook = models.ForeignKey(
        WorkbookDocument, null=True, blank=True, related_name='markers',
        on_delete=models.SET_NULL,
    )

    class Meta:
        ordering = ['id']


class SavedProject(models.Model):
    """A portable snapshot captured by Guardar plano, for either source format."""
    cad_plan = models.OneToOneField(Plan, null=True, blank=True, on_delete=models.CASCADE, related_name='saved_project')
    pdf_plan = models.OneToOneField(PdfPlan, null=True, blank=True, on_delete=models.CASCADE, related_name='saved_project')
    name = models.CharField(max_length=255)
    content = models.BinaryField(default=bytes)
    saved_at = models.DateTimeField(auto_now=True)
    saved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ['-saved_at']
        constraints = [models.CheckConstraint(condition=(models.Q(cad_plan__isnull=False, pdf_plan__isnull=True) | models.Q(cad_plan__isnull=True, pdf_plan__isnull=False)), name='saved_project_exactly_one_plan')]


class PlanWorkbookSheet(models.Model):
    workbook = models.ForeignKey(WorkbookDocument, on_delete=models.CASCADE, related_name='plan_sheets')
    name = models.CharField(max_length=31)
    is_listing = models.BooleanField(default=False)
    is_primary = models.BooleanField(default=False)
    is_protected = models.BooleanField(default=False)
    listing_order = models.PositiveIntegerField(default=0)
    origin_uuid = models.UUIDField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ['listing_order', 'id']
        constraints = [models.UniqueConstraint(fields=['workbook', 'name'], name='plan_workbook_unique_sheet')]


class SavedMotor(models.Model):
    data_fingerprint = models.CharField(max_length=64, blank=True, db_index=True)
    uuid = models.UUIDField(default=uuid4, unique=True, editable=False)
    name = models.CharField(max_length=120)
    name_key = models.CharField(max_length=240, unique=True)
    fingerprint = models.CharField(max_length=64, unique=True)
    workbook = models.OneToOneField(WorkbookDocument, on_delete=models.PROTECT, related_name='saved_motor')
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ['name_key', 'id']
        constraints = [models.UniqueConstraint(fields=['data_fingerprint'], condition=~models.Q(data_fingerprint=''), name='saved_motor_unique_data')]
