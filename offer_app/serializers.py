# /mnt/data/serializers.py
from rest_framework import serializers
from django.contrib.auth import authenticate
from .models import User, Category, Product, Offer


# ---------------- LOGIN SERIALIZER (REQUIRED) ----------------
class LoginSerializer(serializers.Serializer):
    # accept either 'email' or 'username' (email preferred)
    email = serializers.EmailField(required=False, allow_blank=True)
    username = serializers.CharField(required=False, allow_blank=True)
    password = serializers.CharField()

    def validate(self, data):
        email = data.get("email")
        username = data.get("username")
        password = data.get("password")

        if not password:
            raise serializers.ValidationError("Password is required.")

        if not email and not username:
            raise serializers.ValidationError("Provide email or username and password.")

        # If email provided -> lookup user by email, then authenticate using username
        if email:
            try:
                user_obj = User.objects.get(email=email)
            except User.DoesNotExist:
                raise serializers.ValidationError("Invalid email or user not found.")
            user = authenticate(username=user_obj.username, password=password)
            if user is None:
                raise serializers.ValidationError("Incorrect password.")
        else:
            # username login path
            user = authenticate(username=username, password=password)
            if user is None:
                raise serializers.ValidationError("Invalid username or password.")

        # At this point authentication succeeded and we have a user instance
        data["user"] = user
        return data


# ---------------- USER SERIALIZERS ----------------
class UserRegistrationSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('username', 'email', 'password', 'shop_name')
        extra_kwargs = {"password": {"write_only": True}}

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = "__all__"


# ---------------- CATEGORY SERIALIZER ----------------
class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = "__all__"


# ---------------- PRODUCT SERIALIZERS ----------------
class ProductCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = (
            'product_name',
            'brand',
            'category',
            'original_price',
            'offer_price',
            'valid_until',
            'template_type',
            'image'
        )


class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = "__all__"


# ------------- OFFER SERIALIZERS ----------------
class OfferSerializer(serializers.ModelSerializer):
    class Meta:
        model = Offer
        fields = "__all__"


# Template serializer for old product-based offer view
class OfferTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = "__all__"


# ------------- NEW MULTI-PRODUCT OFFER CREATE ----------------
class OfferCreateSerializer(serializers.Serializer):
    category_id = serializers.IntegerField(required=False, allow_null=True)
    template_type = serializers.CharField()
    product_ids = serializers.ListField(child=serializers.UUIDField())

    def validate(self, data):
        if not data.get("product_ids"):
            raise serializers.ValidationError("At least one product required")
        return data

    def create(self, validated_data):
        user = self.context["request"].user

        category = None
        if validated_data.get("category_id"):
            category = Category.objects.filter(id=validated_data["category_id"]).first()

        offer = Offer.objects.create(
            user=user,
            category=category,
            template_type=validated_data["template_type"]
        )

        products = Product.objects.filter(id__in=validated_data["product_ids"], user=user)
        offer.products.set(products)
        offer.save()

        return offer


# ------------- PUBLIC OFFER SERIALIZER ----------------
class OfferPublicSerializer(serializers.ModelSerializer):
    products = ProductSerializer(many=True)
    category = CategorySerializer()
    qr_url = serializers.SerializerMethodField()

    class Meta:
        model = Offer
        fields = (
            "id",
            "title",
            "template_type",
            "category",
            "products",
            "offer_link",
            "qr_url",
            "created_at",
            "is_public"
        )

    def get_qr_url(self, obj):
        if obj.qr_code:
            return obj.qr_code.url
        return None
