from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('files', '0004_remove_filerecord_upload_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='filerecord',
            name='integrity_established',
            field=models.BooleanField(default=False),
        ),
    ]
