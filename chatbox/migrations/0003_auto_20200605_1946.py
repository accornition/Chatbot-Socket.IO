# Generated by Django 2.2.12 on 2020-06-05 14:16

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chatbox', '0002_auto_20200605_1643'),
    ]

    operations = [
        migrations.AlterField(
            model_name='chatroom',
            name='uuid',
            field=models.CharField(max_length=255, primary_key=True, serialize=False),
        ),
    ]
