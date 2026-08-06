from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0002_subscription_grace_period'),
    ]

    operations = [
        migrations.AddField(
            model_name='subscription',
            name='last_payment_failed_notified_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
