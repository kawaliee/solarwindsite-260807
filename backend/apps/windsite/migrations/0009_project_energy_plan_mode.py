"""
사업에 에너지원을, 배치안에 검토 방식을 붙인다.

풍력 배치안과 태양광 후보 필지가 한 목록에 섞이면 무엇을 견주는 것인지
알 수 없다. 화면은 카테고리별로 갈라져 있으므로 저장 목록도 가른다.

기존 레코드는 전부 풍력 배치선이므로 기본값을 그대로 둔다.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('windsite', '0008_energy_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='siteproject',
            name='energy_type',
            field=models.CharField(
                choices=[('WIND', '풍력'), ('SOLAR', '태양광'), ('ALL', '공통')],
                default='WIND', max_length=10, verbose_name='에너지원'),
        ),
        migrations.AddField(
            model_name='siteplan',
            name='mode',
            field=models.CharField(
                choices=[('layout', '배치선'), ('area', '구역'), ('parcel', '필지')],
                default='layout', max_length=10, verbose_name='검토 방식'),
        ),
    ]
