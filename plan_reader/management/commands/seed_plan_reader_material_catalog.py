from __future__ import annotations

from dataclasses import dataclass

from django.core.management.base import BaseCommand
from django.db import transaction

from plan_reader.models import MaterialCatalogItem


@dataclass(frozen=True)
class CatalogMaterial:
    code: str
    material_type: str
    category: str
    material_name: str
    uom: str
    auto_rule: str
    display_order: int
    is_active: bool = True


def manual_material(
    *,
    code: str,
    category: str,
    material_name: str,
    uom: str,
    display_order: int,
) -> CatalogMaterial:
    """
    Construye una fila manual del formulario original.

    Todas las filas del documento usan:
        material_type = UNDERGROUND
    """
    return CatalogMaterial(
        code=code,
        material_type="UNDERGROUND",
        category=category,
        material_name=material_name,
        uom=uom,
        auto_rule=MaterialCatalogItem.RULE_MANUAL,
        display_order=display_order,
    )


MATERIALS = [
    # =========================================================================
    # CONDUIT
    # =========================================================================
    manual_material(
        code="underground-conduit-1-inch",
        category="Conduit",
        material_name='1" Conduit',
        uom="FT",
        display_order=10,
    ),
    manual_material(
        code="underground-conduit-1-inch-coupler",
        category="Conduit",
        material_name='1" Conduit Coupler',
        uom="EA",
        display_order=20,
    ),
    manual_material(
        code="underground-conduit-1-25-inch",
        category="Conduit",
        material_name='1.25" Conduit',
        uom="FT",
        display_order=30,
    ),
    manual_material(
        code="underground-conduit-1-25-inch-coupler",
        category="Conduit",
        material_name='1.25" Conduit Coupler',
        uom="EA",
        display_order=40,
    ),
    # =========================================================================
    # DEVICES
    # =========================================================================
    manual_material(
        code="underground-device-400-ld-hh",
        category="Device",
        material_name="400 LD HH",
        uom="EA",
        display_order=50,
    ),
    manual_material(
        code="underground-device-400-tier-8-hh",
        category="Device",
        material_name="400 Tier 8 HH",
        uom="EA",
        display_order=60,
    ),
    manual_material(
        code="underground-device-500-hh",
        category="Device",
        material_name="500 HH",
        uom="EA",
        display_order=70,
    ),
    manual_material(
        code="underground-device-900-hh",
        category="Device",
        material_name="900 HH",
        uom="EA",
        display_order=80,
    ),
    manual_material(
        code="underground-device-bd1-ped",
        category="Device",
        material_name="BD1 PED",
        uom="EA",
        display_order=90,
    ),
    manual_material(
        code="underground-device-bd3-ped",
        category="Device",
        material_name="BD3 PED",
        uom="EA",
        display_order=100,
    ),
    manual_material(
        code="underground-device-bd5-ped",
        category="Device",
        material_name="BD5 PED",
        uom="EA",
        display_order=110,
    ),
    manual_material(
        code="underground-device-bdorat-6",
        category="Device",
        material_name='BDORAT - 6"',
        uom="EA",
        display_order=120,
    ),
    manual_material(
        code="underground-device-bdorat-8",
        category="Device",
        material_name='BDORAT - 8"',
        uom="EA",
        display_order=130,
    ),
    manual_material(
        code="underground-device-bh-1",
        category="Device",
        material_name="BH-1",
        uom="EA",
        display_order=140,
    ),
    manual_material(
        code="underground-device-bh-2",
        category="Device",
        material_name="BH-2",
        uom="EA",
        display_order=150,
    ),
    manual_material(
        code="underground-device-bh-mini",
        category="Device",
        material_name="BH-Mini",
        uom="EA",
        display_order=160,
    ),
    manual_material(
        code="underground-device-tier-22",
        category="Device",
        material_name="Tier 22",
        uom="EA",
        display_order=170,
    ),
    manual_material(
        code="underground-device-flower-pots-drops",
        category="Device",
        material_name="Flower Pots (Drops)",
        uom="EA",
        display_order=180,
    ),
    # =========================================================================
    # FIBER
    # =========================================================================
    manual_material(
        code="underground-fiber-24ct",
        category="Fiber",
        material_name="24ct Fiber",
        uom="FT",
        display_order=190,
    ),
    manual_material(
        code="underground-fiber-48ct",
        category="Fiber",
        material_name="48ct Fiber",
        uom="FT",
        display_order=200,
    ),
    manual_material(
        code="underground-fiber-72ct",
        category="Fiber",
        material_name="72ct Fiber",
        uom="FT",
        display_order=210,
    ),
    # =========================================================================
    # GROUNDING
    # =========================================================================
    manual_material(
        code="underground-grounding-ground-rod",
        category="Grounding",
        material_name="Ground Rod",
        uom="EA",
        display_order=220,
    ),
    manual_material(
        code="underground-grounding-ground-rod-clamp-acorn",
        category="Grounding",
        material_name="Ground Rod Clamp ( Acorn)",
        uom="EA",
        display_order=230,
    ),
    manual_material(
        code="underground-grounding-iso-switch",
        category="Grounding",
        material_name="ISO Switch",
        uom="EA",
        display_order=240,
    ),
    manual_material(
        code="underground-grounding-neutral-bar",
        category="Grounding",
        material_name="NEUTRAL BAR ( Ground Bars)",
        uom="EA",
        display_order=250,
    ),
    manual_material(
        code="underground-grounding-toneable-mule-tape",
        category="Grounding",
        material_name="Toneable Mule Tape",
        uom="FT",
        display_order=260,
    ),
    manual_material(
        code="underground-grounding-tracer-wire",
        category="Grounding",
        material_name="Tracer Wire",
        uom="FT",
        display_order=270,
    ),
    # =========================================================================
    # SPLICING — MANUAL ACCESSORIES
    # =========================================================================
    manual_material(
        code="underground-splicing-blue-zip-ties",
        category="Splicing",
        material_name="Blue Zip Ties",
        uom="EA",
        display_order=280,
    ),
    manual_material(
        code="underground-splicing-orange-zip-ties",
        category="Splicing",
        material_name="Orange Zip Ties",
        uom="EA",
        display_order=290,
    ),
    manual_material(
        code="underground-splicing-green-zip-ties",
        category="Splicing",
        material_name="Green Zip Ties",
        uom="EA",
        display_order=300,
    ),
    manual_material(
        code="underground-splicing-brown-zip-ties",
        category="Splicing",
        material_name="Brown Zip Ties",
        uom="EA",
        display_order=310,
    ),
    manual_material(
        code="underground-splicing-slate-zip-ties",
        category="Splicing",
        material_name="Slate Zip Ties",
        uom="EA",
        display_order=320,
    ),
    # =========================================================================
    # SPLICING — SPLICE CASES
    # =========================================================================
    manual_material(
        code="underground-splicing-case-fosc450-d3v",
        category="Splicing",
        material_name="SPLICE CASE - FOSC450-D6-6-NT-0-D3V",
        uom="EA",
        display_order=330,
    ),
    manual_material(
        code="underground-splicing-case-fosc450-d6v",
        category="Splicing",
        material_name="SPLICE CASE - FOSC450-D6-6-NT-0-D6V",
        uom="EA",
        display_order=340,
    ),
    CatalogMaterial(
        code="underground-splicing-case-ofdc-a4",
        material_type="UNDERGROUND",
        category="Splicing",
        material_name="SPLICE CASE - OFDC-A4-S2/44-14-N-12",
        uom="EA",
        auto_rule=MaterialCatalogItem.RULE_SPLICE_CASE_A4,
        display_order=350,
    ),
    CatalogMaterial(
        code="underground-splicing-case-ofdc-b8g-empty",
        material_type="UNDERGROUND",
        category="Splicing",
        material_name="SPLICE CASE - OFDC-B8G-NN/00-NN-N-72",
        uom="EA",
        auto_rule=MaterialCatalogItem.RULE_SPLICE_CASE_B8G_EMPTY,
        display_order=360,
    ),
    CatalogMaterial(
        code="underground-splicing-case-ofdc-b8g-1x2",
        material_type="UNDERGROUND",
        category="Splicing",
        material_name="SPLICE CASE - OFDC-B8G-S2/82-12-NN-72",
        uom="EA",
        auto_rule=MaterialCatalogItem.RULE_SPLICE_CASE_B8G_1X2,
        display_order=370,
    ),
    CatalogMaterial(
        code="underground-splicing-case-ofdc-b8g-1x4",
        material_type="UNDERGROUND",
        category="Splicing",
        material_name="SPLICE CASE - OFDC-B8G-S2/84-14-NN-72",
        uom="EA",
        auto_rule=MaterialCatalogItem.RULE_SPLICE_CASE_B8G_1X4,
        display_order=380,
    ),
    CatalogMaterial(
        code="underground-splicing-case-ofdc-b8g-1x8",
        material_type="UNDERGROUND",
        category="Splicing",
        material_name="SPLICE CASE - OFDC-B8G-S2/88-18-N-72",
        uom="EA",
        auto_rule=MaterialCatalogItem.RULE_SPLICE_CASE_B8G_1X8,
        display_order=390,
    ),
    CatalogMaterial(
        code="underground-splicing-case-ofdc-c12-1x8",
        material_type="UNDERGROUND",
        category="Splicing",
        material_name="SPLICE CASE - OFDC-C12-S2/88-18-N-96",
        uom="EA",
        auto_rule=MaterialCatalogItem.RULE_SPLICE_CASE_C12,
        display_order=400,
    ),
    manual_material(
        code="underground-splicing-case-ofdc-c12-tt",
        category="Splicing",
        material_name="SPLICE CASE - OFDC-C12-S2/TT-NN-N-96",
        uom="EA",
        display_order=410,
    ),
    # =========================================================================
    # SPLICING — SLEEVES
    # =========================================================================
    CatalogMaterial(
        code="underground-splicing-sleeve-40mm",
        material_type="UNDERGROUND",
        category="Splicing",
        material_name="SPLICE SLEEVE - 40MM",
        uom="EA",
        auto_rule=MaterialCatalogItem.RULE_SPLICE_SLEEVE_40MM,
        display_order=420,
    ),
    CatalogMaterial(
        code="underground-splicing-sleeve-60mm",
        material_type="UNDERGROUND",
        category="Splicing",
        material_name="SPLICE SLEEVE - 60MM",
        uom="EA",
        auto_rule=MaterialCatalogItem.RULE_SPLICE_SLEEVE_60MM,
        display_order=430,
    ),
    # =========================================================================
    # SPLICING — HOLDERS AND TRAYS
    # =========================================================================
    manual_material(
        code="underground-splicing-fosc-sp-splitter-holder",
        category="Splicing",
        material_name="FOSC-SP Splitter Holder 1/8 (Splitter Chip)",
        uom="EA",
        display_order=440,
    ),
    manual_material(
        code="underground-splicing-fusion-splice-tray",
        category="Splicing",
        material_name="Fusion Splice Tray",
        uom="EA",
        display_order=450,
    ),
    # =========================================================================
    # SPLICING — SPLITTERS
    # =========================================================================
    CatalogMaterial(
        code="underground-splicing-splitter-1x2-250-micron",
        material_type="UNDERGROUND",
        category="Splicing",
        material_name=(
            "SPLITTER - 1x2 250 Micron, Bare Fiber, Non Ribbon, "
            "Without Connectors, Symmetrical"
        ),
        uom="EA",
        auto_rule=MaterialCatalogItem.RULE_SPLITTER_1X2,
        display_order=460,
    ),
    manual_material(
        code="underground-splicing-splitter-1x2-900-micron",
        category="Splicing",
        material_name=(
            "SPLITTER - 1x2 900 Micron, Loose Tube, SC/APC Splitter, "
            "With Connectors, Symmetrical"
        ),
        uom="EA",
        display_order=470,
    ),
    manual_material(
        code="underground-splicing-splitter-1x2-planar",
        category="Splicing",
        material_name="SPLITTER - 1X2 Planar",
        uom="EA",
        display_order=480,
    ),
    manual_material(
        code="underground-splicing-splitter-1x2-plc-900-micron",
        category="Splicing",
        material_name=(
            "SPLITTER - 1X2 PLC 900 Micron, Loose Tube, SC/APC "
            "Splitter, With Connectors"
        ),
        uom="EA",
        display_order=490,
    ),
    CatalogMaterial(
        code="underground-splicing-splitter-1x4-250-micron",
        material_type="UNDERGROUND",
        category="Splicing",
        material_name=(
            "SPLITTER - 1x4 250 Micron, Bare Fiber, Non Ribbon, "
            "Without Connectors, Symmetrical"
        ),
        uom="EA",
        auto_rule=MaterialCatalogItem.RULE_SPLITTER_1X4,
        display_order=500,
    ),
    manual_material(
        code="underground-splicing-splitter-1x4-900-micron",
        category="Splicing",
        material_name=(
            "SPLITTER - 1x4 900 Micron, Loose Tube, SC/APC Splitter, "
            "With Connectors, Symmetrical"
        ),
        uom="EA",
        display_order=510,
    ),
    manual_material(
        code="underground-splicing-splitter-1x4-planar",
        category="Splicing",
        material_name="SPLITTER - 1X4 Planar",
        uom="EA",
        display_order=520,
    ),
    CatalogMaterial(
        code="underground-splicing-splitter-1x6-planar",
        material_type="UNDERGROUND",
        category="Splicing",
        material_name="SPLITTER - 1X6 Planar",
        uom="EA",
        auto_rule=MaterialCatalogItem.RULE_SPLITTER_1X6,
        display_order=530,
    ),
    CatalogMaterial(
        code="underground-splicing-splitter-1x8-250-micron",
        material_type="UNDERGROUND",
        category="Splicing",
        material_name=(
            "SPLITTER - 1x8 250 Micron, Bare Fiber, Non Ribbon, "
            "Without Connectors, Symmetrical"
        ),
        uom="EA",
        auto_rule=MaterialCatalogItem.RULE_SPLITTER_1X8,
        display_order=540,
    ),
    manual_material(
        code="underground-splicing-splitter-1x8-900-micron",
        category="Splicing",
        material_name=(
            "SPLITTER - 1x8 900 Micron, Loose Tube, SC/APC Splitter, "
            "With Connectors, Symmetrical"
        ),
        uom="EA",
        display_order=550,
    ),
    manual_material(
        code="underground-splicing-splitter-1x8-planar",
        category="Splicing",
        material_name="SPLITTER - 1X8 Planar",
        uom="EA",
        display_order=560,
    ),
    # =========================================================================
    # TDS LABEL
    #
    # El formulario original contiene una sola fila llamada TDS LABEL.
    # Se conserva como manual porque el modelo permite solamente una auto_rule
    # por material y las reglas actuales de TDS están separadas por caja.
    # =========================================================================
    manual_material(
        code="underground-splicing-tds-label",
        category="Splicing",
        material_name="TDS LABEL",
        uom="EA",
        display_order=570,
    ),
]


class Command(BaseCommand):
    help = (
        "Creates or updates the exact Underground Material Request catalog "
        "and deactivates catalog records that are not part of the form."
    )

    @transaction.atomic
    def handle(self, *args, **options):
        created_count = 0
        updated_count = 0
        unchanged_count = 0
        deactivated_count = 0

        valid_codes = {material.code for material in MATERIALS}

        obsolete_queryset = MaterialCatalogItem.objects.exclude(
            code__in=valid_codes,
        ).filter(
            is_active=True,
        )

        deactivated_count = obsolete_queryset.update(
            is_active=False,
        )

        for material in MATERIALS:
            defaults = {
                "material_type": material.material_type,
                "category": material.category,
                "material_name": material.material_name,
                "uom": material.uom,
                "auto_rule": material.auto_rule,
                "display_order": material.display_order,
                "is_active": material.is_active,
            }

            catalog_item, created = MaterialCatalogItem.objects.get_or_create(
                code=material.code,
                defaults=defaults,
            )

            if created:
                created_count += 1

                self.stdout.write(
                    self.style.SUCCESS(
                        f"CREATED: {material.code} - " f"{material.material_name}"
                    )
                )

                continue

            changed_fields = []

            for field_name, expected_value in defaults.items():
                current_value = getattr(
                    catalog_item,
                    field_name,
                )

                if current_value == expected_value:
                    continue

                setattr(
                    catalog_item,
                    field_name,
                    expected_value,
                )

                changed_fields.append(
                    field_name,
                )

            if changed_fields:
                catalog_item.save(
                    update_fields=[
                        *changed_fields,
                        "updated_at",
                    ]
                )

                updated_count += 1

                self.stdout.write(
                    self.style.WARNING(
                        f"UPDATED: {material.code} " f"({', '.join(changed_fields)})"
                    )
                )
            else:
                unchanged_count += 1

                self.stdout.write(f"UNCHANGED: {material.code}")

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS("Underground Material Request catalog seed completed.")
        )
        self.stdout.write(f"Created: {created_count}")
        self.stdout.write(f"Updated: {updated_count}")
        self.stdout.write(f"Unchanged: {unchanged_count}")
        self.stdout.write(f"Deactivated obsolete records: {deactivated_count}")
        self.stdout.write(f"Configured materials: {len(MATERIALS)}")
        self.stdout.write(
            "Active catalog records: "
            f"{MaterialCatalogItem.objects.filter(is_active=True).count()}"
        )
