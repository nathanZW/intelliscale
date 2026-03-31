from django.db import migrations, models


def copy_legacy_mettler_flag_to_protocol(apps, schema_editor):
    Scale = apps.get_model('scale', 'Scale')
    Scale.objects.filter(mettler_toledo=True).update(protocol='mettler_toledo')


def copy_protocol_back_to_legacy_flag(apps, schema_editor):
    Scale = apps.get_model('scale', 'Scale')
    Scale.objects.update(mettler_toledo=False)
    Scale.objects.filter(protocol='mettler_toledo').update(mettler_toledo=True)


class Migration(migrations.Migration):

    dependencies = [
        ('scale', '0040_weighingprocess_send_simple_request'),
    ]

    operations = [
        migrations.AddField(
            model_name='scale',
            name='protocol',
            field=models.CharField(
                choices=[
                    ('generic', 'Generic'),
                    ('mettler_toledo', 'Mettler Toledo'),
                    ('cas_stream', 'CAS Stream'),
                ],
                default='generic',
                help_text='Select the serial protocol used by this scale.',
                max_length=32,
            ),
        ),
        migrations.RunPython(
            copy_legacy_mettler_flag_to_protocol,
            copy_protocol_back_to_legacy_flag,
        ),
    ]
