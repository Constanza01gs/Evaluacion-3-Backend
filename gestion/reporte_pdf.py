from io import BytesIO
from xml.sax.saxutils import escape

from django.utils import timezone
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether


GREEN = colors.HexColor('#a9d32a')
DARK = colors.HexColor('#17211b')
MUTED = colors.HexColor('#5b685e')
PALE = colors.HexColor('#f0f4eb')


def generar_pdf(d):
    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=(210*mm, 297*mm), leftMargin=19*mm, rightMargin=19*mm, topMargin=25*mm, bottomMargin=20*mm, title='Reporte general de gestión FitLife')
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='ReportTitle', parent=styles['Title'], fontName='Helvetica-Bold', fontSize=22, leading=26, textColor=DARK, alignment=0, spaceAfter=8))
    styles.add(ParagraphStyle(name='ReportSection', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=12, leading=15, textColor=DARK, spaceBefore=14, spaceAfter=7, keepWithNext=True))
    styles.add(ParagraphStyle(name='ReportBody', parent=styles['BodyText'], fontSize=9, leading=13, textColor=DARK, spaceAfter=5))
    styles.add(ParagraphStyle(name='ReportMuted', parent=styles['BodyText'], fontSize=8, leading=11, textColor=MUTED, spaceAfter=5))
    styles.add(ParagraphStyle(name='ReportCell', parent=styles['BodyText'], fontSize=8.5, leading=11, textColor=DARK))
    styles.add(ParagraphStyle(name='ReportHead', parent=styles['BodyText'], fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=MUTED))
    money = lambda n: '$' + format(int(n), ',').replace(',', '.') + ' CLP'
    num = lambda n: format(int(n), ',').replace(',', '.')
    p = lambda text: Paragraph(escape(str(text)), styles['ReportCell'])
    created = timezone.localtime().strftime('%d/%m/%Y %H:%M')

    def frame(canvas, document):
        w, h = document.pagesize
        canvas.saveState()
        canvas.setFillColor(DARK)
        canvas.rect(0, h-12*mm, w, 12*mm, fill=1, stroke=0)
        canvas.setFillColor(GREEN)
        canvas.setFont('Helvetica-Bold', 10)
        canvas.drawString(19*mm, h-8*mm, 'FITLIFE')
        canvas.setFillColor(colors.white)
        canvas.setFont('Helvetica', 8)
        canvas.drawRightString(w-19*mm, h-8*mm, 'REPORTE DE GESTIÓN')
        canvas.setStrokeColor(colors.HexColor('#dce4d8'))
        canvas.line(19*mm, 14*mm, w-19*mm, 14*mm)
        canvas.setFillColor(MUTED)
        canvas.setFont('Helvetica', 7.5)
        canvas.drawString(19*mm, 9*mm, 'FitLife · Generado ' + created)
        canvas.drawRightString(w-19*mm, 9*mm, 'Página ' + str(document.page))
        canvas.restoreState()

    def table(headers, rows, widths):
        content = [[Paragraph(escape(h), styles['ReportHead']) for h in headers]]
        content.extend([[p(value) for value in row] for row in rows] or [[p('Sin registros para este período.')] + [p('') for _ in headers[1:]]])
        t = Table(content, colWidths=widths, repeatRows=1, hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),PALE),('LINEBELOW',(0,0),(-1,0),0.5,colors.HexColor('#d8e1d5')),('LINEBELOW',(0,1),(-1,-1),0.25,colors.HexColor('#e4e9e1')),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
        return t

    def bars(items, key, monetary=False):
        if not items:
            return [Paragraph('Sin registros para este período.', styles['ReportMuted'])]
        out = []
        for item in items:
            name = str(item[key])
            label = name if len(name) <= 58 else name[:55] + '...'
            value = money(item['total']) if monetary else num(item['total'])
            drawing = Drawing(170*mm, 30)
            drawing.add(String(0, 19, label, fontName='Helvetica', fontSize=8.5, fillColor=DARK))
            drawing.add(Rect(0, 2, 125*mm, 8, fillColor=colors.HexColor('#e8eee5'), strokeColor=None))
            drawing.add(Rect(0, 2, 125*mm*item['porcentaje']/100, 8, fillColor=GREEN, strokeColor=None))
            drawing.add(String(129*mm, 2, value, fontName='Helvetica-Bold', fontSize=8, fillColor=DARK))
            out.append(KeepTogether(drawing))
        return out

    story = [Paragraph('Reporte general del gimnasio', styles['ReportTitle']),
             Paragraph('Resumen de gestión y actividad de FitLife', styles['ReportBody']),
             Paragraph('Período: {} al {} · Generado: {}'.format(d['desde'].strftime('%d/%m/%Y'), d['hasta'].strftime('%d/%m/%Y'), created), styles['ReportMuted']),
             Spacer(1, 5*mm), Paragraph('Resumen del período', styles['ReportSection'])]
    kpis = [
        [Paragraph('INGRESOS REGISTRADOS', styles['ReportHead']), Paragraph('MEMBRESÍAS CONTRATADAS', styles['ReportHead'])],
        [Paragraph(money(d['ingresos']), styles['ReportSection']), Paragraph(num(d['membresias_count']), styles['ReportSection'])],
        [Paragraph('ENTRADAS PERMITIDAS', styles['ReportHead']), Paragraph('ASISTENCIAS A CLASES', styles['ReportHead'])],
        [Paragraph(num(d['accesos_count']), styles['ReportSection']), Paragraph(num(d['asistencias_count']), styles['ReportSection'])],
    ]
    t = Table(kpis, colWidths=[86*mm,86*mm])
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),PALE),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),12),('RIGHTPADDING',(0,0),(-1,-1),12),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),('LINEBEFORE',(1,0),(1,-1),1,colors.white),('LINEABOVE',(0,2),(-1,2),2,colors.white)]))
    story.extend([t, Paragraph('Panorama de socios y membresías', styles['ReportSection']),
                  table(['Indicador','Resultado','Referencia'], [
                      ['Nuevos socios',num(d['socios_nuevos']),'Registrados durante el período'],
                      ['Socios activos',num(d['socios_activos']),'Estado al generar el reporte'],
                      ['Membresías contratadas',num(d['membresias_count']),'Contratos registrados en el período'],
                      ['Membresías vigentes',num(d['membresias_vigentes']),'Incluye pausadas; estado actual'],
                  ], [64*mm,25*mm,83*mm]),
                  Spacer(1,4*mm), Paragraph('Las cifras de estado actual describen el momento de generación; no son un corte histórico al final del período.', styles['ReportMuted']),
                  PageBreak(), Paragraph('Ingresos y planes', styles['ReportTitle']),
                  Paragraph('Pagos registrados en el período, excluyendo los que actualmente figuran anulados.', styles['ReportMuted']),
                  table(['Indicador','Resultado'], [
                      ['Ingresos registrados',money(d['ingresos'])],
                      ['Pagos no anulados',num(d['pagos_count'])],
                      ['Pagos anulados',num(d['pagos_anulados'])],
                      ['Monto de pagos anulados',money(d['monto_anulado'])],
                  ], [108*mm,64*mm]),
                  Paragraph('Ingresos por plan', styles['ReportSection'])])
    story.extend(bars(d['planes'], 'membresia__plan__nombre', True))
    story.extend([Paragraph('Ingresos por medio de pago', styles['ReportSection']),
                  table(['Medio de pago','Pagos','Monto'], [[x['nombre'],num(x['cantidad']),money(x['total'])] for x in d['metodos']], [80*mm,25*mm,67*mm]),
                  Paragraph('Evolución mensual de ingresos', styles['ReportSection']),
                  table(['Mes','Pagos','Monto'], [[x['mes'].strftime('%m/%Y'),num(x['cantidad']),money(x['total'])] for x in d['mensual']], [80*mm,25*mm,67*mm]),
                  PageBreak(), Paragraph('Operación y clases', styles['ReportTitle']),
                  Paragraph('Actividad registrada durante el período seleccionado.', styles['ReportMuted']),
                  table(['Indicador','Resultado','Referencia'], [
                      ['Entradas permitidas',num(d['accesos_count']),'Movimientos, no personas únicas'],
                      ['Entradas denegadas',num(d['accesos_denegados']),'Según fecha del intento'],
                      ['Clases programadas',num(d['clases_periodo']),'Clases activas actualmente'],
                      ['Clases desactivadas',num(d['clases_inactivas']),'Con fecha dentro del período'],
                      ['Reservas confirmadas',num(d['reservas_confirmadas']),'Estado actual; fecha de la clase'],
                      ['Reservas canceladas',num(d['reservas_canceladas']),'Estado actual; fecha de la clase'],
                      ['Asistencias',num(d['asistencias_count']),'Marcadas como asistió'],
                      ['Inasistencias',num(d['inasistencias']),'Marcadas como no asistió'],
                  ], [64*mm,25*mm,83*mm]),
                  Paragraph('Clases con más asistencias', styles['ReportSection'])])
    story.extend(bars(d['clases'], 'clase__nombre'))
    story.extend([Paragraph('Cómo interpretar este informe', styles['ReportSection']),
                  Paragraph('Los totales financieros consideran la fecha del pago y excluyen los pagos que están anulados al generar el documento. Las membresías y socios nuevos se cuentan por su fecha de registro. Los accesos se cuentan por fecha de movimiento; las reservas y asistencias, por fecha de la clase.', styles['ReportBody']),
                  Paragraph('Los estados pueden cambiar después de la fecha seleccionada: el informe usa el estado actual de los registros y no reconstruye una fotografía histórica. Las clases desactivadas no necesariamente fueron canceladas. Los pagos corresponden a registros del sistema.', styles['ReportMuted'])])
    doc.build(story, onFirstPage=frame, onLaterPages=frame)
    return output.getvalue()
