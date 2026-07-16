from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.contrib.auth import authenticate, login
from .models import User
from .serializers import UserSerializer


class UserViewSet(viewsets.ModelViewSet):
    """사용자 CRUD API"""
    queryset = User.objects.all()
    serializer_class = UserSerializer

    @action(detail=False, methods=['post'], authentication_classes=[], permission_classes=[])
    def login(self, request):
        email = request.data.get('email')
        password = request.data.get('password')

        if not email or not password:
            return Response({'error': '이메일과 비밀번호를 입력해주세요.'}, status=status.HTTP_400_BAD_REQUEST)

        user = authenticate(request, username=email, password=password)
        if user is not None:
            login(request, user)
            serializer = self.get_serializer(user)
            return Response({
                'message': '로그인 성공',
                'user': serializer.data
            })
        else:
            return Response({'error': '이메일 또는 비밀번호가 올바르지 않습니다.'}, status=status.HTTP_400_BAD_REQUEST)
