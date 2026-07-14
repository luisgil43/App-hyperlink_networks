from django.db import migrations

TABLE_NAME = "plan_reader_planreaderjob"
COLUMN_NAME = "reading_type"


def remove_legacy_reading_type(apps, schema_editor):
    """
    Limpieza defensiva de una columna residual creada durante desarrollo.

    - En bases donde reading_type no existe: no hace nada.
    - En SQLite local: elimina primero cualquier índice que dependa de la columna.
    - En producción/PostgreSQL: elimina la columna solo si realmente existe.

    Esta migración no modifica el estado de los modelos Django.
    """

    connection = schema_editor.connection

    with connection.cursor() as cursor:
        table_names = connection.introspection.table_names(cursor)

        if TABLE_NAME not in table_names:
            return

        description = connection.introspection.get_table_description(
            cursor,
            TABLE_NAME,
        )

        column_names = {column.name for column in description}

        if COLUMN_NAME not in column_names:
            return

        constraints = connection.introspection.get_constraints(
            cursor,
            TABLE_NAME,
        )

    quote_name = connection.ops.quote_name

    # Eliminar índices/constraints que dependan exclusivamente
    # de la columna residual antes de intentar borrar la columna.
    for constraint_name, details in constraints.items():
        columns = details.get("columns") or []

        if COLUMN_NAME not in columns:
            continue

        if details.get("primary_key"):
            continue

        quoted_constraint = quote_name(constraint_name)

        if details.get("index"):
            schema_editor.execute(f"DROP INDEX IF EXISTS {quoted_constraint}")

    vendor = connection.vendor

    if vendor == "postgresql":
        schema_editor.execute(f"""
            ALTER TABLE {quote_name(TABLE_NAME)}
            DROP COLUMN IF EXISTS {quote_name(COLUMN_NAME)}
            """)
        return

    # SQLite moderno soporta DROP COLUMN.
    # Como los índices dependientes ya fueron eliminados,
    # la operación puede ejecutarse sin dejar referencias rotas.
    if vendor == "sqlite":
        schema_editor.execute(f"""
            ALTER TABLE {quote_name(TABLE_NAME)}
            DROP COLUMN {quote_name(COLUMN_NAME)}
            """)
        return

    # Fallback para otras bases.
    schema_editor.execute(f"""
        ALTER TABLE {quote_name(TABLE_NAME)}
        DROP COLUMN {quote_name(COLUMN_NAME)}
        """)


def reverse_remove_legacy_reading_type(apps, schema_editor):
    """
    Reverse seguro.

    Solo vuelve a crear la columna si no existe.
    """
    connection = schema_editor.connection

    with connection.cursor() as cursor:
        table_names = connection.introspection.table_names(cursor)

        if TABLE_NAME not in table_names:
            return

        description = connection.introspection.get_table_description(
            cursor,
            TABLE_NAME,
        )

        column_names = {column.name for column in description}

    if COLUMN_NAME in column_names:
        return

    quote_name = connection.ops.quote_name

    schema_editor.execute(f"""
        ALTER TABLE {quote_name(TABLE_NAME)}
        ADD COLUMN {quote_name(COLUMN_NAME)}
        varchar(30) NOT NULL DEFAULT 'dfn_boxes'
        """)


class Migration(migrations.Migration):

    dependencies = [
        ("plan_reader", "0003_planreaderitem_splitter_lines"),
    ]

    operations = [
        migrations.RunPython(
            remove_legacy_reading_type,
            reverse_remove_legacy_reading_type,
        ),
    ]
