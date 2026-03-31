"""
Weighing process management views for IntelliScale.
Handles weighing process CRUD operations.
"""
from django.db import transaction
from django.utils.http import url_has_allowed_host_and_scheme
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from users.views import is_admin
from ..models import WeighingProcess
from ..forms import WeighingProcessForm


@login_required
@user_passes_test(is_admin)
def weighing_process_list(request):
    weighing_processes = WeighingProcess.objects.all().order_by('name')
    return render(request, 'scale/weighing_process_list.html', {'weighing_processes': weighing_processes})


@login_required
@user_passes_test(is_admin)
def weighing_process_detail(request, pk):
    weighing_process = get_object_or_404(WeighingProcess, pk=pk)
    return render(request, 'scale/weighing_process_detail.html', {'weighing_process': weighing_process})


@login_required
@user_passes_test(is_admin)
def weighing_process_create(request):
    if request.method == 'POST':
        form = WeighingProcessForm(request.POST)
        if form.is_valid():
            weighing_process = form.save()
            messages.success(request, f'Weighing process {weighing_process.name} was created successfully.')
            return redirect('scale:weighing_process_list')
    else:
        form = WeighingProcessForm()
    
    return render(request, 'scale/weighing_process_create.html', {'form': form})


@login_required
@user_passes_test(is_admin)
def weighing_process_edit(request, pk):
    weighing_process = get_object_or_404(WeighingProcess, pk=pk)
    
    if request.method == 'POST':
        form = WeighingProcessForm(request.POST, instance=weighing_process)
        if form.is_valid():
            weighing_process = form.save()
            messages.success(request, f'Weighing process {weighing_process.name} was updated successfully.')
            return redirect('scale:weighing_process_detail', pk=weighing_process.pk)
    else:
        form = WeighingProcessForm(instance=weighing_process)
    
    return render(request, 'scale/weighing_process_edit.html', {'form': form, 'weighing_process': weighing_process})


@login_required
@user_passes_test(is_admin)
def weighing_process_delete(request, pk):
    weighing_process = get_object_or_404(WeighingProcess, pk=pk)
    
    if request.method == 'POST':
        name = weighing_process.name
        weighing_process.delete()
        messages.success(request, f'Weighing process {name} was deleted successfully.')
        return redirect('scale:weighing_process_list')
    
    # If not POST, redirect to detail page
    return redirect('scale:weighing_process_detail', pk=pk)


@login_required
@user_passes_test(is_admin)
def toggle_weighing_process_active(request, pk):
    if request.method != 'POST':
        return redirect('scale:weighing_process_detail', pk=pk)

    weighing_process = get_object_or_404(WeighingProcess, pk=pk)

    with transaction.atomic():
        if weighing_process.is_active:
            weighing_process.is_active = False
            weighing_process.save()
            messages.success(request, f'Weighing process {weighing_process.name} was deactivated successfully.')
        else:
            WeighingProcess.objects.filter(is_active=True).exclude(pk=weighing_process.pk).update(is_active=False)
            weighing_process.is_active = True
            weighing_process.save()
            messages.success(request, f'Weighing process {weighing_process.name} was activated successfully.')

    next_url = request.POST.get('next')
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)

    return redirect('scale:weighing_process_detail', pk=pk)
