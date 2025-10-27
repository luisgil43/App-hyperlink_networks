# invoicing/urls.py
from django.urls import path

from . import (views, views_api, views_branding, views_customers,
               views_invoices, views_itemcodes, views_templates)

app_name = "invoicing"

urlpatterns = [
    path("customers/", views_customers.customers_list, name="customers_list"),
    path("customers/create/", views_customers.customers_create, name="customers_create"),

    # CUSTOMERS
    path("customers/detail/",  views_customers.customers_detail,  name="customers_detail"),
    path("customers/update/",  views_customers.customers_update,  name="customers_update"),
    path("customers/delete/",  views_customers.customers_delete,  name="customers_delete"),

    # BRANDING (logos + perfiles)
    path("branding/", views_branding.view_Branding, name="branding"),
    path("branding/upload/", views_branding.branding_upload, name="branding_upload"),
    path("branding/delete/", views_branding.branding_delete, name="branding_delete"),
    path("branding/set-primary/", views_branding.branding_set_primary, name="branding_set_primary"),

    # CRUD de perfiles de branding
    path("branding/profile/save/",        views_branding.profile_save,        name="branding_profile_save"),
    path("branding/profile/detail/",      views_branding.profile_detail,      name="branding_profile_detail"),
    path("branding/profile/delete/",      views_branding.profile_delete,      name="branding_profile_delete"),
    path("branding/profile/set-default/", views_branding.profile_set_default, name="branding_profile_set_default"),

    # Templates
    path("templates/", views_templates.view_Templates, name="templates"),
    path("templates/set/", views_templates.template_set, name="template_set"),
    path("templates/preview/<slug:key>/", views_templates.template_preview, name="template_preview"),

    # Item Codes
    path("item-codes/", views_itemcodes.itemcodes_list, name="itemcodes_list"),
    path("item-codes/new/", views_itemcodes.itemcodes_edit, name="itemcodes_new"),
    path("item-codes/edit/<int:pk>/", views_itemcodes.itemcodes_edit, name="itemcodes_edit"),
    path("item-codes/delete/", views_itemcodes.itemcodes_delete, name="itemcodes_delete"),
    path("item-codes/import/", views_itemcodes.itemcodes_import, name="itemcodes_import"),
    path("item-codes/template/", views_itemcodes.itemcodes_template, name="itemcodes_template"),

    path("invoices/", views_invoices.invoices_list, name="invoices_list"),
    path("invoices/delete/", views_invoices.invoice_delete, name="invoice_delete"),
    path("invoices/set_status/", views_invoices.invoice_set_status, name="invoice_set_status"),  
    path("invoices/new/", views_invoices.invoice_new, name="invoice_new"),

    path("api/customers/",  views_api.api_customers, name="api_customers"),
    path("api/itemcodes/",  views_api.api_itemcodes, name="api_itemcodes"),
    path("api/invoices/create", views_invoices.invoice_create_api, name="api_invoice_create"),
    path("api/invoices/next-number", views_invoices.invoice_next_number_api, name="api_invoice_next_number"),

]



