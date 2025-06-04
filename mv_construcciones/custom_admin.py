from django.contrib.admin import AdminSite


class CustomAdminSite(AdminSite):
    site_header = 'Mi Panel de Administración Personalizado'
    site_title = 'Admin MV Construcciones'
    index_title = 'Bienvenido al Panel de Administración'


custom_admin_site = CustomAdminSite(name='custom_admin')
