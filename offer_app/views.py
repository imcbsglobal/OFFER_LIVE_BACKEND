# views.py
from rest_framework import generics, status, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework_simplejwt.tokens import RefreshToken
from django.db.models import Q
from django.utils import timezone
import secrets
import requests as http_requests

from .models import User, Category, Product, Offer, OfferMaster, OfferMasterMedia, BranchMaster
from .serializers import (
    UserSerializer,
    UserPublicSerializer,
    CategorySerializer,
    ProductSerializer,
    ProductCreateSerializer,
    OfferCreateSerializer,
    OfferPublicSerializer,
    LoginSerializer,
    UserRegistrationSerializer,
    OfferSerializer,
    OfferTemplateSerializer,
    OfferMasterSerializer,
    OfferMasterCreateUpdateSerializer,
    OfferMasterMediaSerializer,
    BranchMasterSerializer,
    BranchMasterCreateUpdateSerializer,
    UserSimpleSerializer,
    BranchWithOffersSerializer,
)

# ------------------ AUTO-EXPIRE OFFERS ------------------

def auto_expire_offers():
    """
    Bulk-set status='inactive' for any OfferMaster whose valid_to date
    has passed today. Called at the top of every view that reads offers,
    so no cron job or Celery is needed — expiry is applied on the next
    API hit after the date passes.
    """
    today = timezone.localdate()
    expired_count = OfferMaster.objects.filter(
        valid_to__lt=today,
        status='active'          # only touch active ones, leave scheduled/inactive alone
    ).update(status='inactive')
    if expired_count:
        print(f"[auto_expire] Marked {expired_count} offer(s) as inactive (valid_to passed).")

# ------------------ PERMISSIONS ------------------

class IsAdminUser(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.user_type == "admin"


def _block_if_disabled(user):
    # model uses: 'Active' / 'Disable'
    if getattr(user, "status", "Active") == "Disable":
        return True
    return False


# ===================== AUTH =====================

@api_view(["POST"])
@permission_classes([permissions.AllowAny])
def admin_login(request):
    serializer = LoginSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=400)

    user = serializer.validated_data["user"]

    if _block_if_disabled(user):
        return Response({"error": "Account is disabled"}, status=403)

    if user.user_type != "admin":
        return Response({"error": "Admin access only"}, status=403)

    refresh = RefreshToken.for_user(user)
    return Response({
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "user": UserPublicSerializer(user).data
    })


# ─── Debtors API ──────────────────────────────────────────────────────────────

DEBTORS_API_URL = "https://vsaverapi.imcbs.com/api/debtors/"

def _safe_paginate(url, timeout=10):
    """
    Helper: fetches a URL and returns (results_list, next_url).
    Handles both plain list responses and paginated {"results":[], "next":...} responses.
    """
    resp = http_requests.get(url, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        return data, None
    return data.get("results", []), data.get("next")


def _find_debtor_by_phone(phone_number):
    url = DEBTORS_API_URL
    while url:
        try:
            results, next_url = _safe_paginate(url, timeout=10)
        except Exception as e:
            raise Exception(f"Failed to reach debtors API: {e}")

        for debtor in results:
            phone2 = (debtor.get("phone2") or "").strip()
            if phone2 and phone2[-10:] == phone_number:
                return debtor

        url = next_url

    return None


@api_view(["POST"])
@permission_classes([permissions.AllowAny])
def user_login(request):
    phone_number = request.data.get("phone_number", "").strip().replace(" ", "")

    if not phone_number or not phone_number.lstrip("+").isdigit() or len(phone_number.lstrip("+")) < 10:
        return Response({"error": "Please provide a valid 10-digit mobile number."}, status=400)

    phone_number = phone_number[-10:]

    try:
        debtor = _find_debtor_by_phone(phone_number)
    except Exception as e:
        return Response({"error": str(e)}, status=503)

    if not debtor:
        return Response(
            {"error": "Mobile number not registered. Please contact your admin."},
            status=404
        )

    debtor_code = (debtor.get("code") or "").strip()
    debtor_name = (debtor.get("name") or "").strip()

    user, created = User.objects.get_or_create(
        phone_number=phone_number,
        defaults={
            "username": f"debtor_{debtor_code}_{phone_number}",
            "user_type": "user",
            "status": "Active",
            "business_name": debtor_name,
            "location": debtor.get("place") or "",
        }
    )

    if _block_if_disabled(user):
        return Response({"error": "Your account is disabled. Please contact admin."}, status=403)

    refresh = RefreshToken.for_user(user)
    return Response({
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "user": {
            **UserPublicSerializer(user).data,
            "debtor_code": debtor_code,
            "debtor_name": debtor_name,
            "place": debtor.get("place") or "",
            "balance": debtor.get("exregnodate") or "0",
        }
    })


@api_view(["POST"])
@permission_classes([permissions.AllowAny])
def register_user(request):
    serializer = UserRegistrationSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=400)

    user = serializer.save(user_type="user")
    refresh = RefreshToken.for_user(user)
    return Response({
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "user": UserPublicSerializer(user).data
    }, status=201)


# ===================== CATEGORY =====================

class CategoryListCreateView(generics.ListCreateAPIView):
    serializer_class = CategorySerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        return Category.objects.all().order_by("-id")

    def perform_create(self, serializer):
        serializer.save()


class CategoryDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = CategorySerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Category.objects.all()
    parser_classes = [MultiPartParser, FormParser]

    def destroy(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            instance.delete()
            return Response({"message": "Category deleted successfully"}, status=status.HTTP_200_OK)
        except Category.DoesNotExist:
            return Response({"error": "Category not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["PATCH", "PUT"])
@permission_classes([permissions.IsAuthenticated])
def update_category_image(request, category_id):
    try:
        category = Category.objects.get(id=category_id)
        if "image" in request.FILES:
            category.image = request.FILES["image"]
            category.save()
            return Response(CategorySerializer(category).data)
        return Response({"error": "No image provided"}, status=400)
    except Category.DoesNotExist:
        return Response({"error": "Category not found"}, status=404)


# ===================== PRODUCTS =====================

class ProductListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get_serializer_class(self):
        return ProductCreateSerializer if self.request.method == "POST" else ProductSerializer

    def get_queryset(self):
        return Product.objects.filter(user=self.request.user).order_by("-created_at")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class ProductDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        return Product.objects.filter(user=self.request.user)

    def update(self, request, *args, **kwargs):
        """
        Custom update method that excludes category and valid_until from being updated
        """
        try:
            instance = self.get_object()
            
            # Create a mutable copy of request data
            data = request.data.copy()
            
            # Remove category and valid_until if present (these cannot be edited)
            data.pop('category', None)
            data.pop('valid_until', None)
            
            # Use ProductCreateSerializer for the update (handles file uploads properly)
            serializer = ProductCreateSerializer(instance, data=data, partial=True)
            
            if serializer.is_valid():
                serializer.save()
                # Return full product data
                return Response(ProductSerializer(instance).data)
            
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            
        except Product.DoesNotExist:
            return Response(
                {"error": "Product not found"}, 
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            print(f"Error updating product: {str(e)}")
            import traceback
            traceback.print_exc()
            return Response(
                {"error": f"Failed to update product: {str(e)}"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def destroy(self, request, *args, **kwargs):
        """
        Custom destroy method to handle ManyToMany relationships before deletion
        """
        try:
            instance = self.get_object()
            
            # Try to remove product from all offers if the relationship exists
            try:
                if hasattr(instance, 'offers'):
                    instance.offers.clear()
            except Exception as clear_error:
                # If offers relationship doesn't exist or fails, log but continue
                print(f"Warning: Could not clear offers relationship: {str(clear_error)}")
                # This is expected if migrations haven't been run yet
            
            # Now delete the product
            instance.delete()
            
            return Response(
                {"message": "Product deleted successfully"}, 
                status=status.HTTP_200_OK
            )
        except Product.DoesNotExist:
            return Response(
                {"error": "Product not found"}, 
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            # Log the actual error for debugging
            print(f"Error deleting product: {str(e)}")
            import traceback
            traceback.print_exc()
            return Response(
                {"error": f"Failed to delete product: {str(e)}"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# ===================== PRODUCTS BY CATEGORY =====================

@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def products_by_category(request, category_name):
    products = Product.objects.filter(
        user=request.user,
        category=category_name,
        is_active=True
    ).order_by("-created_at")
    return Response(ProductSerializer(products, many=True).data)


# ===================== LEGACY OFFER (per product) =====================

@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def get_offer(request, product_id):
    try:
        product = Product.objects.get(id=product_id, is_active=True)
        serializer = OfferTemplateSerializer(product)
        return Response(serializer.data)
    except Product.DoesNotExist:
        return Response({"error": "Offer not found or has expired."}, status=status.HTTP_404_NOT_FOUND)


# ===================== NEW OFFER SYSTEM =====================

class OfferCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = OfferCreateSerializer(data=request.data, context={"request": request})
        if serializer.is_valid():
            offer = serializer.save()
            out = OfferPublicSerializer(offer, context={"request": request})
            return Response(out.data, status=201)
        return Response(serializer.errors, status=400)


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def public_offer_detail(request, offer_id):
    try:
        offer = Offer.objects.get(id=offer_id, is_public=True)
        serializer = OfferPublicSerializer(offer)
        return Response(serializer.data)
    except Offer.DoesNotExist:
        return Response({"error": "Offer not found"}, status=404)


# ===================== OFFER MASTER (UPDATED WITH BRANCH ASSIGNMENT) =====================

class OfferMasterListCreateView(generics.ListCreateAPIView):
    """
    GET: List all offer masters (admins see all, users see all)
    POST: Create a new offer master (ADMIN ONLY) with branch assignment
    """
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        """
        Everyone sees ALL offers (created by admin).
        Expired offers are auto-marked inactive before querying.
        """
        auto_expire_offers()
        return OfferMaster.objects.all().prefetch_related('branches', 'media_files').order_by('-created_at')

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return OfferMasterCreateUpdateSerializer
        return OfferMasterSerializer

    def create(self, request, *args, **kwargs):
        """
        Custom create to handle multiple file uploads and branch assignment
        ADMIN ONLY - Regular users cannot create offers
        """
        # CHECK: Only admin can create
        if request.user.user_type != 'admin':
            return Response(
                {"error": "Only administrators can create offers"}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        try:
            # Get files using getlist - Django sends multiple files with same key
            files = request.FILES.getlist('files')
            
            # Get branch IDs - Django sends multiple values with same key
            branch_ids = request.data.getlist('branch_ids')
            
            # Get other form data
            data = {
                'title': request.data.get('title'),
                'description': request.data.get('description', ''),
                'valid_from': request.data.get('valid_from'),
                'valid_to': request.data.get('valid_to'),
                'status': request.data.get('status', 'active'),
            }
            
            # Add files and branch_ids to data
            if files:
                data['files'] = files
            if branch_ids:
                data['branch_ids'] = branch_ids
            
            serializer = self.get_serializer(data=data)
            serializer.is_valid(raise_exception=True)
            
            # Save with user (admin user)
            offer_master = serializer.save(user=request.user)
            
            # Return the created object with media files and branches
            response_serializer = OfferMasterSerializer(
                offer_master, 
                context={'request': request}
            )
            return Response(response_serializer.data, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            print(f"Error creating offer master: {str(e)}")
            import traceback
            traceback.print_exc()
            return Response(
                {"error": f"Failed to create offer: {str(e)}"}, 
                status=status.HTTP_400_BAD_REQUEST
            )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['request'] = self.request
        return context


class OfferMasterDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET: Retrieve a specific offer master (everyone can view)
    PUT/PATCH: Update an offer master (ADMIN ONLY) including branch assignment
    DELETE: Delete an offer master (ADMIN ONLY)
    """
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        """Everyone can view all offers"""
        return OfferMaster.objects.all().prefetch_related('branches', 'media_files')

    def get_serializer_class(self):
        if self.request.method in ['PUT', 'PATCH']:
            return OfferMasterCreateUpdateSerializer
        return OfferMasterSerializer

    def update(self, request, *args, **kwargs):
        """
        Custom update to handle multiple file uploads and branch assignment
        ADMIN ONLY - Regular users cannot edit offers
        """
        # CHECK: Only admin can update
        if request.user.user_type != 'admin':
            return Response(
                {"error": "Only administrators can update offers"}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        try:
            instance = self.get_object()
            
            # Get files using getlist
            files = request.FILES.getlist('files')
            
            # Get branch IDs
            branch_ids = request.data.getlist('branch_ids')
            
            # Get other form data
            data = {
                'title': request.data.get('title', instance.title),
                'description': request.data.get('description', instance.description),
                'valid_from': request.data.get('valid_from', instance.valid_from),
                'valid_to': request.data.get('valid_to', instance.valid_to),
                'status': request.data.get('status', instance.status),
            }
            
            # Add files if provided
            if files:
                data['files'] = files
                
            # Add branch_ids if provided (even if empty list, to allow clearing)
            if branch_ids is not None:
                data['branch_ids'] = branch_ids
            
            serializer = self.get_serializer(instance, data=data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            
            # Return updated object with media files and branches
            response_serializer = OfferMasterSerializer(
                instance, 
                context={'request': request}
            )
            return Response(response_serializer.data)
            
        except OfferMaster.DoesNotExist:
            return Response(
                {"error": "Offer not found"}, 
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            print(f"Error updating offer master: {str(e)}")
            import traceback
            traceback.print_exc()
            return Response(
                {"error": f"Failed to update offer: {str(e)}"}, 
                status=status.HTTP_400_BAD_REQUEST
            )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['request'] = self.request
        return context

    def destroy(self, request, *args, **kwargs):
        """
        Delete an offer master and all its media files
        ADMIN ONLY - Regular users cannot delete offers
        """
        # CHECK: Only admin can delete
        if request.user.user_type != 'admin':
            return Response(
                {"error": "Only administrators can delete offers"}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        try:
            instance = self.get_object()
            # Media files will be automatically deleted via CASCADE
            # Branch relationships will be automatically cleared
            instance.delete()
            return Response(
                {"message": "Offer deleted successfully"}, 
                status=status.HTTP_200_OK
            )
        except OfferMaster.DoesNotExist:
            return Response(
                {"error": "Offer not found"}, 
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            print(f"Error deleting offer: {str(e)}")
            import traceback
            traceback.print_exc()
            return Response(
                {"error": f"Failed to delete offer: {str(e)}"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# ===================== OFFER MASTER MEDIA MANAGEMENT =====================

@api_view(['DELETE'])
@permission_classes([permissions.IsAuthenticated])
def delete_offer_master_media(request, pk, media_id):
    """
    Delete a specific media file from an offer master
    ADMIN ONLY - Only admin can delete media files
    """
    # CHECK: Only admin can delete media
    if request.user.user_type != 'admin':
        return Response(
            {"error": "Only administrators can delete media files"}, 
            status=status.HTTP_403_FORBIDDEN
        )
    
    try:
        media = OfferMasterMedia.objects.get(id=media_id, offer_master_id=pk)
        
        # Delete the media file
        media.delete()
        return Response(
            {"message": "Media file deleted successfully"}, 
            status=status.HTTP_200_OK
        )
    except OfferMasterMedia.DoesNotExist:
        return Response(
            {"error": "Media file not found"}, 
            status=status.HTTP_404_NOT_FOUND
        )
    except Exception as e:
        print(f"Error deleting media: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response(
            {"error": f"Failed to delete media file: {str(e)}"}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def offer_master_stats(request):
    """
    Get statistics about offer masters
    - Admin: stats about offers they created
    - User: stats about all offers (read-only)
    """
    user = request.user
    
    if user.user_type == 'admin':
        # Admin sees their own created offers
        total = OfferMaster.objects.filter(user=user).count()
        active = OfferMaster.objects.filter(user=user, status='active').count()
        inactive = OfferMaster.objects.filter(user=user, status='inactive').count()
        scheduled = OfferMaster.objects.filter(user=user, status='scheduled').count()
    else:
        # Regular users see ALL offers (read-only)
        total = OfferMaster.objects.all().count()
        active = OfferMaster.objects.filter(status='active').count()
        inactive = OfferMaster.objects.filter(status='inactive').count()
        scheduled = OfferMaster.objects.filter(status='scheduled').count()

    return Response({
        'total': total,
        'active': active,
        'inactive': inactive,
        'scheduled': scheduled
    })


# ===================== BRANCH-SPECIFIC VIEWS =====================

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def get_user_branches(request):
    """
    Get all branches belonging to the logged-in user
    Returns branches with basic info (no offers)
    """
    user = request.user
    
    try:
        branches = BranchMaster.objects.filter(
            user=user, 
            status='active'
        ).order_by('branch_name')
        
        serializer = BranchMasterSerializer(
            branches, 
            many=True, 
            context={'request': request}
        )
        
        return Response({
            'success': True,
            'count': branches.count(),
            'branches': serializer.data
        })
    except Exception as e:
        print(f"Error fetching user branches: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response(
            {'error': f'Failed to fetch branches: {str(e)}'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def get_branch_offers(request, branch_id):
    """
    Get all offers assigned to a specific branch
    Only if the branch belongs to the logged-in user
    Returns branch info + its offers
    """
    # Auto-expire any offers whose valid_to has passed
    auto_expire_offers()

    user = request.user
    
    try:
        # Verify the branch belongs to this user
        branch = BranchMaster.objects.prefetch_related(
            'offers', 
            'offers__media_files'
        ).get(id=branch_id, user=user)
        
    except BranchMaster.DoesNotExist:
        return Response({
            'success': False,
            'error': 'Branch not found or you do not have access'
        }, status=status.HTTP_404_NOT_FOUND)
    
    try:
        # Get active offers for this branch
        offers = branch.offers.filter(status='active').order_by('-created_at')
        
        # Serialize branch with offers
        branch_serializer = BranchMasterSerializer(branch, context={'request': request})
        offers_serializer = OfferMasterSerializer(offers, many=True, context={'request': request})
        
        return Response({
            'success': True,
            'branch': branch_serializer.data,
            'offers_count': offers.count(),
            'offers': offers_serializer.data
        })
    except Exception as e:
        print(f"Error fetching branch offers: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response(
            {'error': f'Failed to fetch offers: {str(e)}'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def get_all_branches_dropdown(request):
    """
    Get all branches for admin dropdown (when assigning offers)
    Admin sees all branches, regular users see only their branches
    Returns simplified data for dropdown selection
    """
    user = request.user
    
    try:
        if user.user_type == 'admin':
            # Admin sees all active branches from all users
            branches = BranchMaster.objects.filter(
                status='active'
            ).select_related('user').order_by('user__shop_name', 'branch_name')
        else:
            # Regular users see only their branches
            branches = BranchMaster.objects.filter(
                user=user, 
                status='active'
            ).order_by('branch_name')
        
        # Simple format for dropdown
        branch_list = [{
            'id': str(branch.id),
            'label': f"{branch.branch_name} ({branch.branch_code}) - {branch.user.shop_name or branch.user.username}",
            'branch_name': branch.branch_name,
            'branch_code': branch.branch_code,
            'shop_name': branch.user.shop_name or branch.user.username,
            'user_id': branch.user.id,
            'location': branch.location
        } for branch in branches]
        
        return Response({
            'success': True,
            'count': len(branch_list),
            'branches': branch_list
        })
    except Exception as e:
        print(f"Error fetching branches dropdown: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response(
            {'error': f'Failed to fetch branches: {str(e)}'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ===================== PUBLIC OFFER DISCOVERY =====================

@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def discover_offers(request):
    """
    PUBLIC ENDPOINT: Discover all active offers
    Anyone can see this - no authentication required
    Supports filtering by location, city, branch_id

    Query Parameters:
        - location: Filter by branch location (case-insensitive search)
        - city: Filter by branch city (case-insensitive search)
        - branch_id: Filter by specific branch ID

    Example:
        GET /api/public/offers/
        GET /api/public/offers/?location=Kozhikode
        GET /api/public/offers/?city=Kochi
        GET /api/public/offers/?branch_id=<uuid>
    """
    try:
        # Auto-expire any offers whose valid_to has passed
        auto_expire_offers()

        # Get query parameters for filtering
        location = request.query_params.get('location', None)
        city = request.query_params.get('city', None)
        branch_id = request.query_params.get('branch_id', None)
        
        # Start with all active offers
        offers = OfferMaster.objects.filter(
            status='active'
        ).prefetch_related('branches', 'branches__user', 'media_files')
        
        # Filter by branch if specified
        if branch_id:
            offers = offers.filter(branches__id=branch_id)
        # Filter by location/city if no branch specified
        elif location:
            offers = offers.filter(branches__location__icontains=location)
        elif city:
            offers = offers.filter(branches__city__icontains=city)
        
        offers = offers.distinct().order_by('-created_at')
        
        # Serialize the offers
        serializer = OfferMasterSerializer(offers, many=True, context={'request': request})
        
        return Response({
            'success': True,
            'count': offers.count(),
            'offers': serializer.data
        })
    except Exception as e:
        print(f"Error discovering offers: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response(
            {'error': f'Failed to discover offers: {str(e)}'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def get_all_active_branches_public(request):
    """
    PUBLIC ENDPOINT: Get all active branches with their offers
    Anyone can see this to discover shops and their offers

    Query Parameters:
        - location: Filter by branch location (case-insensitive search)
        - city: Filter by branch city (case-insensitive search)

    Example:
        GET /api/public/branches/
        GET /api/public/branches/?location=Kozhikode
        GET /api/public/branches/?city=Kochi
    """
    try:
        # Auto-expire any offers whose valid_to has passed
        auto_expire_offers()

        # Get query parameters for filtering
        location = request.query_params.get('location', None)
        city = request.query_params.get('city', None)
        
        # Get all active branches
        branches = BranchMaster.objects.filter(
            status='active'
        ).select_related('user').prefetch_related('offers', 'offers__media_files')
        
        # Apply filters if provided
        if location:
            branches = branches.filter(location__icontains=location)
        if city:
            branches = branches.filter(city__icontains=city)
        
        branches = branches.order_by('user__shop_name', 'branch_name')
        
        # Serialize with offers
        serializer = BranchWithOffersSerializer(branches, many=True, context={'request': request})
        
        return Response({
            'success': True,
            'count': branches.count(),
            'branches': serializer.data
        })
    except Exception as e:
        print(f"Error fetching public branches: {str(e)}")
        import traceback
        traceback.print_exc()
        return Response(
            {'error': f'Failed to fetch branches: {str(e)}'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ===================== TEMPLATES =====================

class TemplateListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        templates = [
            {"id": 1, "name": "Template 1", "type": "template1"},
            {"id": 2, "name": "Template 2", "type": "template2"},
            {"id": 3, "name": "Template 3", "type": "template3"},
            {"id": 4, "name": "Template 4", "type": "template4"},
        ]
        return Response(templates)


# ===================== DASHBOARD STATS =====================

@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def user_dashboard_stats(request):
    """
    Returns real-time counts for the user dashboard:
    - Total categories (global count)
    - Total products for this user
    - Active offers (products with is_active=True for this user)
    - Total offer masters for this user
    """
    user = request.user
    
    # Count all categories (categories are global, not per-user)
    total_categories = Category.objects.count()
    
    # Count all products for this user
    total_products = Product.objects.filter(user=user).count()
    
    # Count active offers (products with is_active=True)
    active_offers = Product.objects.filter(user=user, is_active=True).count()
    
    # Count offer masters
    total_offer_masters = OfferMaster.objects.filter(user=user).count()
    active_offer_masters = OfferMaster.objects.filter(user=user, status='active').count()
    
    return Response({
        "total_categories": total_categories,
        "total_products": total_products,
        "active_offers": active_offers,
        "total_offer_masters": total_offer_masters,
        "active_offer_masters": active_offer_masters,
    })


# ===================== PROFILE =====================

@api_view(["GET", "PUT"])
@permission_classes([permissions.IsAuthenticated])
def user_profile(request):
    user = request.user
    if request.method == "GET":
        return Response(UserPublicSerializer(user).data)

    # For update, keep using full serializer only if you want to allow all fields.
    # Better: create a dedicated "UserUpdateSerializer". Leaving as-is for minimal change.
    serializer = UserSerializer(user, data=request.data, partial=True)
    if serializer.is_valid():
        serializer.save()
        return Response(UserPublicSerializer(user).data)
    return Response(serializer.errors, status=400)


# ===================== ADMIN USER MANAGEMENT =====================

class AdminListView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminUser]

    def get(self, request):
        try:
            search_term = request.GET.get("search", "")
            queryset = User.objects.filter(user_type="user")

            if search_term:
                queryset = queryset.filter(
                    Q(username__icontains=search_term) |
                    Q(email__icontains=search_term) |
                    Q(shop_name__icontains=search_term) |
                    Q(location__icontains=search_term)
                )

            queryset = queryset.order_by("-date_joined")
            return Response(UserPublicSerializer(queryset, many=True).data)
        except Exception as e:
            return Response({"error": str(e)}, status=500)

    def post(self, request):
        try:
            data = request.data.copy()
            data["user_type"] = "user"
            data["business_name"] = data.get("customer_name", "")

            serializer = UserSerializer(data=data)
            if serializer.is_valid():
                user = serializer.save()
                user.set_password(data.get("password"))
                user.save()
                return Response(UserPublicSerializer(user).data, status=201)

            return Response(serializer.errors, status=400)
        except Exception as e:
            return Response({"error": str(e)}, status=500)


class AdminDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminUser]
    serializer_class = UserSerializer

    def get_queryset(self):
        return User.objects.filter(user_type="user")

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.delete()
        return Response({"message": "User deleted successfully"}, status=status.HTTP_200_OK)


# ===================== ADMIN STATS =====================

class AdminStatsView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminUser]

    def get(self, request):
        return Response({
            "total_admins": User.objects.filter(user_type="user").count(),
            "active_admins": User.objects.filter(user_type="user", status="Active").count(),
            "disabled_admins": User.objects.filter(user_type="user", status="Disable").count(),
        })


# ===================== BRANCH MASTER =====================

class BranchMasterListCreateView(APIView):
    """
    GET: List all branches (Admin sees ALL, users see their own)
    POST: Create a new branch
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        try:
            # Admin sees ALL branches from ALL users
            if request.user.is_superuser or request.user.user_type == 'admin':
                branches = BranchMaster.objects.all().select_related('user')
            else:
                # Regular users see only their branches
                branches = BranchMaster.objects.filter(user=request.user)
            
            serializer = BranchMasterSerializer(branches, many=True, context={'request': request})
            return Response(serializer.data, status=status.HTTP_200_OK)
        
        except Exception as e:
            return Response(
                {'error': f'Failed to fetch branches: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def post(self, request):
        try:
            serializer = BranchMasterCreateUpdateSerializer(
                data=request.data, 
                context={'request': request}
            )
            
            if serializer.is_valid():
                # Admin can specify which user the branch belongs to
                if not (request.user.is_superuser or request.user.user_type == 'admin'):
                    serializer.validated_data['user'] = request.user
                
                branch = serializer.save()
                branch.refresh_from_db()  # ensures qr_code is loaded after generation
                response_serializer = BranchMasterSerializer(branch, context={'request': request})
                return Response(response_serializer.data, status=status.HTTP_201_CREATED)
            
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        except Exception as e:
            return Response(
                {'error': f'Failed to create branch: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class BranchMasterDetailView(APIView):
    """
    GET: Retrieve a specific branch
    PATCH: Update a specific branch
    DELETE: Delete a specific branch
    """
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self, pk, user):
        try:
            # Admin can access any branch
            if user.is_superuser or user.user_type == 'admin':
                return BranchMaster.objects.get(pk=pk)
            else:
                return BranchMaster.objects.get(pk=pk, user=user)
        except BranchMaster.DoesNotExist:
            return None

    def get(self, request, pk):
        branch = self.get_object(pk, request.user)
        
        if not branch:
            return Response(
                {'error': 'Branch not found or you do not have permission to view it'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        serializer = BranchMasterSerializer(branch, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, pk):
        branch = self.get_object(pk, request.user)
        
        if not branch:
            return Response(
                {'error': 'Branch not found or you do not have permission to update it'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        try:
            serializer = BranchMasterCreateUpdateSerializer(
                branch, 
                data=request.data, 
                partial=True,
                context={'request': request}
            )
            
            if serializer.is_valid():
                updated_branch = serializer.save()
                updated_branch.refresh_from_db()  # ensures qr_code is loaded after generation
                response_serializer = BranchMasterSerializer(
                    updated_branch, 
                    context={'request': request}
                )
                return Response(response_serializer.data, status=status.HTTP_200_OK)
            
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        except Exception as e:
            return Response(
                {'error': f'Failed to update branch: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def delete(self, request, pk):
        branch = self.get_object(pk, request.user)
        
        if not branch:
            return Response(
                {'error': 'Branch not found or you do not have permission to delete it'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        try:
            branch.delete()
            return Response(
                {'message': 'Branch deleted successfully'},
                status=status.HTTP_204_NO_CONTENT
            )
        except Exception as e:
            return Response(
                {'error': f'Failed to delete branch: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def branch_master_stats(request):
    """
    Get statistics about branches
    """
    try:
        # Admin sees ALL branches stats
        if request.user.is_superuser or request.user.user_type == 'admin':
            branches = BranchMaster.objects.all()
        else:
            branches = BranchMaster.objects.filter(user=request.user)
        
        stats = {
            'total_branches': branches.count(),
            'active_branches': branches.filter(status='active').count(),
            'inactive_branches': branches.filter(status='inactive').count(),
        }
        
        return Response(stats, status=status.HTTP_200_OK)
    
    except Exception as e:
        return Response(
            {'error': f'Failed to fetch branch statistics: {str(e)}'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def get_all_users_for_dropdown(request):
    """
    Get list of all users for dropdown selection (Admin only)
    """
    try:
        # Only admins can access this
        if not (request.user.is_superuser or request.user.user_type == 'admin'):
            return Response(
                {'error': 'Permission denied'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Get all users except admins
        users = User.objects.filter(user_type='user').order_by('username')
        serializer = UserSimpleSerializer(users, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
    
    except Exception as e:
        return Response(
            {'error': f'Failed to fetch users: {str(e)}'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ===================== MISEL SHOP SYNC =====================

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def sync_misel_shops(request):
    """
    Admin only.
    Fetches shops from the Misel API and creates User records for any shop
    that doesn't already exist in the system.
    Uses firm_name as shop_name and generates a username from misel_<id>.
    After syncing, the existing GET /api/users/dropdown/ will automatically
    include these shops in the User/Shop dropdown.
    """
    if not (request.user.is_superuser or request.user.user_type == 'admin'):
        return Response({'error': 'Permission denied'}, status=status.HTTP_403_FORBIDDEN)

    try:
        response = http_requests.get('https://vsaverapi.imcbs.com/api/misel/', timeout=10)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        return Response(
            {'error': f'Failed to fetch Misel API: {str(e)}'},
            status=status.HTTP_502_BAD_GATEWAY
        )

    shops = data.get('results', [])
    created = []
    skipped = []

    for shop in shops:
        firm_name = shop.get('firm_name', '').strip()
        address = shop.get('address1', '').strip()
        misel_id = shop.get('id')

        if not firm_name:
            continue

        # Generate a unique username from misel ID
        base_username = f"misel_{misel_id}"

        if User.objects.filter(username=base_username).exists():
            skipped.append(firm_name)
            continue

        # Create a User record for this Misel shop
        User.objects.create_user(
            username=base_username,
            email=f"{base_username}@misel.sync",
            password=secrets.token_urlsafe(16),
            user_type='user',
            shop_name=firm_name,
            location=address,
            status='Active',
        )
        created.append(firm_name)

    return Response({
        'success': True,
        'created': created,
        'created_count': len(created),
        'skipped': skipped,
        'skipped_count': len(skipped),
        'message': f'{len(created)} shop(s) synced, {len(skipped)} already existed.'
    }, status=status.HTTP_200_OK)


# ===================== PUBLIC BRANCH OFFERS (QR SCAN LANDING) =====================

@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def public_branch_offers(request, branch_id):
    """
    PUBLIC — no auth needed.
    Called when customer scans the branch QR code.
    Returns branch info + all active offers for that branch.
    """
    # Auto-expire any offers whose valid_to has passed
    auto_expire_offers()

    try:
        branch = BranchMaster.objects.prefetch_related(
            'offers',
            'offers__media_files',
            'user',
        ).get(id=branch_id)
    except BranchMaster.DoesNotExist:
        return Response({'error': 'Branch not found.'}, status=status.HTTP_404_NOT_FOUND)

    serializer = BranchWithOffersSerializer(branch, context={'request': request})
    return Response(serializer.data)

# ===================== USER INVOICES (E-Invoice History) =====================

INVOICES_API_URL = "https://vsaverapi.imcbs.com/api/invoices/"


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def user_invoices(request):
    """
    Returns invoices for the logged-in user by matching their debtor_code
    (stored in username as debtor_<code>_<phone>) against customerid in
    the external invoices API.

    Handles both plain list [] and paginated {"results":[], "next":...} responses.

    Query params:
      ?debtor_code=<code>   — override auto-detected code (optional)
      ?limit=<n>            — max invoices to return (default 20, max 50)
    """
    # ── 1. Resolve debtor_code from username ──────────────────────
    debtor_code = request.query_params.get('debtor_code', '').strip()

    if not debtor_code:
        # Username pattern: debtor_<code>_<phone>  e.g. debtor_.,DBH_9656938213
        username = getattr(request.user, 'username', '') or ''
        if username.startswith('debtor_'):
            inner = username[len('debtor_'):]
            # phone is always the last segment after final underscore
            parts = inner.rsplit('_', 1)
            debtor_code = parts[0] if len(parts) == 2 else inner

    if not debtor_code:
        return Response(
            {'error': 'Could not determine customer code for this account.'},
            status=400
        )

    limit = min(int(request.query_params.get('limit', 20)), 50)

    def _collect_invoices(base_url, max_pages=300):
        """
        Paginate through the invoices API (handles both list and dict responses)
        and collect all invoices matching debtor_code.
        """
        collected = []
        url = base_url
        pages = 0

        while url and pages < max_pages:
            try:
                results, next_url = _safe_paginate(url, timeout=15)
            except Exception as e:
                raise Exception(f"Invoice API error: {e}")

            pages += 1
            for inv in results:
                if (inv.get('customerid') or '').strip() == debtor_code:
                    collected.append({
                        'slno':     inv.get('slno'),
                        'invdate':  inv.get('invdate'),
                        'nettotal': inv.get('nettotal'),
                    })

            url = next_url

        return collected

    # ── 2. Try filtered endpoint first (fast path) ───────────────
    try:
        filtered_url = f"{INVOICES_API_URL}?customerid={debtor_code}&page_size=100"
        test_results, _ = _safe_paginate(filtered_url, timeout=15)

        # Verify the API actually filtered (all results must match our code)
        api_filtered = bool(test_results) and all(
            (r.get('customerid') or '').strip() == debtor_code
            for r in test_results
        )

        if api_filtered:
            # API supports filtering — paginate only filtered results (fast)
            collected = _collect_invoices(filtered_url, max_pages=50)
        else:
            # API ignores the filter — fall back to full scan
            raise ValueError("API does not support customerid filter")

    except Exception:
        # ── 3. Full scan fallback ─────────────────────────────────
        try:
            collected = _collect_invoices(INVOICES_API_URL, max_pages=300)
        except Exception as e:
            return Response(
                {'error': f'Failed to fetch invoices: {str(e)}'},
                status=503
            )

    # ── 4. Sort descending by slno and return top N ───────────────
    collected.sort(key=lambda x: x.get('slno') or 0, reverse=True)

    return Response({
        'success':     True,
        'debtor_code': debtor_code,
        'total_found': len(collected),
        'invoices':    collected[:limit],
    })