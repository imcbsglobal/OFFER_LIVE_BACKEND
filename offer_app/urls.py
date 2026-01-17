from django.urls import path
from . import views

urlpatterns = [
    # ---------- AUTH ----------
    path('admin/login/', views.admin_login, name='admin-login'),
    path('user/login/', views.user_login, name='user-login'),
    path('register/', views.register_user, name='register-user'),

    # ---------- CATEGORY ----------
    path('categories/', views.CategoryListCreateView.as_view(), name='category-list'),
    path('categories/<int:category_id>/update-image/', views.update_category_image, name='category-update-image'),

    # ---------- PRODUCTS ----------
    path('products/', views.ProductListCreateView.as_view(), name='product-list'),
    path('products/<uuid:pk>/', views.ProductDetailView.as_view(), name='product-detail'),
    path('products/category/<str:category_name>/', views.products_by_category, name='products-by-category'),

    # ---------- TEMPLATES ----------
    path('templates/', views.TemplateListView.as_view(), name='templates-list'),

    # ---------- NEW OFFER SYSTEM ----------
    path('offers/create/', views.OfferCreateView.as_view(), name='offer-create'),
    path('offers/<uuid:offer_id>/', views.public_offer_detail, name='offer-detail'),

    # ---------- OLD OFFER (per product) ----------
    path('offer/<uuid:product_id>/', views.get_offer, name='legacy-offer'),

    # ---------- PROFILE ----------
    path('profile/', views.user_profile, name='user-profile'),

    # ---------- ADMIN ----------
    path('admins/', views.AdminListView.as_view(), name='admin-list'),
    path('admins/stats/', views.AdminStatsView.as_view(), name='admin-stats'),
]