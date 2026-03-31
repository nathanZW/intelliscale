from django.contrib import admin
from .models import Scale, WeighingProcess, Product, DeliveryNote, WeighingRecord, ErpSystem, CompanySettings, Driver, Truck, Trailer


class ScaleAdmin(admin.ModelAdmin):
    list_display = ('name', 'manufacturer', 'model_number', 'protocol', 'is_active', 'last_connection_status', 'last_seen')
    list_filter = ('protocol', 'is_active', 'last_connection_status')
    search_fields = ('name', 'manufacturer', 'model_number')
    list_editable = ('is_active', 'last_connection_status')
    list_per_page = 20

admin.site.register(Scale, ScaleAdmin)


class WeighingProcessAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'max_weight', 'min_weight', 'weight_rounding', 'allow_manual_entry')
    list_filter = ('is_active',)
    search_fields = ('name',)
    list_editable = ('is_active', 'max_weight', 'min_weight', 'weight_rounding', 'allow_manual_entry')
    list_per_page = 20

admin.site.register(WeighingProcess, WeighingProcessAdmin)


class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'erp_product_id', 'is_active', 'created_at', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'erp_product_id', 'description')
    list_editable = ('is_active',)
    list_per_page = 20

admin.site.register(Product, ProductAdmin)


class DeliveryNoteAdmin(admin.ModelAdmin):
    list_display = ('delivery_note_number', 'created_by', 'status', 'is_synced', 'created_at', 'updated_at')
    list_filter = ('is_synced', 'status')
    search_fields = ('delivery_note_number', 'created_by__username')
    list_per_page = 20

admin.site.register(DeliveryNote, DeliveryNoteAdmin)


class WeighingRecordAdmin(admin.ModelAdmin):
    list_display = ('process', 'product', 'gross_weight', 'tare_weight', 'net_weight', 'created_at', 'updated_at')
    list_filter = ('process', 'product', 'is_synced')
    search_fields = ('process__name', 'product__name')
    list_per_page = 20

admin.site.register(WeighingRecord, WeighingRecordAdmin)

class ErpSystemAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at', 'updated_at')
    search_fields = ('name',)
    list_per_page = 20

admin.site.register(ErpSystem, ErpSystemAdmin)

class CompanySettingsAdmin(admin.ModelAdmin):
    list_display = ('id', 'company_name', 'erp_system', 'is_active', 'created_at', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('company_name', 'erp_system__name')
    list_per_page = 20

admin.site.register(CompanySettings, CompanySettingsAdmin)



class DriverAdmin(admin.ModelAdmin):
    list_display = ('name', 'phone', 'created_at', 'updated_at')
    search_fields = ('name', 'phone')
    list_per_page = 20
    
admin.site.register(Driver, DriverAdmin)


class TruckAdmin(admin.ModelAdmin):
    list_display = ('brand', 'license_plate', 'color', 'created_at', 'updated_at')
    search_fields = ('brand', 'license_plate', 'color')
    list_per_page = 20

admin.site.register(Truck, TruckAdmin)


class TrailerAdmin(admin.ModelAdmin):
    list_display = ('brand', 'license_plate', 'color', 'created_at', 'updated_at')
    search_fields = ('brand', 'license_plate', 'color')
    list_per_page = 20
    
admin.site.register(Trailer, TrailerAdmin)
