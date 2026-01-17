from rest_framework import generics, status, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework_simplejwt.tokens import RefreshToken
from django.db.models import Q

from .models import User, Category, Product, Offer
from .serializers import (
    UserSerializer,
    CategorySerializer,
    ProductSerializer,
    ProductCreateSerializer,
    OfferCreateSerializer,
    OfferPublicSerializer,
    LoginSerializer,
    UserRegistrationSerializer,
    OfferSerializer,
    OfferTemplateSerializer
)

# ------------------ PERMISSIONS ------------------

class IsAdminUser(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.user_type == 'admin'


# ===================== AUTH =====================

@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def admin_login(request):
    serializer = LoginSerializer(data=request.data)
    if serializer.is_valid():
        user = serializer.validated_data['user']
        if user.user_type != 'admin':
            return Response({'error': 'Admin access only'}, status=403)

        refresh = RefreshToken.for_user(user)
        return Response({
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': UserSerializer(user).data
        })
    return Response(serializer.errors, status=400)


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def user_login(request):
    serializer = LoginSerializer(data=request.data)
    if serializer.is_valid():
        user = serializer.validated_data['user']
        if user.user_type != 'user':
            return Response({'error': 'User login only'}, status=403)

        refresh = RefreshToken.for_user(user)
        return Response({
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': UserSerializer(user).data
        })
    return Response(serializer.errors, status=400)


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def register_user(request):
    serializer = UserRegistrationSerializer(data=request.data)
    if serializer.is_valid():
        user = serializer.save(user_type='user')
        refresh = RefreshToken.for_user(user)
        return Response({
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': UserSerializer(user).data
        }, status=201)
    return Response(serializer.errors, status=400)


# ===================== CATEGORY =====================

class CategoryListCreateView(generics.ListCreateAPIView):
    serializer_class = CategorySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Category.objects.all().order_by('-id')

    def perform_create(self, serializer):
        if self.request.user.user_type != 'admin':
            raise permissions.PermissionDenied("Admin only")
        serializer.save()


# ===================== PRODUCTS =====================

class ProductListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get_serializer_class(self):
        return ProductCreateSerializer if self.request.method == 'POST' else ProductSerializer

    def get_queryset(self):
        return Product.objects.filter(user=self.request.user).order_by('-created_at')


class ProductDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Product.objects.filter(user=self.request.user)


# ===================== ✅ LEGACY OFFER (REQUIRED FIX) =====================

@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def get_offer(request, product_id):
    try:
        product = Product.objects.get(id=product_id, is_active=True)
        serializer = OfferTemplateSerializer(product)
        return Response(serializer.data)
    except Product.DoesNotExist:
        return Response(
            {'error': 'Offer not found or has expired.'},
            status=status.HTTP_404_NOT_FOUND
        )


# ===================== NEW OFFER SYSTEM =====================

class OfferCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = OfferCreateSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            offer = serializer.save()
            out = OfferPublicSerializer(offer, context={'request': request})
            return Response(out.data, status=201)
        return Response(serializer.errors, status=400)


@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def public_offer_detail(request, offer_id):
    try:
        offer = Offer.objects.get(id=offer_id, is_public=True)
        serializer = OfferPublicSerializer(offer)
        return Response(serializer.data)
    except Offer.DoesNotExist:
        return Response({'error': 'Offer not found'}, status=404)


# ===================== PROFILE =====================

@api_view(['GET', 'PUT'])
def user_profile(request):
    user = request.user
    if request.method == 'GET':
        return Response(UserSerializer(user).data)

    serializer = UserSerializer(user, data=request.data, partial=True)
    if serializer.is_valid():
        serializer.save()
        return Response(serializer.data)
    return Response(serializer.errors, status=400)


# ===================== ✅ ✅ ADMIN USER MANAGEMENT (FINAL FIX) =====================

class AdminListView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminUser]

    # ✅ GET USERS
    def get(self, request):
        try:
            search_term = request.GET.get('search', '')
            queryset = User.objects.filter(user_type='user')

            if search_term:
                queryset = queryset.filter(
                    Q(username__icontains=search_term) |
                    Q(email__icontains=search_term) |
                    Q(shop_name__icontains=search_term) |
                    Q(location__icontains=search_term)
                )

            queryset = queryset.order_by('-date_joined')
            return Response(UserSerializer(queryset, many=True).data)

        except Exception as e:
            return Response({'error': str(e)}, status=500)

    # ✅ ✅ POST USER (CUSTOMER NAME FIXED ✅)
    def post(self, request):
        try:
            data = request.data.copy()
            data["user_type"] = "user"

            # ✅ FIX: customer_name → business_name
            data["business_name"] = data.get("customer_name", "")

            serializer = UserSerializer(data=data)

            if serializer.is_valid():
                user = serializer.save()
                user.set_password(data.get("password"))  # ✅ password hashing
                user.save()

                return Response(UserSerializer(user).data, status=201)

            return Response(serializer.errors, status=400)

        except Exception as e:
            return Response({'error': str(e)}, status=500)


# ===================== ADMIN STATS =====================

class AdminStatsView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminUser]

    def get(self, request):
        return Response({
            'total_admins': User.objects.filter(user_type='admin').count(),
            'total_users': User.objects.filter(user_type='user').count(),
            'active_users': User.objects.filter(user_type='user', status='Active').count(),
            'disabled_users': User.objects.filter(user_type='user', status='Disable').count(),
        })
    # ===================== CATEGORY IMAGE UPDATE =====================

@api_view(['PATCH', 'PUT'])
@permission_classes([permissions.IsAuthenticated])
def update_category_image(request, category_id):
    """Update category image"""
    try:
        category = Category.objects.get(id=category_id)
        
        if 'image' in request.FILES:
            category.image = request.FILES['image']
            category.save()
            return Response(CategorySerializer(category).data)
        
        return Response({'error': 'No image provided'}, status=400)
    except Category.DoesNotExist:
        return Response({'error': 'Category not found'}, status=404)


# ===================== PRODUCTS BY CATEGORY =====================

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def products_by_category(request, category_name):
    """Get all products in a specific category"""
    products = Product.objects.filter(
        user=request.user,
        category=category_name,
        is_active=True
    ).order_by('-created_at')
    return Response(ProductSerializer(products, many=True).data)


# ===================== TEMPLATES =====================

class TemplateListView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request):
        templates = [
            {'id': 1, 'name': 'Template 1', 'type': 'template1'},
            {'id': 2, 'name': 'Template 2', 'type': 'template2'},
            {'id': 3, 'name': 'Template 3', 'type': 'template3'},
            {'id': 4, 'name': 'Template 4', 'type': 'template4'},
        ]
        return Response(templates)
    class ProductListCreateView(generics.ListCreateAPIView):
     permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get_serializer_class(self):
        return ProductCreateSerializer if self.request.method == 'POST' else ProductSerializer

    def get_queryset(self):
        return Product.objects.filter(user=self.request.user).order_by('-created_at')
    
    def perform_create(self, serializer):
        # ✅ Automatically assign current user
        serializer.save(user=self.request.user)
        class CategoryListCreateView(generics.ListCreateAPIView):
         serializer_class = CategorySerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        return Category.objects.all().order_by('-id')

    def perform_create(self, serializer):
        serializer.save()
