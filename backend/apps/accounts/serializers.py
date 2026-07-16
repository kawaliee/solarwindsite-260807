from rest_framework import serializers
from .models import User


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'email', 'name', 'department', 'role', 'created_at', 'last_login_at']
        read_only_fields = ['id', 'created_at']
