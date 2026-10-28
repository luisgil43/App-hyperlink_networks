"""
Pruebas de integridad para sincronización de kilometraje.

No utilizan registros de producción.
"""

import ast
from pathlib import Path

from django.test import SimpleTestCase


ROOT = Path(__file__).resolve().parent.parent


class OdometerSynchronizationIntegrityTests(SimpleTestCase):

    def _function_source(self, relative, name):
        path = ROOT / relative

        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))

        matches = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == name
        ]

        self.assertEqual(len(matches), 1)

        return ast.unparse(matches[0])

    def test_fleet_uses_atomic_transaction(self):
        source = self._function_source(
            "fleet/views.py",
            "service_edit",
        )

        self.assertIn("transaction.atomic", source)
        self.assertIn("select_for_update", source)

    def test_fleet_checks_project_access(self):
        source = self._function_source(
            "fleet/views.py",
            "service_edit",
        )

        self.assertIn("user_has_project_access", source)

    def test_fleet_checks_financial_role(self):
        source = self._function_source(
            "fleet/views.py",
            "service_edit",
        )

        self.assertIn("financial_role", source)
        self.assertIn("facturacion", source)

    def test_fleet_updates_expense_odometer(self):
        source = self._function_source(
            "fleet/views.py",
            "service_edit",
        )

        self.assertIn("CartolaMovimiento.objects", source)
        self.assertIn("kilometraje=new_odometer", source)

    def test_expense_uses_atomic_transaction(self):
        source = self._function_source(
            "facturacion/views.py",
            "editar_movimiento",
        )

        self.assertIn("transaction.atomic", source)
        self.assertIn("select_for_update", source)

    def test_expense_updates_fleet_odometer(self):
        source = self._function_source(
            "facturacion/views.py",
            "editar_movimiento",
        )

        self.assertIn("VehicleService.objects", source)
        self.assertIn(
            "kilometraje_declarado=kilometraje_nuevo",
            source,
        )

    def test_expense_preserves_approval_fields(self):
        source = self._function_source(
            "facturacion/views.py",
            "editar_movimiento",
        )

        self.assertIn("campos_permitidos", source)

        self.assertNotIn(
            '"aprobado_por_supervisor",',
            source,
        )

        self.assertNotIn(
            '"aprobado_por_pm",',
            source,
        )

        self.assertNotIn(
            '"aprobado_por_finanzas",',
            source,
        )

    def test_expense_does_not_create_odometer_events(self):
        source = self._function_source(
            "facturacion/views.py",
            "editar_movimiento",
        )

        self.assertNotIn(
            "VehicleOdometerEvent.objects.create",
            source,
        )

    def test_fleet_does_not_create_linked_odometer_events(self):
        source = self._function_source(
            "fleet/views.py",
            "service_edit",
        )

        self.assertNotIn(
            "VehicleOdometerEvent.objects.create",
            source,
        )
