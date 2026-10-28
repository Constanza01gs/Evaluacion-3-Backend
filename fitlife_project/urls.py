"""
Configuración global de URLs para el proyecto FitLife.
"""

from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # Panel de administración de Django
    path('admin/', admin.site.urls),

    # Aplicación principal FitLife
    path('', include('gestion.urls')),
]

# Servir archivos multimedia en desarrollo
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])

# La ruta final muestra el 404 de FitLife también durante el desarrollo.
from gestion.views import custom_404_view
urlpatterns += [re_path(r'^.*$', custom_404_view)]

# Manejador de error 404 personalizado para rutas no encontradas
handler404 = 'gestion.views.custom_404_view'
