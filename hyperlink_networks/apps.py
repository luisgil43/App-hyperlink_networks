from django.contrib.admin.apps import AdminConfig


class CustomAdminConfig(AdminConfig):
    default_site = 'hyperlink_networks.admin.CustomAdminSite'
