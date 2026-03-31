from django.test import TestCase
from django.urls import reverse

from .forms import WeighingProcessForm
from .models import WeighingProcess
from users.models import CustomUser


class WeighingProcessFormTests(TestCase):
    def test_saving_active_process_deactivates_existing_active_process(self):
        current_active = WeighingProcess.objects.create(
            name="Current Active",
            is_active=True,
        )
        inactive_process = WeighingProcess.objects.create(
            name="Inactive Process",
            is_active=False,
        )

        form = WeighingProcessForm(
            data={
                "name": "Inactive Process",
                "process_type": "WeighBridge",
                "is_active": "on",
            },
            instance=inactive_process,
        )

        self.assertTrue(form.is_valid(), form.errors)

        saved_process = form.save()

        current_active.refresh_from_db()
        saved_process.refresh_from_db()

        self.assertFalse(current_active.is_active)
        self.assertTrue(saved_process.is_active)


class WeighingProcessToggleViewTests(TestCase):
    def setUp(self):
        self.admin_user = CustomUser.objects.create_user(
            username="admin",
            password="password123",
            role="admin",
        )

    def test_toggle_active_process_deactivates_it(self):
        process = WeighingProcess.objects.create(
            name="Active Process",
            is_active=True,
        )

        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse("scale:weighing_process_toggle_active", args=[process.pk]),
            {"next": reverse("scale:weighing_process_detail", args=[process.pk])},
        )

        self.assertEqual(response.status_code, 302)
        process.refresh_from_db()
        self.assertFalse(process.is_active)

    def test_toggle_inactive_process_activates_it_and_deactivates_existing_active_process(self):
        current_active = WeighingProcess.objects.create(
            name="Current Active",
            is_active=True,
        )
        process = WeighingProcess.objects.create(
            name="Inactive Process",
            is_active=False,
        )

        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse("scale:weighing_process_toggle_active", args=[process.pk]),
            {"next": reverse("scale:weighing_process_list")},
        )

        self.assertEqual(response.status_code, 302)
        current_active.refresh_from_db()
        process.refresh_from_db()

        self.assertFalse(current_active.is_active)
        self.assertTrue(process.is_active)
