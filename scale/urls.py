from django.urls import path
from users import views as user_views
from . import views

app_name = 'scale'

urlpatterns = [
    # Dashboard URLs from users views
    path('dashboard/admin/', user_views.admin_dashboard, name='admin_dashboard'),
    path('dashboard/manager/', user_views.manager_dashboard, name='manager_dashboard'),
    path('dashboard/operator/', user_views.operator_dashboard, name='operator_dashboard'),
    path('dashboard/default/', user_views.default_dashboard, name='default_dashboard'),
    
    # Scale management URLs
    path('scales/', views.scale_list, name='scale_list'),
    path('scales/create/', views.scale_create, name='scale_create'),
    path('scales/<int:pk>/', views.scale_detail, name='scale_detail'),
    path('scales/<int:pk>/edit/', views.scale_edit, name='scale_edit'),
    path('scales/<int:pk>/delete/', views.scale_delete, name='scale_delete'),
    path('scales/<int:scale_id>/connect/', views.connect_scale_view, name='connect_scale'),
    
    # Weighing Record URLs
    path('weighing-records/', views.weighing_record_list, name='weighing_record_list'),
    path('weighing-records/<int:pk>/', views.weighing_record_detail, name='weighing_record_detail'),
    path('weighing-records/<int:pk>/print/', views.print_weighing_record, name='print_weighing_record'),
    path('weighing-records/<int:pk>/edit/', views.weighing_record_edit, name='weighing_record_edit'),
    path('weighing-records/<int:pk>/delete/', views.weighing_record_delete, name='weighing_record_delete'),
    path('weighing-records/export/', views.export_weighing_records, name='export_weighing_records'),
    
    # Weighing Process URLs
    path('weighing-processes/', views.weighing_process_list, name='weighing_process_list'),
    path('weighing-processes/create/', views.weighing_process_create, name='weighing_process_create'),
    path('weighing-processes/<int:pk>/', views.weighing_process_detail, name='weighing_process_detail'),
    path('weighing-processes/<int:pk>/edit/', views.weighing_process_edit, name='weighing_process_edit'),
    path('weighing-processes/<int:pk>/delete/', views.weighing_process_delete, name='weighing_process_delete'),
    
    # Product Management URLs
    path('products/', views.product_list, name='product_list'),
    path('products/create/', views.product_create, name='product_create'),
    path('products/<int:pk>/', views.product_detail, name='product_detail'),
    path('products/<int:pk>/edit/', views.product_edit, name='product_edit'),
    path('products/<int:pk>/delete/', views.product_delete, name='product_delete'),
    
    # Delivery Note Management URLs
    path('delivery-notes/', views.delivery_note_list, name='delivery_note_list'),
    path('delivery-notes/create/', views.delivery_note_create, name='delivery_note_create'),
    path('delivery-notes/<int:pk>/', views.delivery_note_detail, name='delivery_note_detail'),
    path('delivery-notes/<int:pk>/edit/', views.delivery_note_edit, name='delivery_note_edit'),
    path('delivery-notes/<int:pk>/delete/', views.delivery_note_delete, name='delivery_note_delete'),
    path('delivery-notes/<int:pk>/close/', views.close_delivery_note, name='close_delivery_note'),
    path('delivery-notes/<int:pk>/close-commercial/', views.close_commercial_delivery_note, name='close_commercial_delivery_note'),
    path('delivery-notes/<int:pk>/deactivate/', views.deactivate_active_delivery_note, name='deactivate_delivery_note'),
    path('delivery-notes/<int:pk>/suspend/', views.delivery_note_suspend, name='delivery_note_suspend'),
    path('delivery-note/<int:pk>/recall-bale/', views.delivery_note_bale_recall, name='delivery_note_bale_recall'),
    path('delivery-note/<int:pk>/recall/', views.recall_delivery_note, name='recall_delivery_note'),
    path('delivery-note/bale/<int:pk>/recall/', views.recall_bale, name='recall_bale'),
    path('delivery-notes/<int:pk>/print/', views.print_delivery_note, name='print_delivery_note'),
    path('delivery-notes/<int:delivery_note_id>/record/', views.get_delivery_note_record, name='get_delivery_note_record'),
    path('delivery-notes/find-by-barcode/', views.find_delivery_note_by_barcode, name='find_delivery_note_by_barcode'),
    path('delivery-notes/search/', views.search_delivery_notes, name='search_delivery_notes'),
    path('delivery-notes/<int:pk>/activate/', views.activate_delivery_note, name='activate_delivery_note'),
    path('delivery-notes/sync/', views.manual_sync_delivery_notes, name='manual_sync_delivery_notes'),
    
    # Weighing Station URLs
    path('weighing-station/', views.weighing_station, name='weighing_station'),
    path('printing-station/', views.printing_station, name='printing_station'),
    path('printing-station/notes/', views.printing_note_list, name='printing_note_list'),
    path('printing-station/notes/<int:pk>/', views.printing_note_detail, name='printing_note_detail'),
    path('printing-station/records/<int:pk>/delete/', views.printing_record_delete, name='printing_record_delete'),
    path('printing-station/notes/<int:pk>/export/', views.printing_note_export_xlsx, name='printing_note_export_xlsx'),
    path('weighing-station/recall-update-bale/', views.recall_and_update_bale, name='recall_and_update_bale'),
    path('scales/<int:scale_id>/get-weight/', views.get_weight, name='get_weight'),
    path('weighing-records/sync-all-unsynced/', views.sync_all_unsynced, name='sync_all_unsynced'),
    path('api/scales/<str:scale_id>/weight/', views.get_current_weight_api, name='get_current_weight_api'),
    
    # Company Settings URL
    path('company-settings/', views.company_settings, name='company_settings'),
    path('configuration/import-export/', views.config_import_export, name='config_import_export'),
    path('configuration/export/', views.config_export, name='config_export'),
    
    # Driver Management URLs
    path('drivers/create-ajax/', views.driver_create_ajax, name='driver_create_ajax'),
    path('trucks/create-ajax/', views.truck_create_ajax, name='truck_create_ajax'),
    path('trailers/create-ajax/', views.trailer_create_ajax, name='trailer_create_ajax'),
    path('delivery-notes/create-ajax/', views.delivery_note_create_ajax, name='delivery_note_create_ajax'),
] 