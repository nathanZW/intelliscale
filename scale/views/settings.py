"""
Company settings and configuration views for IntelliScale.
Handles company settings, config import/export, and data management.
"""
from collections import deque
from pathlib import Path
import re

from django.conf import settings
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from users.views import is_admin
from ..models import (
    CompanySettings, Product, WeighingProcess, ErpSystem, 
    DeliveryNote, WeighingRecord
)
from ..forms import CompanySettingsForm
import json


LOG_FILE_CHOICES = {
    'all': ('all', 'All Logs'),
    'requests': ('requests.log', 'Web Requests'),
    'api': ('api.log', 'API Traffic'),
    'database': ('database.log', 'Database'),
    'app': ('app.log', 'Application'),
}
LOG_LEVELS = ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')
TIMESTAMP_PATTERN = re.compile(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})')
LEVEL_PATTERN = re.compile(r'\b(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b')


def _coerce_line_limit(raw_value, default=200, minimum=50, maximum=1000):
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(value, maximum))


def _tail_lines(path, max_lines):
    with path.open('r', encoding='utf-8', errors='replace') as handle:
        return list(deque(handle, maxlen=max_lines))


def _parse_log_line(source_key, source_label, line):
    cleaned = line.rstrip()
    if not cleaned:
        return None

    timestamp_match = TIMESTAMP_PATTERN.search(cleaned)
    level_match = LEVEL_PATTERN.search(cleaned)

    return {
        'source': source_key,
        'source_label': source_label,
        'timestamp': timestamp_match.group(1) if timestamp_match else '',
        'level': level_match.group(1) if level_match else 'UNKNOWN',
        'message': cleaned,
    }


def _iter_selected_logs(selected_log):
    if selected_log == 'all':
        for source_key in ('requests', 'api', 'database', 'app'):
            yield source_key, LOG_FILE_CHOICES[source_key][1], settings.LOG_DIR / LOG_FILE_CHOICES[source_key][0]
        return

    log_details = LOG_FILE_CHOICES.get(selected_log)
    if not log_details or selected_log == 'all':
        return

    yield selected_log, log_details[1], settings.LOG_DIR / log_details[0]


def _load_log_entries(selected_log, selected_level, search_text, line_limit):
    entries = []
    missing_logs = []
    per_file_limit = line_limit if selected_log != 'all' else min(line_limit * 3, 3000)
    search_text = (search_text or '').strip().lower()

    for source_key, source_label, path in _iter_selected_logs(selected_log):
        if not path.exists():
            missing_logs.append({'source': source_key, 'label': source_label, 'path': str(path)})
            continue

        for line in _tail_lines(Path(path), per_file_limit):
            entry = _parse_log_line(source_key, source_label, line)
            if not entry:
                continue
            if selected_level != 'all' and entry['level'] != selected_level:
                continue
            if search_text and search_text not in entry['message'].lower():
                continue
            entries.append(entry)

    entries.sort(key=lambda item: item['timestamp'], reverse=True)
    return entries[:line_limit], missing_logs


def _clear_selected_logs(selected_log):
    cleared_files = []
    missing_logs = []

    for source_key, source_label, path in _iter_selected_logs(selected_log):
        log_path = Path(path)
        if not log_path.exists():
            missing_logs.append({'source': source_key, 'label': source_label, 'path': str(log_path)})
            continue

        log_path.write_text('', encoding='utf-8')
        cleared_files.append({'source': source_key, 'label': source_label, 'path': str(log_path)})

    return cleared_files, missing_logs


@login_required
@user_passes_test(is_admin)
def company_settings(request):
    # Get the first company settings record or None
    company_settings_obj = CompanySettings.objects.first()
    
    if request.method == 'POST':
        form = CompanySettingsForm(request.POST, instance=company_settings_obj)
        if form.is_valid():
            form.save()
            messages.success(request, 'Company settings saved successfully.')
            return redirect('scale:company_settings')
    else:
        form = CompanySettingsForm(instance=company_settings_obj)
    
    return render(request, 'scale/company_settings.html', {
        'form': form,
        'company_settings': company_settings_obj
    })


@login_required
@user_passes_test(is_admin)
def config_import_export(request):
    if request.method == 'POST':
        if 'config_file' not in request.FILES:
            messages.error(request, 'No file selected.')
            return redirect('scale:config_import_export')
            
        config_file = request.FILES['config_file']
        try:
            data = json.load(config_file)
            
            # Import Products
            products_count = 0
            if 'products' in data:
                for item in data['products']:
                    Product.objects.update_or_create(
                        name=item['name'],
                        defaults={
                            'erp_product_id': item.get('erp_product_id', ''),
                            'description': item.get('description', ''),
                            'is_active': item.get('is_active', False),
                            'tare_weight': item.get('tare_weight', 0),
                        }
                    )
                    products_count += 1

            # Import Weighing Processes
            processes_count = 0
            if 'weighing_processes' in data:
                for item in data['weighing_processes']:
                    # Convert rolling_hessian to boolean if needed
                    rolling_hessian = item.get('rolling_hessian', False)
                    if isinstance(rolling_hessian, str) and rolling_hessian.lower() == 'true':
                           rolling_hessian = True
                    elif isinstance(rolling_hessian, str):
                           rolling_hessian = False

                    WeighingProcess.objects.update_or_create(
                        name=item['name'],
                        defaults={
                            'description': item.get('description', ''),
                            'custom_fields_schema': item.get('custom_fields_schema', []),
                            'erp_target_model': item.get('erp_target_model', ''),
                            'is_active': item.get('is_active', False),
                            'max_weight': item.get('max_weight'),
                            'min_weight': item.get('min_weight'),
                            'weight_rounding': item.get('weight_rounding', 2),
                            'allow_manual_entry': item.get('allow_manual_entry', False),
                            'process_type': item.get('process_type', 'WeighBridge'),
                            'allow_marshalling': item.get('allow_marshalling', False),
                            'allow_bale_insert': item.get('allow_bale_insert', False),
                            'allow_spaces_in_barcode': item.get('allow_spaces_in_barcode', False),
                            'use_code39_mod43_validation': item.get('use_code39_mod43_validation', False),
                            'rolling_hessian': rolling_hessian,
                        }
                    )
                    processes_count += 1
            
            # Import Company Settings
            settings_updated = False
            if 'company_settings' in data:
                for item in data['company_settings']:
                    # Assuming we match by company name or we just have one record.
                    # Let's try to find an ERP system by name first or create it
                    erp_system_name = item.get('erp_system_name', 'Default')
                    erp_system, _ = ErpSystem.objects.get_or_create(name=erp_system_name)
                    
                    CompanySettings.objects.update_or_create(
                        company_name=item['company_name'],
                        defaults={
                            'erp_system': erp_system,
                            'api_key': item.get('api_key'),
                            'erp_username': item.get('erp_username'),
                            'erp_password': item.get('erp_password'),
                            'api_url': item.get('api_url'),
                            'database_name': item.get('database_name'),
                            'satellite': item.get('satellite', False),
                            'is_active': item.get('is_active', True),
                        }
                    )
                    settings_updated = True

            messages.success(request, f'Configuration imported successfully! {products_count} products, {processes_count} processes updated/created.')
            
        except json.JSONDecodeError:
            messages.error(request, 'Invalid JSON file.')
        except Exception as e:
            messages.error(request, f'Error during import: {str(e)}')
            
        return redirect('scale:config_import_export')
        
    return render(request, 'scale/config_import_export.html')


@login_required
@user_passes_test(is_admin)
def config_export(request):
    if request.method == 'POST':
        data = {
            'products': [],
            'weighing_processes': [],
            'company_settings': []
        }
        
        # Export Products
        for obj in Product.objects.all():
            data['products'].append({
                'name': obj.name,
                'erp_product_id': obj.erp_product_id,
                'description': obj.description,
                'is_active': obj.is_active,
                'tare_weight': float(obj.tare_weight) if obj.tare_weight else 0
            })
            
        # Export Weighing Processes
        for obj in WeighingProcess.objects.all():
            data['weighing_processes'].append({
                'name': obj.name,
                'description': obj.description,
                'custom_fields_schema': obj.custom_fields_schema,
                'erp_target_model': obj.erp_target_model,
                'is_active': obj.is_active,
                'max_weight': float(obj.max_weight) if obj.max_weight else None,
                'min_weight': float(obj.min_weight) if obj.min_weight else None,
                'weight_rounding': obj.weight_rounding,
                'allow_manual_entry': obj.allow_manual_entry,
                'process_type': obj.process_type,
                'allow_marshalling': obj.allow_marshalling,
                'allow_bale_insert': obj.allow_bale_insert,
                'allow_spaces_in_barcode': obj.allow_spaces_in_barcode,
                'use_code39_mod43_validation': obj.use_code39_mod43_validation,
                'rolling_hessian': obj.rolling_hessian,
            })
            
        # Export Company Settings
        for obj in CompanySettings.objects.all():
            data['company_settings'].append({
                'company_name': obj.company_name,
                'erp_system_name': obj.erp_system.name if obj.erp_system else 'Default',
                'api_key': obj.api_key,
                'erp_username': obj.erp_username,
                'erp_password': obj.erp_password,
                'api_url': obj.api_url,
                'database_name': obj.database_name,
                'satellite': obj.satellite,
                'is_active': obj.is_active
            })
            
        response = HttpResponse(
            json.dumps(data, indent=4),
            content_type='application/json'
        )
        timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
        response['Content-Disposition'] = f'attachment; filename="intelliscale_config_{timestamp}.json"'
        return response
        
    return redirect('scale:config_import_export')


@login_required
@user_passes_test(is_admin)
def data_management(request):
    """Render data management page"""
    return render(request, 'scale/data_management.html')


@login_required
@user_passes_test(is_admin)
def log_viewer(request):
    selected_log = request.GET.get('log', 'all')
    if selected_log not in LOG_FILE_CHOICES:
        selected_log = 'all'

    selected_level = request.GET.get('level', 'all').upper()
    if selected_level != 'ALL' and selected_level not in LOG_LEVELS:
        selected_level = 'ALL'
    selected_level = selected_level.lower() if selected_level == 'ALL' else selected_level

    search_text = request.GET.get('q', '').strip()
    line_limit = _coerce_line_limit(request.GET.get('lines'))
    entries, missing_logs = _load_log_entries(
        selected_log=selected_log,
        selected_level=selected_level if selected_level != 'all' else 'all',
        search_text=search_text,
        line_limit=line_limit,
    )

    return render(
        request,
        'scale/log_viewer.html',
        {
            'entries': entries,
            'missing_logs': missing_logs,
            'selected_log': selected_log,
            'selected_level': selected_level,
            'search_text': search_text,
            'line_limit': line_limit,
            'log_options': [
                {'value': key, 'label': label}
                for key, (_, label) in LOG_FILE_CHOICES.items()
            ],
            'level_options': ['all', *LOG_LEVELS],
            'selected_log_label': LOG_FILE_CHOICES[selected_log][1],
        },
    )


@login_required
@user_passes_test(is_admin)
def clear_log_data(request):
    """AJAX endpoint to clear selected log files with password verification."""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            password = data.get('password', '')
            selected_log = data.get('log', 'all')

            if not password:
                return JsonResponse({'success': False, 'message': 'Password is required.'})

            if not request.user.check_password(password):
                return JsonResponse({'success': False, 'message': 'Incorrect password.'})

            if selected_log not in LOG_FILE_CHOICES:
                return JsonResponse({'success': False, 'message': 'Invalid log selection.'})

            cleared_files, missing_logs = _clear_selected_logs(selected_log)
            log_label = LOG_FILE_CHOICES[selected_log][1]

            if cleared_files:
                message = f'Cleared {len(cleared_files)} log file(s) for {log_label}.'
                if missing_logs:
                    message = f'{message} Some selected log files were not available.'
                return JsonResponse({'success': True, 'message': message})

            return JsonResponse({
                'success': False,
                'message': f'No log files were cleared for {log_label}.',
            })

        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'message': 'Invalid JSON data.'})
        except Exception as e:
            return JsonResponse({'success': False, 'message': f'Error details: {str(e)}'})

    return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=405)


@login_required
@user_passes_test(is_admin)
def delete_all_delivery_notes(request):
    """AJAX endpoint to delete all delivery notes with password verification"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            password = data.get('password', '')

            if not password:
                return JsonResponse({'success': False, 'message': 'Password is required.'})

            if not request.user.check_password(password):
                return JsonResponse({'success': False, 'message': 'Incorrect password.'})

            # Delete all delivery notes (weighing records will cascade delete based on model settings)
            count = DeliveryNote.objects.count()
            DeliveryNote.objects.all().delete()
            return JsonResponse({'success': True, 'message': f'Successfully deleted {count} delivery notes.'})

        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'message': 'Invalid JSON data.'})
        except Exception as e:
            return JsonResponse({'success': False, 'message': f'Error details: {str(e)}'})

    return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=405)


@login_required
@user_passes_test(is_admin)
def delete_all_weighing_records(request):
    """AJAX endpoint to delete all weighing records with password verification"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            password = data.get('password', '')

            if not password:
                return JsonResponse({'success': False, 'message': 'Password is required.'})

            if not request.user.check_password(password):
                return JsonResponse({'success': False, 'message': 'Incorrect password.'})

            # Delete all weighing records
            count = WeighingRecord.objects.count()
            WeighingRecord.objects.all().delete()
            return JsonResponse({'success': True, 'message': f'Successfully deleted {count} weighing records.'})

        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'message': 'Invalid JSON data.'})
        except Exception as e:
            return JsonResponse({'success': False, 'message': f'Error details: {str(e)}'})

    return JsonResponse({'success': False, 'message': 'Invalid request method.'}, status=405)
