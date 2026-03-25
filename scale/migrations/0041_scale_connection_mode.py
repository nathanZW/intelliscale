from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scale', '0040_weighingprocess_send_simple_request'),
    ]

    operations = [
        migrations.AddField(
            model_name='scale',
            name='connection_mode',
            field=models.CharField(
                choices=[('default', 'Default'), ('mettler', 'Mettler Toledo'), ('autodetect', 'Auto Detect')],
                default='default',
                help_text='Connection method: Default, Mettler Toledo, or Auto Detect',
                max_length=20,
            ),
        ),
    ]
