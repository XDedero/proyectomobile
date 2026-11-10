from uuid import uuid4

from django.conf import settings
from django.db import models
from django.db.models.functions import Lower


def plan_upload_path(instance, filename):
    return f'plans/{uuid4().hex}.dxf'


def excel_upload_path(instance, filename):
    return f'motor_excel/{uuid4().hex}.xlsx'


def pdf_upload_path(instance, filename):
    return f'pdf_plans/{uuid4().hex}.pdf'


def new_cloud_key():
    return f'project_{uuid4().hex}'


class UserProfile(models.Model):
    class Role(models.TextChoices):
        VIEWER = 'viewer', 'Espectador'
        WORKER = 'worker', 'Trabajador'
        ADMINISTRATOR = 'administrator', 'Administrador'

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='app_profile')
    rut = models.CharField(max_length=10, unique=True)
    rut_is_test = models.BooleanField(default=False)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.VIEWER)
    is_primary_administrator = models.BooleanField(default=False, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['is_primary_administrator'], condition=models.Q(is_primary_administrator=True),
                                    name='one_primary_administrator'),
            models.CheckConstraint(condition=models.Q(is_primary_administrator=False) | models.Q(role='administrator'),
                                   name='primary_must_be_administrator'),
        ]

    def clean(self):
        from .accounts import normalize_rut
        super().clean()
        self.rut = normalize_rut(self.rut)

    def save(self, *args, **kwargs):
        from .accounts import normalize_rut
        self.rut = normalize_rut(self.rut)
        return super().save(*args, **kwargs)


class RegistrationRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pendiente'
        APPROVED = 'approved', 'Aceptada'
        REJECTED = 'rejected', 'Eliminada'

    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    rut = models.CharField(max_length=10)
    password_hash = models.CharField(max_length=128, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='reviewed_registrations')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name='registration_requests')

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [models.UniqueConstraint(fields=['rut'], condition=models.Q(status='pending'),
                                               name='unique_pending_registration_rut')]

    def save(self, *args, **kwargs):
        from .accounts import normalize_rut
        self.rut = normalize_rut(self.rut)
        return super().save(*args, **kwargs)


class Plan(models.Model):
    sectors = models.JSONField(default=list, blank=True)
    display_settings = models.JSONField(default=dict, blank=True)
    owner_username = models.CharField(max_length=150, blank=True)
    cloud_key = models.CharField(max_length=80, default=new_cloud_key, unique=True, editable=False)
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


class ButtonCategory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    name = models.CharField('nombre', max_length=80, unique=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'categoría de botón'
        verbose_name_plural = 'categorías de botones'
        constraints = [models.UniqueConstraint(Lower('name'), name='unique_button_category_name')]

    def __str__(self):
        return self.name


class CategoryWorkbook(models.Model):
    category = models.ForeignKey(ButtonCategory, on_delete=models.CASCADE, related_name='templates')
    name = models.CharField(max_length=255)
    content = models.BinaryField()
    sheet = models.CharField(max_length=31)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name', 'id']


class PlanRole(models.Model):
    name = models.CharField(max_length=80, unique=True)
    description = models.CharField(max_length=255, blank=True)
    can_view = models.BooleanField(default=True)
    can_export = models.BooleanField(default=True)
    can_create = models.BooleanField(default=True)
    all_plans = models.BooleanField(default=True)
    cad_plans = models.ManyToManyField(Plan, blank=True)
    pdf_plans = models.ManyToManyField('PdfPlan', blank=True)
    users = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name='plan_roles')

    class Meta:
        ordering = ['name']
        permissions = [('administer_application', 'Administrar usuarios, roles y categorías')]


class PlanRoleGroup(models.Model):
    name = models.CharField(max_length=80, unique=True)
    roles = models.ManyToManyField(PlanRole, blank=True, related_name='role_groups')
    users = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name='plan_role_groups')

    class Meta:
        ordering = ['name']


class ButtonSource(models.Model):
    button_scale = models.FloatField(default=1)
    piece_source = models.BooleanField(default=False)
    area_shape = models.CharField(max_length=12, default='ellipse')
    descriptions = models.JSONField(null=True, blank=True)
    button_color = models.CharField(max_length=7, blank=True)
    show_description = models.BooleanField(default=False)
    source_workbook = models.ForeignKey('WorkbookDocument', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    source_sheet = models.CharField(max_length=31, blank=True)
    source_output = models.CharField(max_length=31, blank=True)
    source_revision = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        abstract = True


class Motor(ButtonSource):
    sheet_name = models.CharField(max_length=31, blank=True)
    category = models.ForeignKey(ButtonCategory, null=True, blank=True, on_delete=models.SET_NULL, related_name='cad_buttons')
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
    has_area = models.BooleanField(default=False)
    area_radius_x = models.FloatField(null=True, blank=True)
    area_radius_y = models.FloatField(null=True, blank=True)
    area_color = models.CharField(max_length=50, blank=True, default='rgba(59, 130, 246, 0.25)')

    class Meta:
        ordering = ['id']


class PdfPlan(models.Model):
    sectors = models.JSONField(default=list, blank=True)
    display_settings = models.JSONField(default=dict, blank=True)
    owner_username = models.CharField(max_length=150, blank=True)
    cloud_key = models.CharField(max_length=80, default=new_cloud_key, unique=True, editable=False)
    cloud_snapshot = models.CharField(max_length=64, unique=True, null=True, blank=True)
    cloud_origin = models.CharField(max_length=128, blank=True, db_index=True)
    cloud_workbook_version = models.CharField(max_length=192, blank=True)
    cloud_base_state = models.JSONField(null=True, blank=True)
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
    source_pieces = models.JSONField(default=dict, blank=True)
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


class PdfMarker(ButtonSource):
    sheet_name = models.CharField(max_length=31, blank=True)
    category = models.ForeignKey(ButtonCategory, null=True, blank=True, on_delete=models.SET_NULL, related_name='pdf_buttons')
    plan = models.ForeignKey(PdfPlan, related_name='markers', on_delete=models.CASCADE)
    page = models.PositiveIntegerField()
    x = models.FloatField()
    y = models.FloatField()
    label = models.CharField(max_length=120, blank=True)
    workbook = models.ForeignKey(
        WorkbookDocument, null=True, blank=True, related_name='markers',
        on_delete=models.SET_NULL,
    )
    has_area = models.BooleanField(default=False)
    area_radius_x = models.FloatField(null=True, blank=True)
    area_radius_y = models.FloatField(null=True, blank=True)
    area_color = models.CharField(max_length=50, blank=True, default='rgba(59, 130, 246, 0.25)')

    class Meta:
        ordering = ['id']


class SavedProject(models.Model):
    cloud_receipt = models.JSONField(default=dict, blank=True)
    """A portable snapshot captured by Guardar plano, for either source format."""
    cad_plan = models.OneToOneField(Plan, null=True, blank=True, on_delete=models.CASCADE, related_name='saved_project')
    pdf_plan = models.OneToOneField(PdfPlan, null=True, blank=True, on_delete=models.CASCADE, related_name='saved_project')
    name = models.CharField(max_length=255)
    content = models.BinaryField(default=bytes)
    saved_at = models.DateTimeField(auto_now=True)
    saved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    cloud_status = models.CharField(max_length=16, default='local')
    cloud_error = models.CharField(max_length=500, blank=True)
    cloud_stage = models.CharField(max_length=24, default='local')
    cloud_progress = models.PositiveSmallIntegerField(default=0)

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


class SavedPiece(models.Model):
    cloud_version = models.CharField(max_length=64, blank=True)
    category = models.ForeignKey(ButtonCategory, null=True, blank=True, on_delete=models.SET_NULL)
    cloud_sheet = models.CharField(max_length=31, blank=True)
    cloud_status = models.CharField(max_length=16, default='local')
    data_fingerprint = models.CharField(max_length=64, blank=True, db_index=True)
    uuid = models.UUIDField(default=uuid4, unique=True, editable=False)
    name = models.CharField(max_length=120)
    name_key = models.CharField(max_length=240, unique=True)
    fingerprint = models.CharField(max_length=64, unique=True)
    workbook = models.OneToOneField(WorkbookDocument, on_delete=models.PROTECT, related_name='saved_motor')
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        db_table = 'mobileApp_savedmotor'
        ordering = ['name_key', 'id']
        constraints = [models.UniqueConstraint(fields=['data_fingerprint'], condition=~models.Q(data_fingerprint=''), name='saved_motor_unique_data')]


# Compatibility for existing project archives and older API clients.
SavedMotor = SavedPiece


class PieceDeletion(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    synced = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
