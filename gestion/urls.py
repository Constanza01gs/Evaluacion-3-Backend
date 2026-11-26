"""
Rutas de la aplicación de gestión de FitLife.
Incluye rutas para vistas web basadas en templates y endpoints de la API REST.
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views, solicitudes_pago, actividad_gimnasio, reportes

# Router para la API REST (Django REST Framework)
router = DefaultRouter()
router.register(r'socios', views.SocioViewSet, basename='api-socio')
router.register(r'planes', views.PlanViewSet, basename='api-plan')
router.register(r'membresias', views.MembresiaViewSet, basename='api-membresia')
router.register(r'pagos', views.PagoViewSet, basename='api-pago')
router.register(r'clases', views.ClaseViewSet, basename='api-clase')
router.register(r'reservas', views.ReservaViewSet, basename='api-reserva')
router.register(r'accesos', views.RegistroAccesoViewSet, basename='api-acceso')

from . import vistas_horario

urlpatterns = [
    # Redirección raíz y autenticación
    path('', views.pagina_publica_view, name='raiz'),
    path('login/', views.login_view, name='login'),
    path('crear-cuenta/', views.registrar_socio_view, name='registrar_socio'),
    path('recuperar-cuenta/', views.recuperar_socio_view, name='recuperar_socio'),
    path('recuperar-cuenta/pregunta/', views.recuperar_socio_pregunta_view, name='recuperar_socio_pregunta'),
    path('recuperar-cuenta/nueva-clave/', views.recuperar_socio_clave_view, name='recuperar_socio_clave'),
    path('recuperar-cuenta/codigo/', views.recuperar_socio_codigo_view, name='recuperar_socio_codigo'),
    path('activar-cuenta/', views.activar_cuenta_profesor_view, name='activar_cuenta_profesor'),
    path('activar-recepcion/', views.activar_cuenta_recepcion_view, name='activar_cuenta_recepcion'),
    path('gestion/recepcionistas/', views.recepcionistas_view, name='recepcionistas'),
    path('gestion/recepcionistas/<int:usuario_id>/ficha/', views.recepcionista_ficha_view, name='recepcionista_ficha'),
    path('gestion/recepcionistas/<int:usuario_id>/editar/', views.editar_recepcionista_view, name='editar_recepcionista'),
    path('gestion/recepcionistas/<int:usuario_id>/estado/<str:accion>/', views.estado_recepcionista_view, name='estado_recepcionista'),
    path('gestion/recepcionistas/nuevo/', views.crear_recepcionista_view, name='crear_recepcionista'),
    path('gestion/recepcionistas/<int:usuario_id>/codigo/', views.regenerar_codigo_recepcion_view, name='regenerar_codigo_recepcion'),
    path('activar-cuenta-socio/', views.activar_cuenta_socio_view, name='activar_cuenta_socio'),
    path('profesores/<int:profesor_id>/crear-cuenta/', views.crear_cuenta_profesor_view, name='crear_cuenta_profesor'),
    path('profesores/<int:profesor_id>/regenerar-codigo/', views.regenerar_codigo_profesor_view, name='regenerar_codigo_profesor'),
    path('logout/', views.logout_view, name='logout'),
    path('dashboard/', views.dashboard_redirect, name='dashboard_redirect'),
    path('socio/mi-cuenta/', views.socio_portal_view, name='socio_portal'),
    path('socio/membresias/contratar/', views.socio_contratar_view, name='socio_contratar'),
    path('socio/membresias/', views.socio_membresias_view, name='socio_membresias'),
    path('socio/membresias/<int:membresia_id>/solicitar-congelamiento/', views.socio_solicitar_congelamiento_view, name='socio_solicitar_congelamiento'),
    path('socio/membresias/solicitudes-congelamiento/', views.socio_solicitudes_congelamiento_view, name='socio_solicitudes_congelamiento'),
    path('socio/membresias/confirmar/', views.socio_confirmar_contratacion_view, name='socio_confirmar_contratacion'),
    path('socio/pagos/', views.socio_pagos_view, name='socio_pagos'),
    path('socio/pagos/<int:pago_id>/comprobante/', views.socio_comprobante_view, name='socio_comprobante'),
    path('socio/notificaciones/resumen/', views.socio_avisos_resumen_view, name='socio_avisos_resumen'),
    path('socio/codigo-acceso/', views.socio_codigo_acceso_view, name='socio_codigo_acceso'),
    path('socio/codigo-acceso/qr/', views.socio_codigo_qr_view, name='socio_codigo_qr'),
    path('socio/clases/', views.socio_clases_view, name='socio_clases'),
    path('socio/clases/<int:clase_id>/reservar/', views.socio_reservar_view, name='socio_reservar'),
    path('socio/reservas/', views.socio_reservas_view, name='socio_reservas'),
    path('socio/reservas/<int:reserva_id>/cancelar/', views.socio_cancelar_reserva_view, name='socio_cancelar_reserva'),
    path('socio/notificaciones/', views.socio_notificaciones_view, name='socio_notificaciones'),
    path('socio/notificaciones/limpiar/', views.socio_limpiar_notificaciones_view, name='socio_limpiar_notificaciones'),
    path('socio/perfil/', views.socio_perfil_view, name='socio_perfil'),
    path('socio/perfil/cambiar-clave/', views.socio_cambiar_clave_view, name='socio_cambiar_clave'),
    path('socio/perfil/seguridad/', views.socio_seguridad_view, name='socio_seguridad'),

    # Paneles principales según rol
    path('admin-dashboard/', views.admin_dashboard, name='admin_dashboard'),
    path('reportes/', reportes.reportes_view, name='reportes'),
    path('reportes/pdf/', reportes.reportes_pdf_view, name='reportes_pdf'),
    path('recepcion-dashboard/', views.recepcion_dashboard, name='recepcion_dashboard'),
    path('recepcion/notificaciones/', views.recepcion_notificaciones_view, name='recepcion_notificaciones'),
    path('recepcion/notificaciones/limpiar/', views.recepcion_limpiar_notificaciones_view, name='recepcion_limpiar_notificaciones'),
    path('recepcion/notificaciones/resumen/', views.recepcion_avisos_resumen_view, name='recepcion_avisos_resumen'),
    path('profesor/notificaciones/limpiar/', views.profesor_limpiar_notificaciones_view, name='profesor_limpiar_notificaciones'),
    path('profesor/notificaciones/resumen/', views.profesor_avisos_resumen_view, name='profesor_avisos_resumen'),
    path('profesor/clases/', views.profesor_clases_view, name='profesor_clases'),
    path('profesor/perfil/', views.profesor_perfil_view, name='profesor_perfil'),
    path('profesor/perfil/cambiar-clave/', views.profesor_cambiar_clave_view, name='profesor_cambiar_clave'),
    path('profesor/notificaciones/', views.profesor_notificaciones_view, name='profesor_notificaciones'),
    path('profesor/solicitudes/', views.profesor_solicitudes_view, name='profesor_solicitudes'),
    path('profesor/clases/<int:clase_id>/', views.profesor_detalle_clase_view, name='profesor_detalle_clase'),
    path('profesor/clases/<int:clase_id>/guardar-asistencia/', views.profesor_guardar_asistencia_view, name='profesor_guardar_asistencia'),
    path('profesor/clases/<int:clase_id>/avisar-indisponibilidad/', views.avisar_indisponibilidad_view, name='avisar_indisponibilidad'),
    path('clases/<int:clase_id>/cancelar/', views.cancelar_clase_view, name='cancelar_clase'),
    path('gestion/avisos-profesores/', views.avisos_indisponibilidad_view, name='avisos_indisponibilidad'),
    path('gestion/avisos-profesores/<int:aviso_id>/reemplazo/', views.reemplazar_profesor_aviso_view, name='reemplazar_profesor_aviso'),
    path('gestion/avisos-profesores/<int:aviso_id>/revisar/', views.revisar_indisponibilidad_view, name='revisar_indisponibilidad'),
    path('clases/<int:clase_id>/cerrar-asistencia/', views.cerrar_asistencia_clase_view, name='cerrar_asistencia_clase'),
    path('profesor/clases/<int:clase_id>/reservas/<int:reserva_id>/asistencia/', views.profesor_marcar_asistencia_view, name='profesor_marcar_asistencia'),
    path('profesor/clases/<int:clase_id>/reservas/<int:reserva_id>/solicitar-correccion/', views.solicitar_correccion_asistencia_view, name='solicitar_correccion_asistencia'),
    path('pagos/<int:pago_id>/solicitar-anulacion/', solicitudes_pago.solicitar_anulacion_pago, name='solicitar_anulacion_pago'),
    path('solicitudes/pagos/', solicitudes_pago.solicitudes_anulacion_pago, name='solicitudes_anulacion_pago'),
    path('solicitudes/pagos/<int:solicitud_id>/resolver/', solicitudes_pago.resolver_anulacion_pago, name='resolver_anulacion_pago'),
    path('solicitudes/', views.solicitudes_historial_view, name='solicitudes_historial'),
    path('solicitudes/historial/<str:categoria>/<int:solicitud_id>/', views.solicitud_historial_detalle_view, name='solicitud_historial_detalle'),
    path('solicitudes/asistencia/', views.solicitudes_asistencia_view, name='solicitudes_asistencia'),
    path('solicitudes/congelamiento/', views.solicitudes_congelamiento_view, name='solicitudes_congelamiento'),
    path('solicitudes/congelamiento/<int:solicitud_id>/rechazar/', views.rechazar_congelamiento_view, name='rechazar_congelamiento'),
    path('solicitudes/congelamiento/<int:solicitud_id>/aprobar/', views.aprobar_congelamiento_view, name='aprobar_congelamiento'),
    path('socio/congelamiento/<int:solicitud_id>/cancelar/', views.cancelar_solicitud_congelamiento_view, name='cancelar_solicitud_congelamiento'),
    path('socio/congelamiento/<int:solicitud_id>/cancelar-pausa/', views.socio_cancelar_pausa_aprobada_view, name='socio_cancelar_pausa_aprobada'),
    path('socio/congelamiento/<int:solicitud_id>/solicitar-reactivacion/', views.socio_solicitar_reactivacion_view, name='socio_solicitar_reactivacion'),
    path('solicitudes/congelamiento/<int:solicitud_id>/aprobar-reactivacion/', views.aprobar_reactivacion_view, name='aprobar_reactivacion'),
    path('solicitudes/asistencia/<int:solicitud_id>/resolver/', views.resolver_correccion_asistencia_view, name='resolver_correccion_asistencia'),

    # Módulo de Socios
    path('recepcion/socios/buscar/', views.recepcion_buscar_socios_view, name='recepcion_buscar_socios'),
    path('recepcion/membresias/contratar/', views.recepcion_contratar_membresia_view, name='recepcion_contratar_membresia'),
    path('recepcion/membresias/<int:membresia_id>/ficha/', views.recepcion_membresia_ficha_view, name='recepcion_membresia_ficha'),
    path('recepcion/membresias/confirmar/', views.recepcion_confirmar_membresia_view, name='recepcion_confirmar_membresia'),
    path('recepcion/membresias/comprobante/<int:pago_id>/', views.recepcion_comprobante_membresia_view, name='recepcion_comprobante_membresia'),
    path('socios/', views.socios_list_view, name='socios_list'),
    path('socios/nuevo/', views.socio_form_view, name='socio_nuevo'),
    path('socios/<int:socio_id>/membresias/<int:membresia_id>/', views.socio_membresia_detalle_view, name='socio_membresia_detalle'),
    path('socios/<int:socio_id>/', views.socio_ficha_view, name='socio_ficha'),
    path('socios/<int:socio_id>/membresias/<int:membresia_id>/registrar-pago/', views.socio_registrar_pago_pendiente_view, name='socio_registrar_pago_pendiente'),
    path('socios/<int:socio_id>/pagos/<int:pago_id>/', views.recepcion_pago_detalle_view, name='recepcion_pago_detalle'),
    path('socios/<int:socio_id>/editar/', views.socio_form_view, name='socio_editar'),
    path('gestion/membresias/<int:membresia_id>/solicitar-congelamiento/', views.personal_solicitar_congelamiento_view, name='personal_solicitar_congelamiento'),
    path('socios/<int:socio_id>/renovar-codigo/', views.renovar_codigo_acceso_view, name='renovar_codigo_acceso'),
    path('socios/<int:socio_id>/generar-activacion/', views.generar_activacion_socio_view, name='generar_activacion_socio'),
    path('socios/<int:socio_id>/generar-recuperacion/', views.generar_recuperacion_socio_view, name='generar_recuperacion_socio'),
    path('socios/<int:socio_id>/eliminar/', views.socio_eliminar_logico_view, name='socio_eliminar_logico'),
    path('socios/<int:socio_id>/restaurar/', views.socio_restaurar_logico_view, name='socio_restaurar_logico'),
    path('gestion/<str:modulo>/', views.gestion_list_view, name='gestion_list'),
    path('gestion/<str:modulo>/<int:objeto_id>/ficha/', views.gimnasio_ficha_view, name='gimnasio_ficha'),
    path('gestion/pagos/<int:pago_id>/anular/', views.anular_pago_view, name='anular_pago'),
    path('gestion/<str:modulo>/nuevo/', views.gestion_form_view, name='gestion_nuevo'),
    path('gestion/<str:modulo>/<int:objeto_id>/editar/', views.gestion_form_view, name='gestion_editar'),
    path('gestion/<str:modulo>/<int:objeto_id>/<str:accion>/', views.gestion_estado_view, name='gestion_estado'),

    # Módulo de Clases y Reservas
    path('clases/', views.clases_list_view, name='clases_list'),
    path('clases/horario-fijo/nuevo/', vistas_horario.horario_fijo_guardar, name='horario_fijo_nuevo'),
    path('clases/<int:clase_id>/horario-fijo/', vistas_horario.horario_fijo_guardar, name='horario_fijo_editar'),
    path('clases/<int:clase_id>/horario-fijo/detener/', vistas_horario.horario_fijo_detener, name='horario_fijo_detener'),
    path('clases/<int:clase_id>/detalle/', views.admin_clase_detalle_view, name='admin_clase_detalle'),
    path('clases/<int:clase_id>/reservar/', views.reservar_clase_web_view, name='reservar_clase_web'),
    path('reservas/<int:reserva_id>/cancelar/', views.cancelar_reserva_web_view, name='cancelar_reserva_web'),
    path('reservas/<int:reserva_id>/asistencia/', views.confirmar_asistencia_web_view, name='confirmar_asistencia_web'),

    # Módulo de Control de Acceso QR
    path('actividad-gimnasio/', actividad_gimnasio.actividad_gimnasio, name='actividad_gimnasio'),
    path('control-acceso/', views.control_acceso_view, name='control_acceso'),

    # Endpoints de API especializados (AJAX para Frontend)
    path('api/acceso/validar/', views.ValidarAccesoAPIView.as_view(), name='api_validar_acceso'),
    path('api/reservas/<int:reserva_id>/confirmar-asistencia/', views.confirmar_asistencia_api_view, name='api_confirmar_asistencia'),
    path('api/aforo/', views.AforoAPIView.as_view(), name='api_aforo'),
    path('api/salas/disponibilidad/', views.DisponibilidadClasesAPIView.as_view(), name='api_disponibilidad_clases'),

    # Endpoints CRUD de DRF
    path('api/', include(router.urls)),
]
