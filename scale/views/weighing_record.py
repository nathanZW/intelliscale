"""
Weighing record management views for IntelliScale.
Handles weighing record CRUD, listing, and export (CSV, Excel, PDF).
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.core.paginator import Paginator
from django.utils import timezone
from django.utils.dateparse import parse_date
from users.views import is_admin
from ..models import (
    WeighingRecord, Scale, Product, WeighingProcess, DeliveryNote, 
    CompanySettings, PrintingNote
)
from datetime import datetime, timedelta, time
from decimal import Decimal
import csv
import json
from io import BytesIO


@login_required
@user_passes_test(is_admin)
def weighing_record_list(request):
    # Get filter parameters from request
    scale_id = request.GET.get('scale')
    product_id = request.GET.get('product')
    process_id = request.GET.get('process')
    barcode = request.GET.get('barcode')
    sync_status = request.GET.get('sync_status')
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    sort_param = request.GET.get('sort', '-timestamp')  # Default sort by timestamp desc
    
    # Start with all records
    records = WeighingRecord.objects.all()
    
    # Apply filters if provided
    if scale_id:
        records = records.filter(scale__scale_id=scale_id)
    
    if product_id:
        records = records.filter(product_id=product_id)
    
    if process_id:
        records = records.filter(process_id=process_id)
    
    if barcode:
        records = records.filter(barcode__icontains=barcode)
    
    if sync_status == 'unsynced':
        records = records.filter(is_synced=False)
    elif sync_status == 'synced':
        records = records.filter(is_synced=True)
    
    # Date range filtering
    if date_from:
        date_from_obj = parse_date(date_from)
        if date_from_obj:
            date_from_datetime = datetime.combine(date_from_obj, time.min)
            records = records.filter(timestamp__gte=date_from_datetime)
    
    if date_to:
        date_to_obj = parse_date(date_to)
        if date_to_obj:
            date_to_datetime = datetime.combine(date_to_obj, time.max)
            records = records.filter(timestamp__lte=date_to_datetime)
    
    # Apply sorting
    if sort_param:
        records = records.order_by(sort_param)
    
    # Pagination
    paginator = Paginator(records, 10)  # Show 10 records per page
    page_number = request.GET.get('page', 1)
    records_page = paginator.get_page(page_number)
    
    # Get lists for filter dropdowns
    scales = Scale.objects.all().order_by('name')
    products = Product.objects.all().order_by('name')
    processes = WeighingProcess.objects.all().order_by('name')
    
    context = {
        'records': records_page,
        'scales': scales,
        'products': products,
        'processes': processes,
    }
    
    return render(request, 'scale/weighing_record_list.html', context)


@login_required
@user_passes_test(is_admin)
def weighing_record_detail(request, pk):
    record = get_object_or_404(WeighingRecord, pk=pk)
    return render(request, 'scale/weighing_record_detail.html', {'record': record})


@login_required
@user_passes_test(is_admin)
def print_weighing_record(request, pk):
    record = get_object_or_404(WeighingRecord, pk=pk)
    
    # Update print count
    record.print_count += 1
    record.last_printed_at = timezone.now()
    record.save()
    
    # For now, just redirect back to the detail page with a success message
    messages.success(request, f'Weighing record {pk} sent to printer.')
    return redirect('scale:weighing_record_detail', pk=record.pk)


@login_required
@user_passes_test(is_admin)
def weighing_record_edit(request, pk):
    record = get_object_or_404(WeighingRecord, pk=pk)
    
    if request.method == 'POST':
        # For now, handle basic fields manually since we don't have a form
        try:
            record.gross_weight = Decimal(request.POST.get('gross_weight', record.gross_weight))
            record.tare_weight = Decimal(request.POST.get('tare_weight', record.tare_weight))
            record.net_weight = Decimal(request.POST.get('net_weight', record.net_weight))
            record.unit_of_measure = request.POST.get('unit_of_measure', record.unit_of_measure)
            record.notes = request.POST.get('notes', record.notes)
            record.barcode = request.POST.get('barcode', record.barcode)
            
            # Handle delivery note association
            delivery_note_id = request.POST.get('delivery_note_id')
            if delivery_note_id:
                record.delivery_note = get_object_or_404(DeliveryNote, pk=delivery_note_id)
            elif 'remove_delivery_note' in request.POST:
                record.delivery_note = None
            
            record.save()
            messages.success(request, 'Weighing record updated successfully.')
            return redirect('scale:weighing_record_detail', pk=record.pk)
        except Exception as e:
            messages.error(request, f'Error updating record: {str(e)}')
    
    # For edit, we should create a form, but for now, just render a template with the record
    context = {
        'record': record,
        'delivery_notes': DeliveryNote.objects.all().order_by('-created_at'),
        'scales': Scale.objects.filter(is_active=True),
        'products': Product.objects.filter(is_active=True),
        'processes': WeighingProcess.objects.filter(is_active=True),
    }
    
    return render(request, 'scale/weighing_record_edit.html', context)


@login_required
@user_passes_test(is_admin)
def weighing_record_delete(request, pk):
    record = get_object_or_404(WeighingRecord, pk=pk)
    
    if request.method == 'POST':
        record_id = record.id
        record.delete()
        messages.success(request, f'Weighing record #{record_id} deleted successfully.')
        return redirect('scale:weighing_record_list')
    
    # If not POST, redirect to detail page
    return redirect('scale:weighing_record_detail', pk=pk)


@login_required
@user_passes_test(is_admin)
def weighing_record_bulk_delete(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            record_ids = data.get('record_ids', [])
            password = data.get('password', '')
            
            if not record_ids:
                return JsonResponse({'success': False, 'message': 'No records selected.'})
                
            if not password:
                return JsonResponse({'success': False, 'message': 'Password is required.'})
                
            # Verify the user's password using the check_password method
            if not request.user.check_password(password):
                return JsonResponse({'success': False, 'message': 'Incorrect password.'})
                
            # Perform deletion
            deleted_count, _ = WeighingRecord.objects.filter(id__in=record_ids).delete()
            return JsonResponse({'success': True, 'message': f'Successfully deleted {deleted_count} records.'})
            
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'message': 'Invalid JSON data.'})
        except Exception as e:
            return JsonResponse({'success': False, 'message': f'Error details: {str(e)}'})
            
    return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=405)


#################################################################################################
# Export Weighing Records
@login_required
@user_passes_test(is_admin)
def export_weighing_records(request):
    # Get filter parameters from request (same as in weighing_record_list)
    scale_id = request.GET.get('scale')
    product_id = request.GET.get('product')
    process_id = request.GET.get('process')
    barcode = request.GET.get('barcode')
    sync_status = request.GET.get('sync_status')
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    sort_param = request.GET.get('sort', '-timestamp')
    export_format = request.GET.get('format', 'csv')
    
    # Start with all records
    records = WeighingRecord.objects.all()
    
    # Apply filters if provided
    if scale_id:
        records = records.filter(scale_id=scale_id)
    
    if product_id:
        records = records.filter(product_id=product_id)
    
    if process_id:
        records = records.filter(process_id=process_id)
    
    if barcode:
        records = records.filter(barcode__icontains=barcode)
    
    if sync_status == 'unsynced':
        records = records.filter(is_synced=False)
    elif sync_status == 'synced':
        records = records.filter(is_synced=True)
    
    # Date range filtering
    if date_from:
        date_from_obj = parse_date(date_from)
        if date_from_obj:
            date_from_datetime = datetime.combine(date_from_obj, time.min)
            records = records.filter(timestamp__gte=date_from_datetime)
    
    if date_to:
        date_to_obj = parse_date(date_to)
        if date_to_obj:
            date_to_datetime = datetime.combine(date_to_obj, time.max)
            records = records.filter(timestamp__lte=date_to_datetime)
    
    # Apply sorting
    if sort_param:
        records = records.order_by(sort_param)
    
    # Process export based on format
    if export_format == 'csv':
        return export_records_to_csv(records)
    elif export_format == 'excel':
        return export_records_to_excel(records)
    elif export_format == 'pdf':
        return export_records_to_pdf(records)
    else:
        messages.error(request, f"Unsupported export format: {export_format}")
        return redirect('scale:weighing_record_list')


def export_records_to_csv(records):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="weighing_records.csv"'
    
    writer = csv.writer(response)
    writer.writerow(['ID', 'Timestamp', 'Barcode', 'Scale', 'Product', 'Process', 'Gross Weight', 
                    'Tare Weight', 'Net Weight', 'Unit', 'Recorded By', 'Notes', 'Delivery Note'])
    
    for record in records:
        writer.writerow([
            record.id,
            record.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            record.barcode,
            record.scale.name,
            record.product.name,
            record.process.name,
            record.gross_weight,
            record.tare_weight,
            record.net_weight,
            record.unit_of_measure,
            f"{record.user.first_name} {record.user.last_name}",
            record.notes,
            record.delivery_note.delivery_note_number if record.delivery_note else ''
        ])
    
    return response


def export_records_to_excel(records):
    import xlwt
    
    response = HttpResponse(content_type='application/ms-excel')
    response['Content-Disposition'] = 'attachment; filename="weighing_records.xls"'
    
    wb = xlwt.Workbook(encoding='utf-8')
    ws = wb.add_sheet('Weighing Records')
    
    # Sheet header, first row
    row_num = 0
    
    font_style = xlwt.XFStyle()
    font_style.font.bold = True
    
    columns = ['ID', 'Timestamp', 'Barcode', 'Scale', 'Product', 'Process', 'Gross Weight', 
              'Tare Weight', 'Net Weight', 'Unit', 'Recorded By', 'Notes', 'Delivery Note']
    
    for col_num in range(len(columns)):
        ws.write(row_num, col_num, columns[col_num], font_style)
    
    # Sheet body, remaining rows
    font_style = xlwt.XFStyle()
    
    for record in records:
        row_num += 1
        ws.write(row_num, 0, record.id, font_style)
        ws.write(row_num, 1, record.timestamp.strftime('%Y-%m-%d %H:%M:%S'), font_style)
        ws.write(row_num, 2, record.barcode, font_style)
        ws.write(row_num, 3, record.scale.name, font_style)
        ws.write(row_num, 4, record.product.name, font_style)
        ws.write(row_num, 5, record.process.name, font_style)
        ws.write(row_num, 6, float(record.gross_weight), font_style)
        ws.write(row_num, 7, float(record.tare_weight), font_style)
        ws.write(row_num, 8, float(record.net_weight), font_style)
        ws.write(row_num, 9, record.unit_of_measure, font_style)
        ws.write(row_num, 10, f"{record.user.first_name} {record.user.last_name}", font_style)
        ws.write(row_num, 11, record.notes, font_style)
        ws.write(row_num, 12, record.delivery_note.delivery_note_number if record.delivery_note else '', font_style)
    
    wb.save(response)
    return response


def export_records_to_pdf(records):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
    from reportlab.lib.styles import getSampleStyleSheet
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter))
    elements = []
    
    # Add title
    styles = getSampleStyleSheet()
    elements.append(Paragraph("Weighing Records", styles['Title']))
    elements.append(Paragraph(f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles['Normal']))
    
    # Table data
    data = [['ID', 'Timestamp', 'Barcode', 'Scale', 'Product', 'Process', 'Gross', 
            'Tare', 'Net', 'Unit', 'Recorded By', 'Delivery Note']]
    
    for record in records:
        data.append([
            str(record.id),
            record.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            record.barcode,
            record.scale.name,
            record.product.name,
            record.process.name,
            str(record.gross_weight),
            str(record.tare_weight),
            str(record.net_weight),
            record.unit_of_measure,
            f"{record.user.first_name} {record.user.last_name}",
            record.delivery_note.delivery_note_number if record.delivery_note else ''
        ])
    
    # Create the table
    table = Table(data)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
        ('ALIGN', (0, 1), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    
    # Add the table to elements
    elements.append(table)
    
    # Build the PDF
    doc.build(elements)
    
    # Get the value of the buffer
    pdf = buffer.getvalue()
    buffer.close()
    
    # Create the HTTP response
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="weighing_records.pdf"'
    response.write(pdf)
    
    return response


@login_required
def lookup_grower(request):
    grower_number = request.GET.get('grower_number')
    if not grower_number:
        return JsonResponse({'success': False, 'message': 'No grower number provided'})
    
    # improved lookup: find the most recent PrintingNote with this grower number
    # that has a non-empty first_name or last_name
    note = PrintingNote.objects.filter(
        grower_number=grower_number
    ).exclude(
        first_name='', last_name=''
    ).order_by('-created_at').first()
    
    if note:
        return JsonResponse({
            'success': True,
            'first_name': note.first_name,
            'last_name': note.last_name
        })
    else:
        return JsonResponse({
            'success': False, 
            'message': 'Grower not found',
            'first_name': '',
            'last_name': ''
        })
