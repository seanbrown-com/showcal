from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('concerts', '0006_calendarsubscription')]

    operations = [
        # Drop dependent tables intact; removing their constrained foreign keys
        # first would require SQLite to rebuild an invalid intermediate table.
        migrations.DeleteModel(name='Alert'),
        migrations.DeleteModel(name='BrowserSnapshot'),
        migrations.DeleteModel(name='BrowserProviderState'),
        migrations.DeleteModel(name='Watch'),
        migrations.RemoveField(model_name='event', name='price_min'),
        migrations.RemoveField(model_name='event', name='currency'),
        migrations.RemoveField(model_name='profile', name='email_alerts'),
    ]
