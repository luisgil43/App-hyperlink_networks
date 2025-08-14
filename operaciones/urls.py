# operaciones/urls.py

from django.urls import path
from . import views as v
from . import views_billing_exec as b
from . import views_billing_exec as views

app_name = 'operaciones'  # requerido para namespaces

urlpatterns = [
    # ---------------- Rendiciones ----------------
    path('mis-rendiciones/', v.mis_rendiciones, name='mis_rendiciones'),
    path('aprobar_abono/<int:pk>/', v.aprobar_abono, name='aprobar_abono'),
    path('rechazar_abono/<int:pk>/', v.rechazar_abono, name='rechazar_abono'),
    path('mis-rendiciones/editar/<int:pk>/',
         v.editar_rendicion, name='editar_rendicion'),
    path('mis-rendiciones/eliminar/<int:pk>/',
         v.eliminar_rendicion, name='eliminar_rendicion'),

    path('rendiciones/', v.vista_rendiciones, name='vista_rendiciones'),
    path('rendiciones/aprobar/<int:pk>/',
         v.aprobar_rendicion, name='aprobar_rendicion'),
    path('rendiciones/rechazar/<int:pk>/',
         v.rechazar_rendicion, name='rechazar_rendicion'),
    path('rendiciones/exportar/', v.exportar_rendiciones,
         name='exportar_rendiciones'),
    path('mis-rendiciones/exportar/', v.exportar_mis_rendiciones,
         name='exportar_mis_rendiciones'),

    path('precios/', v.listar_precios_tecnico, name='listar_precios_tecnico'),
    path('precios/import/', v.importar_precios, name='importar_precios'),
    path('precios/edit/<int:pk>/', v.editar_precio, name='editar_precio'),
    path('precios/delete/<int:pk>/', v.eliminar_precio, name='eliminar_precio'),
    path('precios/import/confirmar/', v.confirmar_importar_precios,
         name='confirmar_importar_precios'),

    # ---------------- Billing CRUD / List ----------------
    path("billing/nuevo/", v.crear_billing, name="crear_billing"),
    path("billing/<int:sesion_id>/editar/",
         v.editar_billing, name="editar_billing"),
    path("billing/<int:sesion_id>/eliminar/",
         v.eliminar_billing, name="eliminar_billing"),
    path("billing/<int:sesion_id>/reasignar/",
         v.reasignar_tecnicos, name="reasignar_tecnicos"),
    path("billing/listar/", v.listar_billing, name="listar_billing"),

    # AJAX dependientes
    path("billing/ajax/clientes/", v.ajax_clientes, name="ajax_clientes"),
    path("billing/ajax/ciudades/", v.ajax_ciudades, name="ajax_ciudades"),
    path("billing/ajax/proyectos/", v.ajax_proyectos, name="ajax_proyectos"),
    path("billing/ajax/oficinas/", v.ajax_oficinas, name="ajax_oficinas"),
    path("billing/ajax/buscar-codigos/",
         v.ajax_buscar_codigos, name="ajax_buscar_codigos"),
    path("billing/ajax/detalle-codigo/",
         v.ajax_detalle_codigo, name="ajax_detalle_codigo"),

    # ---------------- Técnico ----------------
    path("billing/my/", b.mis_assignments, name="mis_assignments"),
    path("billing/my/<int:pk>/", b.detalle_assignment, name="detalle_assignment"),
    path("billing/my/<int:pk>/start/",
         b.start_assignment, name="start_assignment"),
    path("billing/my/<int:pk>/upload/",
         b.upload_evidencias, name="upload_evidencias"),
    path("billing/my/<int:pk>/finish/",
         b.finish_assignment, name="finish_assignment"),

    # ---------------- Supervisor ----------------
    # (opcional) Configurar requisitos por técnico
    path("billing/<int:sesion_id>/requisitos/",
         b.configurar_requisitos, name="configurar_requisitos"),

    # Compatibilidad con enlaces antiguos (revisión por asignación)
    path("billing/revisar/<int:pk>/",
         b.revisar_assignment, name="revisar_assignment"),

    # NUEVA revisión unificada por PROYECTO
    path("billing/<int:sesion_id>/revisar/",
         b.revisar_sesion, name="revisar_sesion"),

    # ---------------- Reporte fotográfico POR PROYECTO ----------------
    path("billing/<int:sesion_id>/reporte/descargar/",
         b.descargar_reporte_fotos_proyecto, name="descargar_reporte_fotos_proyecto"),
    path("billing/<int:sesion_id>/reporte/regenerar/",
         b.regenerar_reporte_fotografico_proyecto, name="regenerar_reporte_fotografico_proyecto"),

    # ---------------- PM ----------------
    path("billing/sesion/<int:sesion_id>/pm/aprobar/",
         b.pm_aprobar_proyecto,  name="pm_aprobar_proyecto"),
    path("billing/sesion/<int:sesion_id>/pm/rechazar/",
         b.pm_rechazar_proyecto, name="pm_rechazar_proyecto"),


    path("operaciones/billing/my/<int:pk>/upload/",
         views.upload_evidencias, name="upload_evidencias"),
    path("operaciones/billing/my/<int:pk>/evidencia/<int:evidencia_id>/eliminar/",
         views.eliminar_evidencia, name="eliminar_evidencia",),


]
