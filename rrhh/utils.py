from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
import io
import os
import logging
import tempfile
import requests
from datetime import date, timedelta

from django.utils.html import strip_tags
from django.utils.text import slugify
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from cloudinary.uploader import upload, destroy

from PIL import Image as PilImage, UnidentifiedImageError

from PyPDF2 import PdfReader, PdfWriter

from reportlab.lib import colors
from reportlab.lib.colors import black
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from rrhh.models import FichaIngreso, Feriado
from .models import SolicitudVacaciones

from PIL import Image as PilImage, UnidentifiedImageError
from reportlab.platypus import Image, Paragraph, Spacer, Table, TableStyle
from django.utils.text import slugify
from reportlab.platypus import SimpleDocTemplate
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.lib import colors
from reportlab.lib.units import cm
from django.core.files.base import ContentFile
import io


def contar_dias_habiles(inicio, fin):
    dias_habiles = 0
    delta = fin - inicio
    for i in range(delta.days + 1):
        dia = inicio + timedelta(days=i)
        if dia.weekday() < 5 and not Feriado.objects.filter(fecha=dia).exists():
            dias_habiles += 1
    return dias_habiles


def calcular_estado_documento(fecha_vencimiento):
    """
    Calcula el estado de un documento según su fecha de vencimiento.

    - 'válido': si la fecha de vencimiento es futura y queda más de 15 días.
    - 'por_vencer': si quedan 15 días o menos.
    - 'vencido': si ya expiró.
    """
    hoy = date.today()

    if fecha_vencimiento < hoy:
        return 'vencido'
    elif fecha_vencimiento <= hoy + timedelta(days=15):
        return 'por_vencer'
    else:
        return 'vigente'


def descargar_firma_desde_url(url):
    try:
        response = requests.get(url)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        if "image" not in content_type:
            print(f"⚠️ La URL no contiene una imagen válida: {content_type}")
            return None
        return io.BytesIO(response.content)
    except Exception as e:
        print(f"⚠️ Error al descargar firma desde {url}: {e}")
        return None


def generar_pdf_solicitud_vacaciones(solicitud):
    print("📝 Generando PDF para solicitud ID:", solicitud.id)

    usuario = solicitud.usuario
    dias_disponibles = usuario.obtener_dias_vacaciones_disponibles()
    tipo_real = 'total' if float(solicitud.dias_solicitados) == float(
        dias_disponibles) else 'parcial'
    identidad = usuario.identidad or str(usuario.pk)
    pm = solicitud.aprobado_por_pm
    rrhh = solicitud.aprobado_por_rrhh

    # Firmas desde Cloudinary
    firma_trabajador = descargar_firma_desde_url(
        usuario.firma_digital.url) if usuario.firma_digital else None
    firma_pm = descargar_firma_desde_url(
        pm.firma_digital.url) if pm and pm.firma_digital else None
    firma_rrhh = descargar_firma_desde_url(
        rrhh.firma_digital.url) if rrhh and rrhh.firma_digital else None

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                            topMargin=1.5 * cm, bottomMargin=2 * cm)
    styles = getSampleStyleSheet()

    # Estilos personalizados
    titulo_style = ParagraphStyle(
        name='TituloCentrado',
        alignment=TA_CENTER,
        fontSize=18,
        leading=22,
        spaceAfter=16,
        textColor=colors.black,
        fontName='Helvetica-Bold',
    )

    subtitulo_style = ParagraphStyle(
        name='SubtituloCentrado',
        alignment=TA_CENTER,
        fontSize=13,
        leading=16,
        spaceAfter=24,
        textColor=colors.black,
        fontName='Helvetica-Bold',
    )

    estilo_fila = ParagraphStyle(
        name='EstiloFila',
        fontName='Helvetica',
        fontSize=10,
        leading=14,
    )

    elements = []
    elements.append(
        Paragraph("FORMULARIO SOLICITUD DE VACACIONES LEGALES", titulo_style))
    elements.append(
        Paragraph("INGENIERÍA Y CONSTRUCCIÓN MV LIMITADA", subtitulo_style))

    # Datos principales
    datos = [
        Paragraph(f'<b>NOMBRE:</b> {usuario.get_full_name()}', estilo_fila),
        Paragraph(f'<b>RUT:</b> {usuario.identidad}', estilo_fila),
        Paragraph(f'<b>CARGO:</b> Pendiente de ficha', estilo_fila),
        Paragraph(
            f'<b>PERIODO:</b> {solicitud.fecha_inicio.strftime("%d-%m-%Y")} al {solicitud.fecha_fin.strftime("%d-%m-%Y")}', estilo_fila),
        Paragraph(
            f'<b>TIPO:</b> {"Total" if tipo_real == "total" else "Parcial"}', estilo_fila),
        Paragraph(
            f'<b>DÍAS HÁBILES:</b> {solicitud.dias_solicitados}', estilo_fila),
    ]

    tabla_datos = Table([[d] for d in datos], colWidths=[16.5 * cm])
    tabla_datos.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
    ]))
    elements.append(tabla_datos)
    elements.append(Spacer(1, 20))

    texto = f"""
    <b>El trabajador hará uso {"Total" if tipo_real == "total" else "Parcial"}</b> de sus vacaciones legales,
    desde el día <b>{solicitud.fecha_inicio.strftime('%d-%m-%Y')}</b> hasta el <b>{solicitud.fecha_fin.strftime('%d-%m-%Y')}</b>.<br/><br/>
    Total de días <b>hábiles</b> tomados: <b>{solicitud.dias_solicitados}</b>.<br/><br/>
    Fecha de la Solicitud: <b>{solicitud.fecha_solicitud.strftime('%d-%m-%Y')}</b>
    """
    elements.append(Paragraph(texto, styles['Normal']))
    elements.append(Spacer(1, 24))

    # Firmas
    encabezados = ['Firma del Trabajador',
                   'Jefe Directo', 'V° B° Recursos Humanos']
    firmas = [
        Image(firma_trabajador, width=4 * cm, height=2 *
              cm) if firma_trabajador else Spacer(4 * cm, 2 * cm),
        Image(firma_pm, width=4 * cm, height=2 *
              cm) if firma_pm else Spacer(4 * cm, 2 * cm),
        Image(firma_rrhh, width=4 * cm, height=2 *
              cm) if firma_rrhh else Spacer(4 * cm, 2 * cm),
    ]
    tabla_firmas = Table([encabezados, firmas], colWidths=[5.5 * cm] * 3)
    tabla_firmas.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('TOPPADDING', (0, 1), (-1, 1), 4),
    ]))
    elements.append(tabla_firmas)

    # Guardar PDF en buffer
    doc.build(elements)

    nombre_archivo = "Solicitud de vacaciones.pdf"
    pdf_content = ContentFile(buffer.getvalue())

    # Reemplaza si ya existía
    if solicitud.archivo_pdf and solicitud.archivo_pdf.name:
        solicitud.archivo_pdf.delete(save=False)

    solicitud.archivo_pdf.save(nombre_archivo, pdf_content, save=True)
    print("✅ PDF generado y guardado como:", nombre_archivo)


def generar_ficha_ingreso_pdf(ficha):
    print("📝 Generando PDF para ficha ID:", ficha.id)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                            topMargin=1.5 * cm, bottomMargin=2 * cm)
    styles = getSampleStyleSheet()

    titulo_style = ParagraphStyle(name='Titulo', alignment=1, fontSize=16, leading=20,
                                  spaceAfter=10, textColor=colors.black, fontName='Helvetica-Bold')
    subtitulo_style = ParagraphStyle(name='Subtitulo', alignment=1, fontSize=13, leading=16,
                                     spaceAfter=20, textColor=colors.black, fontName='Helvetica-Bold')
    texto_normal = ParagraphStyle(name='TextoNormal', fontName='Helvetica', fontSize=10,
                                  leading=14, spaceAfter=5)

    elements = []

    # Título y subtítulo
    elements.append(
        Paragraph("FICHA DE INGRESO DE PERSONAL NUEVO", titulo_style))
    elements.append(
        Paragraph("INGENIERÍA Y CONSTRUCCIÓN MV LIMITADA", subtitulo_style))

    datos = [
        Paragraph(
            f"<b>Nombre Completo:</b> {ficha.nombres} {ficha.apellidos}", texto_normal),
        Paragraph(f"<b>RUT/Pasaporte:</b> {ficha.rut}", texto_normal),
        Paragraph(
            f"<b>Fecha de Nacimiento:</b> {ficha.fecha_nacimiento}", texto_normal),
        Paragraph(f"<b>Edad:</b> {ficha.edad}", texto_normal),
        Paragraph(f"<b>Estado Civil:</b> {ficha.estado_civil}", texto_normal),
        Paragraph(f"<b>Teléfono:</b> {ficha.telefono}", texto_normal),
        Paragraph(f"<b>Correo Electrónico:</b> {ficha.email}", texto_normal),
        Paragraph(f"<b>Dirección:</b> {ficha.direccion}", texto_normal),
        Paragraph(f"<b>Comuna:</b> {ficha.comuna}", texto_normal),
        Paragraph(f"<b>Ciudad:</b> {ficha.ciudad}", texto_normal),
        Spacer(1, 12),
        Paragraph("<b>DATOS BANCARIOS</b>", subtitulo_style),
        Paragraph(f"<b>Banco:</b> {ficha.banco}", texto_normal),
        Paragraph(f"<b>Tipo de cuenta:</b> {ficha.tipo_cuenta}", texto_normal),
        Paragraph(
            f"<b>Número de cuenta:</b> {ficha.numero_cuenta}", texto_normal),
        Spacer(1, 12),
        Paragraph("<b>DATOS LABORALES</b>", subtitulo_style),
        Paragraph(
            f"<b>Fecha de Ingreso:</b> {ficha.fecha_inicio}", texto_normal),
        Paragraph(f"<b>Cargo:</b> {ficha.cargo}", texto_normal),
        Paragraph(f"<b>Faena o Proyecto:</b> {ficha.faena}", texto_normal),
        Paragraph(f"<b>Jornada:</b> {ficha.jornada}", texto_normal),
        Paragraph(f"<b>Sueldo Base:</b> {ficha.sueldo_base}", texto_normal),
        Spacer(1, 12),
        Paragraph("<b>CONTACTO DE EMERGENCIA</b>", subtitulo_style),
        Paragraph(
            f"<b>Nombre:</b> {ficha.nombre_contacto_emergencia}", texto_normal),
        Paragraph(
            f"<b>Parentesco:</b> {ficha.parentesco_emergencia}", texto_normal),
        Paragraph(
            f"<b>Teléfono:</b> {ficha.telefono_emergencia}", texto_normal),
        Spacer(1, 48),
    ]
    elements.extend(datos)

    doc.build(elements)
    buffer.seek(0)

    # Eliminar archivo anterior si existe
    if ficha.archivo:
        ficha.archivo.delete(save=False)

    # Guardar en la ruta definida por el modelo (upload_to=ruta_ficha_ingreso)
    ficha.archivo.save("ficha_ingreso.pdf",
                       ContentFile(buffer.read()), save=True)
    ficha.save()

    print("✅ Ficha guardada en Cloudinary:", ficha.archivo.url)


def firmar_ficha_ingreso_pdf(ficha):
    # 1. Descargar PDF original desde Cloudinary
    response = requests.get(ficha.archivo.url)
    if response.status_code != 200:
        raise Exception("No se pudo descargar el PDF original")

    original_pdf = io.BytesIO(response.content)

    # 2. Crear una capa de firmas con reportlab
    packet = io.BytesIO()
    can = canvas.Canvas(packet, pagesize=letter)

    # Posiciones de firma
    y_firma = 70
    x_rrhh = 80
    x_pm = 250
    x_trabajador = 420

    def insertar_firma(usuario, x, y):
        if usuario and usuario.firma_digital:
            try:
                r = requests.get(usuario.firma_digital.url)
                if r.status_code == 200:
                    firma_img = ImageReader(io.BytesIO(r.content))
                    can.drawImage(firma_img, x, y, width=100,
                                  height=40, mask='auto')
                    can.setFont("Helvetica", 8)
                    can.drawCentredString(
                        x + 50, y - 12, usuario.get_full_name())
            except:
                pass

    # Insertar las 3 firmas y nombres
    insertar_firma(ficha.creado_por, x_rrhh, y_firma)      # RRHH
    insertar_firma(ficha.pm, x_pm, y_firma)                # PM
    insertar_firma(ficha.usuario, x_trabajador, y_firma)   # Trabajador

    # Etiquetas
    can.setFont("Helvetica-Bold", 9)
    can.drawCentredString(x_rrhh + 50, y_firma - 28, "RRHH")
    can.drawCentredString(x_pm + 50, y_firma - 28, "PM")
    can.drawCentredString(x_trabajador + 50, y_firma - 28, "Trabajador")

    can.save()
    packet.seek(0)

    # 3. Usar PyPDF2 para fusionar la capa con el PDF original
    original_reader = PdfReader(original_pdf)
    overlay_reader = PdfReader(packet)
    writer = PdfWriter()

    # Agregar capa de firmas solo a la primera página
    for i, page in enumerate(original_reader.pages):
        if i == 0:
            page.merge_page(overlay_reader.pages[0])
        writer.add_page(page)

    final_output = io.BytesIO()
    writer.write(final_output)
    final_output.seek(0)

    # 4. Guardar en Cloudinary reemplazando el anterior
    archivo_nombre = "Ficha_ingreso.pdf"

# Eliminar archivo anterior si existe
    if ficha.archivo:
        ficha.archivo.delete(save=False)

# Asegúrate de que la instancia tenga identidad válida para generar la ruta
    if ficha.usuario and ficha.usuario.identidad:
        identidad = ficha.usuario.identidad.replace('.', '').replace('-', '')
    else:
        identidad = slugify(ficha.rut or f"ficha_{ficha.pk}")

# Generar ruta final sin anidaciones dobles
    ruta_final = f"fichas_de_ingreso/{identidad}/{archivo_nombre}"

# Subir nuevo PDF firmado
    contenido = ContentFile(final_output.read())
    ficha.archivo.save(ruta_final, contenido, save=True)
