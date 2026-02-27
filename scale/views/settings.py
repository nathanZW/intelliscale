"""
Company settings and configuration views for IntelliScale.
Handles company settings, config import/export, and data management.
"""
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.http import HttpResponse
from django.utils import timezone
from users.views import is_admin
from ..models import (
    CompanySettings, Product, WeighingProcess, ErpSystem, 
    DeliveryNote, WeighingRecord
)
from ..forms import CompanySettingsForm
import json


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
    """Handle data management operations including bulk delete of delivery notes and weighing records"""
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'delete_delivery_notes':
            try:
                # Delete all delivery notes (weighing records will cascade delete based on model settings)
                count = DeliveryNote.objects.count()
                DeliveryNote.objects.all().delete()
                messages.success(request, f'Successfully deleted {count} delivery notes.')
            except Exception as e:
                messages.error(request, f'Error deleting delivery notes: {str(e)}')
        
        elif action == 'delete_weighing_records':
            try:
                # Catches all weighing recordes that are not attached to a delivery note.
                # This happens in cases where a database is restored and a record is left orphaned.
                count = WeighingRecord.objects.count()
                WeighingRecord.objects.all().delete()
                messages.success(request, f'Successfully deleted {count} weighing records.')
            except Exception as e:
                messages.error(request, f'Error deleting weighing records: {str(e)}')
        
        return redirect('scale:data_management')
    
    return render(request, 'scale/data_management.html')
