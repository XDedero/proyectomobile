from django.contrib import admin
from django.urls import path, include
from mobileApp.web_performance import health

urlpatterns = [
    path('healthz/', health, name='health'),
    path('admin/', admin.site.urls),
    path('', include('mobileApp.urls')),
]
