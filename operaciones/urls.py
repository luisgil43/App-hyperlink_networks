# operaciones/urls.py

from django.urls import path
from . import views as v
from . import views_billing_exec as b
from . import views_billing_exec as views
from . import views

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
         b.upload_evidencias, name="upload_evidencias"),
    path("operaciones/billing/my/<int:pk>/evidencia/<int:evidencia_id>/eliminar/",
         b.eliminar_evidencia, name="eliminar_evidencia",),
    path("prices/bulk-delete/", views.bulk_delete_precios,
         name="bulk_delete_precios"),

    path("api/uploads/presign/", b.presign_wasabi,
         name="operaciones_presign_wasabi"),
    # Página de importación
    path("billing/<int:sesion_id>/requisitos/import/",
         b.import_requirements_page,
         name="import_requirements_page",
         ),
    # Descarga del formato (csv|xlsx)
    path("billing/<int:sesion_id>/requisitos/import/template/<str:ext>/",
         b.download_requirements_template,
         name="download_requirements_template",
         ),
    # Procesamiento del archivo subido (POST)
    path("billing/<int:sesion_id>/requisitos/import/process/",
         b.importar_requisitos,
         name="importar_requisitos",
         ),
    path("direct-uploads/presign/", b.presign_wasabi, name="presign_wasabi"),
    path("billing/export/", views.exportar_billing_excel, name="billing_export"),

    path("billing/<int:sesion_id>/update-semana/",
         b.update_semana_pago_real, name="billing_update_semana"),

    path("produccion/admin/", views.produccion_admin, name="produccion_admin"),
    path("produccion/mia/",   views.produccion_usuario, name="produccion_usuario"),


    path(
        "produccion/admin/pagos/",
        views.admin_weekly_payments,
        name="admin_weekly_payments",
    ),

    # Subida rápida a Wasabi (presigned POST) — AJAX
    path(
        "produccion/admin/pagos/<int:pk>/presign/",
        views.presign_receipt,
        name="presign_receipt",
    ),
    path(
        "produccion/admin/pagos/<int:pk>/confirm/",
        views.confirm_receipt,
        name="confirm_receipt",
    ),

    # (Opcional / respaldo) flujo clásico con multipart a Django
    path("produccion/admin/pagos/<int:pk>/pagar/",
         views.admin_mark_paid, name="admin_mark_paid"),

    # =================== USUARIO ==================
    # Tabla “Approve my payment” con acciones Aprobar/Rechazar
    path("mi-produccion/pagos/", views.user_weekly_payments,
         name="user_weekly_payments"),
    path("mi-produccion/pagos/<int:pk>/aprobar/",
         views.user_approve_payment, name="user_approve_payment"),
    path("mi-produccion/pagos/<int:pk>/rechazar/",
         views.user_reject_payment, name="user_reject_payment"),

    path(
        "produccion/admin/pagos/<int:pk>/reset/",
        views.admin_reset_payment_status,
        name="admin_reset_payment_status",
    ),

    path("rendiciones/presign/", views.presign_rendicion, name="presign_rendicion"),

    path("produccion/admin/pagos/unpay/<int:pk>/",
         v.admin_unpay, name="admin_unpay"),

    path(
        "operaciones/billing/set-real-week/<int:pk>/",
        views.billing_set_real_week,
        name="billing_set_real_week",
    ),
    path(
        "billing/<int:pk>/reopen-asignado/",
        views.billing_reopen_asignado,
        name="billing_reopen_asignado"),
    path("billing/send-to-finance/", views.billing_send_to_finance,
         name="billing_send_finance"),
    path("billing/finance/mark-in-review/<int:pk>/",
         views.billing_mark_in_review, name="billing_mark_in_review"),

    path("operaciones/billing/<int:sesion_id>/reporte-parcial/",
         b.generar_reporte_parcial_proyecto, name="generar_reporte_parcial_proyecto"),
    path("operaciones/billing/asignacion/<int:asignacion_id>/reporte-parcial/",
         b.generar_reporte_parcial_asignacion, name="generar_reporte_parcial_asignacion"),
    path("billing/items/<int:item_id>/qty/",
         views.billing_item_update_qty, name="billing_item_update_qty"),

    path("operaciones/fotos/upload-ajax/<int:pk>/",
         b.upload_evidencias_ajax, name="fotos_upload_ajax"),
    path("fotos/asignacion/<int:asig_id>/status/", b.fotos_status_json,
         name="fotos_status_json"),

]
