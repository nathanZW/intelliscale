"""
Views package for IntelliScale scale application.
Re-exports all views from submodules for backwards compatibility with urls.py.
"""

# Scale management views
from .scale import (
    scale_list,
    scale_detail,
    scale_create,
    scale_edit,
    scale_delete,
    connect_scale_view,
    get_weight,
    get_current_weight_api,
)
from .weight_stream import stream_weight

# Weighing process views
from .weighing_process import (
    weighing_process_list,
    weighing_process_detail,
    weighing_process_create,
    weighing_process_edit,
    weighing_process_delete,
    toggle_weighing_process_active,
)

# Product views
from .product import (
    product_list,
    product_detail,
    product_create,
    product_edit,
    product_delete,
)

# Weighing station views
from .weighing_station import (
    weighing_station,
    sync_all_unsynced,
)

# Delivery note views
from .delivery_note import (
    delivery_note_list,
    delivery_note_detail,
    delivery_note_create,
    delivery_note_edit,
    delivery_note_delete,
    delivery_note_delete_weighing_records,
    generate_delivery_note_number,
    recall_delivery_note,
    delivery_note_suspend,
    manual_sync_delivery_notes,
    close_delivery_note,
    update_dnote_completion_status_with_api_key,
    deactivate_active_delivery_note,
    delivery_note_bale_recall,
    recall_bale,
    close_commercial_delivery_note,
    print_delivery_note,
    print_dispatch_note,
    get_delivery_note_record,
    find_delivery_note_by_barcode,
    search_delivery_notes,
    activate_delivery_note,
)

# Weighing record views
from .weighing_record import (
    weighing_record_list,
    weighing_record_detail,
    weighing_record_edit,
    weighing_record_delete,
    weighing_record_bulk_delete,
    print_weighing_record,
    export_weighing_records,
    export_records_to_csv,
    export_records_to_excel,
    export_records_to_pdf,
    lookup_grower,
)

# Company settings views
from .settings import (
    company_settings,
    config_import_export,
    config_export,
    data_management,
    log_viewer,
    clear_log_data,
    delete_all_delivery_notes,
    delete_all_weighing_records,
)

# Printing station views
from .printing import (
    printing_station,
    printing_note_list,
    printing_note_detail,
    printing_note_delete,
    reactivate_printing_note,
    update_printing_note_details,
    printing_record_delete,
    printing_note_export_xlsx,
)

# AJAX endpoint views
from .ajax import (
    driver_create_ajax,
    recall_and_update_bale,
    truck_create_ajax,
    trailer_create_ajax,
    delivery_note_create_ajax,
)
