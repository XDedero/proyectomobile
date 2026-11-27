from uuid import UUID, uuid4

from django.conf import settings
from django.db import models
from django.db.models.functions import Lower

from .file_content import FileContentField


def plan_upload_path(instance, filename):
    return f'plans/{uuid4().hex}.dxf'


def excel_upload_path(instance, filename):
    return f'motor_excel/{uuid4().hex}.xlsx'


def pdf_upload_path(instance, filename):
    return f'pdf_plans/{uuid4().hex}.pdf'


def new_cloud_key():
    return f'project_{uuid4().hex}'


# Existing installations and the legacy shared registry represent this company.
RELONCAVI_COMPANY_ID = UUID('25c4e566-ed01-47dd-8107-a715fcd125d2')


class Company(models.Model):
    class Status(models.TextChoices):
        PENDING_STORAGE = 'pending_storage', 'Pendiente de almacenamiento'
        ACTIVE = 'active', 'Activa'
        SUSPENDED = 'suspended', 'Suspendida'
        ARCHIVED = 'archived', 'Archivada'

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=100, unique=True)
    tax_id = models.CharField(max_length=10, unique=True, blank=True, null=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING_STORAGE)
    drive_ready = models.BooleanField(default=False)

    class Meta:
        ordering = ['name', 'id']
        constraints = [
            models.CheckConstraint(condition=models.Q(status__in=['pending_storage', 'active', 'suspended', 'archived']),
                                   name='company_valid_status'),
            # Lift this restriction when tenant permissions and Drive routing are both in place.
            models.CheckConstraint(condition=~models.Q(status='active') | models.Q(id=RELONCAVI_COMPANY_ID),
                                   name='company_only_legacy_active'),
        ]

    def clean(self):
        from .accounts import normalize_rut
        super().clean()
        self.tax_id = normalize_rut(self.tax_id) if self.tax_id else None

    def __str__(self):
        return self.name


class CompanyBrand(models.Model):
    company = models.OneToOneField(Company, primary_key=True, on_delete=models.CASCADE)
    base_color = models.CharField(max_length=7, blank=True)
    logo_png = models.BinaryField(blank=True, default=bytes)
    revision = models.PositiveIntegerField(default=1)


class CompanyAdminSetup(models.Model):
    """The company administrator provisioned by the owner's panel."""
    company = models.OneToOneField(Company, primary_key=True, on_delete=models.CASCADE)
    rut = models.CharField(max_length=10, unique=True)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    password_hash = models.CharField(max_length=256)


class DriveStorageAccount(models.Model):
    """Private account inventory; OAuth material lives only in Supabase Vault."""
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    number = models.PositiveIntegerField(unique=True)
    account_id = models.CharField(max_length=128, unique=True)
    email = models.EmailField(max_length=254)
    secret_id = models.UUIDField(unique=True, editable=False)
    company = models.OneToOneField(Company, null=True, blank=True, on_delete=models.PROTECT)
    verified_at = models.DateTimeField()

    class Meta:
        ordering = ['number']
        constraints = [models.CheckConstraint(condition=models.Q(number__gte=1), name='drive_account_positive_number')]


class CompanyDriveConnection(models.Model):
    company = models.OneToOneField(Company, primary_key=True, on_delete=models.CASCADE)
    account_id = models.CharField(max_length=128, unique=True)
    root_id = models.CharField(max_length=128, unique=True)
    folder_ids = models.JSONField()
    encrypted_credentials = models.BinaryField(editable=False)
    drive_secret_id = models.UUIDField(null=True, blank=True, editable=False, unique=True)
    verified_at = models.DateTimeField()


class CompanyAudit(models.Model):
    company = models.ForeignKey(Company, null=True, on_delete=models.SET_NULL)
    company_code = models.UUIDField()
    actor = models.CharField(max_length=200)
    action = models.CharField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True)


class UserProfile(models.Model):
    cloud_id = models.UUIDField(null=True, blank=True, unique=True, editable=False)
    cloud_revision = models.PositiveBigIntegerField(default=0, editable=False)
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


class CompanyAccount(models.Model):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name='accounts')
    rut = models.CharField(max_length=10, unique=True)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    password_hash = models.CharField(max_length=256)
    role = models.CharField(max_length=20, choices=UserProfile.Role.choices, default=UserProfile.Role.VIEWER)


class RegistrationRequest(models.Model):
    """Historical records from the retired registration request workflow."""
    cloud_id = models.UUIDField(default=uuid4, unique=True, editable=False)
    cloud_synced = models.BooleanField(default=False, editable=False)
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pendiente'
        APPROVED = 'approved', 'Aceptada'
        REJECTED = 'rejected', 'Eliminada'

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name='registration_requests',
                                default=RELONCAVI_COMPANY_ID, db_default=RELONCAVI_COMPANY_ID)
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
    draft_dirty = models.BooleanField(default=False)
    draft_of = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE, related_name='drafts')
    draft_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name='+')
    draft_version = models.CharField(max_length=64, blank=True)
    draft_markers = models.JSONField(default=dict, blank=True)
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
    maintenance_required = models.BooleanField('mantención obligatoria', default=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'categoría de botón'
        verbose_name_plural = 'categorías de botones'
        constraints = [models.UniqueConstraint(Lower('name'), name='unique_button_category_name')]

    def __str__(self):
        return self.name


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


class MaintenanceUnit(models.TextChoices):
    DAYS = 'days', 'Días'
    WEEKS = 'weeks', 'Semanas'
    MONTHS = 'months', 'Meses'
    YEARS = 'years', 'Años'


class ButtonSource(models.Model):
    button_creator = models.CharField(max_length=150, blank=True)
    maintenance_key = models.UUIDField(null=True, blank=True, editable=False, db_index=True)
    maintenance_responsibles = models.JSONField(null=True, blank=True, editable=False)
    maintenance_supervisors = models.JSONField(null=True, blank=True, editable=False)
    last_maintenance = models.DateField(null=True, blank=True)
    maintenance_interval = models.PositiveSmallIntegerField(null=True, blank=True)
    maintenance_unit = models.CharField(max_length=6, choices=MaintenanceUnit.choices, blank=True)
    next_maintenance = models.DateField(null=True, blank=True, db_index=True)
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

    @property
    def maintenance_required(self):
        return bool(self.category_id and self.category.maintenance_required)

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
    draft_dirty = models.BooleanField(default=False)
    draft_of = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE, related_name='drafts')
    draft_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name='+')
    draft_version = models.CharField(max_length=64, blank=True)
    draft_markers = models.JSONField(default=dict, blank=True)
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
    content = FileContentField(extension='xlsx')
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


class MaintenanceNotification(models.Model):
    class Kind(models.TextChoices):
        ALERT = 'alert', 'Mantención vencida'
        REMINDER = 'reminder', 'Mantención próxima'
        REPLY = 'reply', 'Mensaje de administración'
        GENERAL = 'general', 'Notificación general'
        RETRY = 'retry', 'Mantención por completar'
        CONFIRMED = 'confirmed', 'Mantención confirmada'
        REVIEW = 'review', 'Mantención realizada'

    class PushState(models.TextChoices):
        PENDING = 'pending'
        QUEUED = 'queued'
        SENT = 'sent'

    ACTION_KINDS = (Kind.ALERT, Kind.REMINDER, Kind.RETRY)

    kind = models.CharField(max_length=9, choices=Kind.choices)
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    responsible = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    responsible_name = models.CharField(max_length=301)
    maintenance_key = models.UUIDField(null=True)
    last_maintenance = models.DateField(null=True)
    due_date = models.DateField(null=True)
    day = models.DateField()
    plan_name = models.CharField(max_length=255)
    button_name = models.CharField(max_length=255)
    category_name = models.CharField(max_length=80, blank=True)
    reply_to = models.ForeignKey('self', null=True, on_delete=models.SET_NULL, related_name='+')
    review_report = models.ForeignKey('MaintenanceSubmission', null=True, on_delete=models.CASCADE, related_name='+')
    sender_name = models.CharField(max_length=301, blank=True)
    message = models.TextField(blank=True)
    title = models.CharField(max_length=120, blank=True)
    description = models.TextField(blank=True)
    button_deleted = models.BooleanField(default=False)
    push_state = models.CharField(max_length=7, choices=PushState.choices, default=PushState.PENDING)

    @property
    def can_dismiss(self):
        return self.button_deleted or self.kind not in (*self.ACTION_KINDS, self.Kind.REVIEW)

    @classmethod
    def inbox_visibility(cls):
        return models.Q(button_deleted=True) | ~models.Q(kind__in=cls.ACTION_KINDS) | models.Q(
            responsible_id=models.F('recipient_id'),
        )

    class Meta:
        ordering = ['-day', '-id']
        indexes = [
            models.Index(fields=['recipient', '-day', '-id'], name='maintenance_inbox_order'),
            models.Index(fields=['push_state', 'day', 'id'], name='notification_push_order'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['maintenance_key', 'recipient'],
                condition=models.Q(kind='alert'), name='unique_overdue_maintenance_alert',
            ),
            models.UniqueConstraint(
                fields=['maintenance_key', 'last_maintenance', 'due_date', 'recipient'],
                condition=models.Q(kind='reminder'), name='unique_maintenance_reminder',
            ),
            models.UniqueConstraint(
                fields=['maintenance_key', 'recipient'],
                condition=models.Q(kind='retry'), name='unique_maintenance_retry',
            ),
            models.UniqueConstraint(
                fields=['maintenance_key', 'recipient'],
                condition=models.Q(kind='confirmed'), name='unique_maintenance_confirmation',
            ),
            models.UniqueConstraint(
                fields=['review_report', 'recipient'],
                condition=models.Q(kind='review'), name='unique_maintenance_review_notice',
            ),
        ]


class DevicePushSubscription(models.Model):
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    session_key = models.CharField(max_length=40, unique=True)
    endpoint = models.URLField(max_length=2048, unique=True)
    public_key = models.CharField(max_length=100)
    auth_secret = models.CharField(max_length=40)


class DevicePushDelivery(models.Model):
    notification = models.ForeignKey(MaintenanceNotification, on_delete=models.CASCADE, related_name='+')
    subscription = models.ForeignKey(DevicePushSubscription, on_delete=models.CASCADE, related_name='+')
    available_at = models.DateTimeField(db_index=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['notification', 'subscription'], name='unique_notification_push_delivery',
        )]


class MaintenanceInboxState(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, primary_key=True, on_delete=models.CASCADE)
    revision = models.UUIDField(default=uuid4)


class MaintenanceSubmission(models.Model):
    class Status(models.TextChoices):
        UPLOADING = 'uploading', 'Subiendo evidencia'
        PENDING = 'pending', 'Pendiente de revisión'
        CLEANUP = 'cleanup', 'Eliminando evidencia'

    maintenance_key = models.UUIDField()
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=9, choices=Status.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    last_maintenance = models.DateField()
    due_date = models.DateField()
    plan_name = models.CharField(max_length=255, blank=True)
    button_name = models.CharField(max_length=255, blank=True)
    responsible_name = models.CharField(max_length=301, blank=True)
    title = models.CharField(max_length=120, blank=True)
    description = models.TextField(blank=True)
    drive_images = models.JSONField(default=list, blank=True)

    class Meta:
        indexes = [models.Index(fields=['status', 'created_at'], name='maintenance_review_queue')]
        constraints = [
            models.UniqueConstraint(
                fields=['maintenance_key'], condition=models.Q(status__in=['uploading', 'pending']),
                name='unique_open_maintenance_report',
            ),
        ]


class LatestMaintenance(models.Model):
    maintenance_key = models.UUIDField(primary_key=True)
    title = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    performed_on = models.DateField()


class SavedProject(models.Model):
    cloud_receipt = models.JSONField(default=dict, blank=True)
    """A portable snapshot captured by Guardar plano, for either source format."""
    cad_plan = models.OneToOneField(Plan, null=True, blank=True, on_delete=models.CASCADE, related_name='saved_project')
    pdf_plan = models.OneToOneField(PdfPlan, null=True, blank=True, on_delete=models.CASCADE, related_name='saved_project')
    name = models.CharField(max_length=255)
    content = FileContentField(extension='planproj')
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


class PlanEditGrant(models.Model):
    cloud_key = models.CharField(max_length=80)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='plan_edit_grants')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['cloud_key', 'user'], name='unique_plan_edit_grant')]
