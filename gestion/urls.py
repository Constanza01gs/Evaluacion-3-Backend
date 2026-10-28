"""
Rutas de la aplicación de gestión de FitLife.
Incluye rutas para vistas web basadas en templates y endpoints de la API REST.
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

# Router para la API REST (Django REST Framework)
router = DefaultRouter()
router.register(r'socios', views.SocioViewSet, basename='api-socio')
router.register(r'planes', views.PlanViewSet, basename='api-plan')
router.register(r'membresias', views.MembresiaViewSet, basename='api-membresia')
router.register(r'pagos', views.PagoViewSet, basename='api-pago')
router.register(r'clases', views.ClaseViewSet, basename='api-clase')
router.register(r'reservas', views.ReservaViewSet, basename='api-reserva')
router.register(r'accesos', views.RegistroAccesoViewSet, basename='api-acceso')

urlpatterns = [
    # Redirección raíz y autenticación
    path('', views.login_view, name='raiz'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('dashboard/', views.dashboard_redirect, name='dashboard_redirect'),

    # Paneles principales según rol
    path('admin-dashboard/', views.admin_dashboard, name='admin_dashboard'),
    path('recepcion-dashboard/', views.recepcion_dashboard, name='recepcion_dashboard'),

    # Módulo de Socios
    path('socios/', views.socios_list_view, name='socios_list'),
    path('socios/nuevo/', views.socio_form_view, name='socio_nuevo'),
    path('socios/<int:socio_id>/editar/', views.socio_form_view, name='socio_editar'),
    path('socios/<int:socio_id>/eliminar/', views.socio_eliminar_logico_view, name='socio_eliminar_logico'),
    path('socios/<int:socio_id>/restaurar/', views.socio_restaurar_logico_view, name='socio_restaurar_logico'),
    path('gestion/<str:modulo>/', views.gestion_list_view, name='gestion_list'),
    path('gestion/pagos/<int:pago_id>/anular/', views.anular_pago_view, name='anular_pago'),
    path('gestion/<str:modulo>/nuevo/', views.gestion_form_view, name='gestion_nuevo'),
    path('gestion/<str:modulo>/<int:objeto_id>/editar/', views.gestion_form_view, name='gestion_editar'),
    path('gestion/<str:modulo>/<int:objeto_id>/<str:accion>/', views.gestion_estado_view, name='gestion_estado'),

    # Módulo de Clases y Reservas
    path('clases/', views.clases_list_view, name='clases_list'),
    path('clases/<int:clase_id>/reservar/', views.reservar_clase_web_view, name='reservar_clase_web'),
    path('reservas/<int:reserva_id>/cancelar/', views.cancelar_reserva_web_view, name='cancelar_reserva_web'),

    # Módulo de Control de Acceso QR
    path('control-acceso/', views.control_acceso_view, name='control_acceso'),

    # Endpoints de API especializados (AJAX para Frontend)
    path('api/acceso/validar/', views.ValidarAccesoAPIView.as_view(), name='api_validar_acceso'),
    path('api/aforo/', views.AforoAPIView.as_view(), name='api_aforo'),

    # Endpoints CRUD de DRF
    path('api/', include(router.urls)),
]
