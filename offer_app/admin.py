from django.contrib import admin
from .models import User, Category, Product, Offer


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ('username', 'email', 'user_type', 'status', 'shop_name')


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name',)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('product_name', 'user', 'original_price', 'offer_price', 'created_at')
    list_filter = ('user', 'category', 'template_type')


@admin.register(Offer)
class OfferAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'template_type', 'created_at', 'is_public')
    filter_horizontal = ('products',)
