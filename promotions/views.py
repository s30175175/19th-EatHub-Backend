from datetime import date

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from users.models import User, UserCoupon
from users.utils import check_and_downgrade_vip, check_merchant_role, token_required_cbv

from .models import Coupon, Promotion
from .serializers import (
    CouponSerializer,
    MerchantCouponSerializer,
    PromotionsCreateSerializer,
    PromotionSerializer,
    UserCouponUsageSerializer,
)


class CreateCouponView(APIView):
    @token_required_cbv
    def post(self, request):
        user = User.objects.filter(uuid=request.user_uuid).first()
        if not user:
            return Response({'success': False}, status=status.HTTP_401_UNAUTHORIZED)

        if user.role not in ['merchant', 'vip_merchant']:
            return Response({'success': False}, status=status.HTTP_403_FORBIDDEN)

        if user.role == 'merchant':
            has_coupon = Coupon.objects.filter(
                restaurant=user.restaurant, is_archived=False
            ).exists()
            if has_coupon:
                return Response({'success': False}, status=status.HTTP_403_FORBIDDEN)
        if user.role == 'vip_merchant':
            count = Coupon.objects.filter(restaurant=user.restaurant, is_archived=False).count()
            if count >= 3:
                return Response(
                    {'success': False},
                    status=status.HTTP_403_FORBIDDEN,
                )
        data = request.data.copy()
        serializer = CouponSerializer(data=data)

        if serializer.is_valid():
            serializer.save(restaurant=user.restaurant)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        else:
            return Response({'success': False}, status=status.HTTP_400_BAD_REQUEST)


class ClaimCouponView(APIView):
    @token_required_cbv
    def post(self, request, uuid):
        user = get_object_or_404(User, uuid=request.user_uuid)
        coupon = get_object_or_404(Coupon, uuid=uuid, is_archived=False)

        if UserCoupon.objects.filter(user=user, coupon=coupon).exists():
            return Response({'success': False}, status=status.HTTP_200_OK)

        UserCoupon.objects.create(user=user, coupon=coupon)
        return Response({'success': True}, status=status.HTTP_201_CREATED)


class PromotionCreateView(APIView):
    @token_required_cbv
    def post(self, request):
        user = get_object_or_404(User, uuid=request.user_uuid)

        if user.role not in ['merchant', 'vip_merchant']:
            return Response({'error': '此帳戶無建立動態權限'}, status=status.HTTP_403_FORBIDDEN)

        if not user.restaurant:
            return Response({'error': '帳戶未綁定餐廳'}, status=status.HTTP_403_FORBIDDEN)

        promotion_count = user.restaurant.promotions.filter(is_archived=False).count()
        limit = 3 if user.role == 'vip_merchant' or user.is_vip else 1

        if promotion_count >= limit:
            role_display = 'VIP 商家' if user.is_vip else '一般商家'
            return Response({'error': f'{role_display} 最多只能建立 {limit} 則動態'}, status=400)

        serializer = PromotionsCreateSerializer(
            data=request.data,
            context={
                'request': request,
                'restaurant': user.restaurant,
            },
        )
        if serializer.is_valid():
            promotion = serializer.save()
            return Response(
                PromotionsCreateSerializer(promotion).data, status=status.HTTP_201_CREATED
            )
        return Response({'success': False}, status=status.HTTP_400_BAD_REQUEST)


class MerchantView(APIView):
    @token_required_cbv
    @check_merchant_role
    @check_and_downgrade_vip
    def get(self, request):
        user = get_object_or_404(User, uuid=request.user_uuid)
        if user.role not in ['merchant', 'vip_merchant']:
            return Response({'success': False}, status=status.HTTP_403_FORBIDDEN)

        restaurant = user.restaurant
        if not restaurant:
            return Response({'success': False}, status=status.HTTP_400_BAD_REQUEST)

        promotions = Promotion.objects.filter(restaurant=restaurant, is_archived=False)
        coupons = Coupon.objects.filter(restaurant=restaurant, is_archived=False)
        max_count = 3 if user.role == 'vip_merchant' else 1
        is_coupon_limit_reached = coupons.count() >= max_count
        is_promotion_limit_reached = promotions.count() >= max_count

        latest_sub = user.subscriptions.order_by('-ended_at').first()
        vip_expiry = (
            latest_sub.ended_at if latest_sub and latest_sub.ended_at >= date.today() else None
        )

        return Response(
            {
                'result': {
                    'restaurant': {
                        'uuid': str(restaurant.uuid),
                        'name': restaurant.name,
                    },
                    'merchant_status': {
                        'role': user.role,
                        'is_coupon_limit_reached': is_coupon_limit_reached,
                        'is_promotion_limit_reached': is_promotion_limit_reached,
                        'vip_expiry': vip_expiry,
                    },
                    'promotions': PromotionSerializer(promotions, many=True).data,
                    'coupons': MerchantCouponSerializer(coupons, many=True).data,
                }
            },
            status=status.HTTP_200_OK,
        )


class CouponUsageView(APIView):
    @token_required_cbv
    def get(self, request, uuid):
        user = get_object_or_404(User, uuid=request.user_uuid)
        coupon = get_object_or_404(Coupon, uuid=uuid)

        if user.restaurant != coupon.restaurant:
            return Response({'success': False}, status=status.HTTP_403_FORBIDDEN)

        user_coupons = UserCoupon.objects.filter(coupon=coupon).select_related('user')
        serializer = UserCouponUsageSerializer(user_coupons, many=True)

        return Response(
            {'title': coupon.title, 'usages': serializer.data}, status=status.HTTP_200_OK
        )


class CouponDetailView(APIView):
    @token_required_cbv
    @check_merchant_role
    def get(self, request, uuid):
        user = get_object_or_404(User, uuid=request.user_uuid)

        coupon = get_object_or_404(Coupon, uuid=uuid, is_archived=False)
        if user.restaurant != coupon.restaurant:
            return Response({'success': False}, status=status.HTTP_403_FORBIDDEN)
        serializer = CouponSerializer(coupon)
        result = serializer.data
        result['total_claimed'] = UserCoupon.objects.filter(coupon=coupon).count()
        result['total_used'] = UserCoupon.objects.filter(coupon=coupon, is_used=True).count()

        return Response({'result': result}, status=status.HTTP_200_OK)

    @token_required_cbv
    def patch(self, request, uuid):
        user = get_object_or_404(User, uuid=request.user_uuid)

        if user.role not in ['merchant', 'vip_merchant']:
            return Response({'success': False}, status=status.HTTP_403_FORBIDDEN)

        restaurant = user.restaurant
        if not restaurant:
            return Response({'success': False}, status=status.HTTP_400_BAD_REQUEST)

        coupon = get_object_or_404(Coupon, uuid=uuid, restaurant=restaurant)

        coupon.is_archived = True
        coupon.save()

        return Response({'success': True}, status=status.HTTP_200_OK)


class PromotionDetailView(APIView):
    @token_required_cbv
    def get(self, request, uuid):
        user = get_object_or_404(User, uuid=request.user_uuid)

        promotion = get_object_or_404(Promotion, uuid=uuid, is_archived=False)
        if user.restaurant != promotion.restaurant:
            return Response({'success': False}, status=status.HTTP_403_FORBIDDEN)
        serializer = PromotionSerializer(promotion)
        return Response({'result': serializer.data}, status=status.HTTP_200_OK)

    @token_required_cbv
    def patch(self, request, uuid):
        user = get_object_or_404(User, uuid=request.user_uuid)

        if user.role not in ['merchant', 'vip_merchant']:
            return Response({'success': False}, status=status.HTTP_403_FORBIDDEN)

        restaurant = user.restaurant
        if not restaurant:
            return Response({'success': False}, status=status.HTTP_400_BAD_REQUEST)

        promotion = get_object_or_404(Promotion, uuid=uuid, restaurant=restaurant)

        promotion.is_archived = True
        promotion.save()

        return Response({'success': True}, status=status.HTTP_200_OK)
