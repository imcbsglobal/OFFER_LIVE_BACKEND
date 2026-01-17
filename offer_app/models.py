import uuid
import qrcode
from io import BytesIO
from django.core.files import File
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings

# ---------- User (unchanged) ----------
class User(AbstractUser):
    USER_TYPE_CHOICES = (
        ('admin', 'Admin'),
        ('user', 'Business Owner'),
    )
    STATUS_CHOICES = [
        ('Active', 'Active'),
        ('Disable', 'Disable'),
    ]
    user_type = models.CharField(max_length=20, choices=USER_TYPE_CHOICES, default='user')
    phone_number = models.CharField(max_length=15, blank=True, null=True, default='')
    business_name = models.CharField(max_length=255, blank=True, null=True, default='')
    shop_name = models.CharField(max_length=255, blank=True, null=True, default='')
    location = models.CharField(max_length=255, blank=True, null=True, default='')
    shop_logo = models.ImageField(upload_to='shop_logos/', blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Active')
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    no_days = models.IntegerField(default=0)
    validity_start = models.DateField(blank=True, null=True)
    validity_end = models.DateField(blank=True, null=True)
    created_date = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.is_superuser:
            self.user_type = 'admin'
            self.is_staff = True
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.username} ({self.user_type})"

# ---------- Category (unchanged) ----------
class Category(models.Model):
    name = models.CharField(max_length=200, unique=True)
    image = models.ImageField(upload_to="categories/", null=True, blank=True)

    def __str__(self):
        return self.name

# ---------- Product (mostly same) ----------
class Product(models.Model):
    TEMPLATE_CHOICES = [
        ('template1', 'Template 1'),
        ('template2', 'Template 2'),
        ('template3', 'Template 3'),
        ('template4', 'Template 4'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey('offer_app.User', on_delete=models.CASCADE)
    product_name = models.CharField(max_length=255)
    category = models.CharField(max_length=255, blank=True, null=True, default='')
    brand = models.CharField(max_length=255, blank=True, null=True, default='')
    original_price = models.DecimalField(max_digits=10, decimal_places=2)
    offer_price = models.DecimalField(max_digits=10, decimal_places=2)
    discount_percentage = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    image = models.ImageField(upload_to='product_images/', blank=True, null=True)
    qr_code = models.ImageField(upload_to='qr_codes/', blank=True, null=True)
    offer_link = models.CharField(max_length=500, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    valid_until = models.DateTimeField(blank=True, null=True)
    template_type = models.CharField(max_length=20, choices=TEMPLATE_CHOICES, default='template1')
    is_active = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        # Calculate discount percentage
        if self.original_price and self.offer_price:
            try:
                discount = ((self.original_price - self.offer_price) / self.original_price) * 100
                self.discount_percentage = round(discount, 2)
            except Exception:
                self.discount_percentage = 0

        # Keep product-level offer_link behavior (optional). It won't replace the new Offer-level link.
        if not self.offer_link:
            self.offer_link = f"{getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000')}/api/product-offer/{self.id}/"

        super().save(*args, **kwargs)

        # Optional: keep generating per-product QR if original code expects it
        if not self.qr_code:
            try:
                self.generate_qr_code()
            except Exception:
                pass

    def generate_qr_code(self):
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(self.offer_link)
        qr.make(fit=True)

        qr_img = qr.make_image(fill_color="black", back_color="white")
        buffer = BytesIO()
        qr_img.save(buffer, format='PNG')
        buffer.seek(0)
        self.qr_code.save(f'qr_code_{self.id}.png', File(buffer), save=False)
        super().save(update_fields=['qr_code'])

    def __str__(self):
        return self.product_name

# ---------- Offer (NEW) ----------
class Offer(models.Model):
    """
    New Offer model which can contain many products and has its own
    unique public link + QR image.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    products = models.ManyToManyField(Product, related_name='offers')
    template_type = models.CharField(max_length=50, default='template1')
    title = models.CharField(max_length=255, blank=True, default='')  # optional friendly title
    offer_link = models.CharField(max_length=500, blank=True)
    qr_code = models.ImageField(upload_to='offer_qr/', blank=True, null=True)
    is_public = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        created = self._state.adding
        super().save(*args, **kwargs)

        # assign offer_link if not present (short stable link)
        if not self.offer_link:
            site = getattr(settings, 'SITE_URL', 'http://127.0.0.1:3000')  # frontend route
            # public frontend route: /offer/<id>
            self.offer_link = f"{site}/offer/{self.id}"
            super().save(update_fields=['offer_link'])

        # generate qr if not present
        if not self.qr_code:
            self.generate_qr()

    def generate_qr(self):
        try:
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=8,
                border=4
            )
            qr.add_data(self.offer_link)
            qr.make(fit=True)
            qr_img = qr.make_image(fill_color="black", back_color="white")
            buffer = BytesIO()
            qr_img.save(buffer, format='PNG')
            buffer.seek(0)
            self.qr_code.save(f'offer_qr_{self.id}.png', File(buffer), save=False)
            super().save(update_fields=['qr_code'])
        except Exception as e:
            # don't fail whole request due to QR generation
            print("QR generation error:", e)

    def __str__(self):
        return f"Offer {self.id} - {self.title or self.template_type}"
