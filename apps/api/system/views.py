from django.db import DatabaseError, connections
from rest_framework.response import Response
from rest_framework.views import APIView


class LivenessView(APIView):
    authentication_classes: list = []
    permission_classes: list = []

    def get(self, request):
        return Response({"status": "ok"})


class ReadinessView(APIView):
    authentication_classes: list = []
    permission_classes: list = []

    def get(self, request):
        try:
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT 1")
        except DatabaseError:
            return Response({"status": "unavailable"}, status=503)
        return Response({"status": "ok"})
