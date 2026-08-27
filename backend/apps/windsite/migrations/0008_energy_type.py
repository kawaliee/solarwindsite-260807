"""
에너지원 구분(energy_type)을 인허가 절차·법령·검토 이력에 붙인다.

이격거리 조례(LocalOrdinance)에는 처음부터 있었으나, 인허가 절차와 적용
법령은 육상풍력 기준으로만 시드돼 있었다. 태양광 검토가 그 목록을 그대로
받으면 발전사업허가 소관·용량 경계부터 어긋난 로드맵이 나온다.

기존 레코드는 전부 육상풍력 기준이므로 기본값을 'WIND'로 둔다 — 마이그레이션
시점에 이미 들어 있는 행이 그 값으로 채워진다. 양쪽 공통 절차는 이후 시드에서
'ALL'로 표시한다.
"""
from django.db import migrations, models


ENERGY_CHOICES = [('WIND', '풍력'), ('SOLAR', '태양광'), ('ALL', '공통')]


class Migration(migrations.Migration):

    dependencies = [
        ('windsite', '0007_siteproject_siteplan'),
    ]

    operations = [
        migrations.AddField(
            model_name='permitstep',
            name='energy_type',
            field=models.CharField(choices=ENERGY_CHOICES, default='WIND',
                                   max_length=10, verbose_name='에너지원'),
        ),
        migrations.AddField(
            model_name='lawreference',
            name='energy_type',
            field=models.CharField(choices=ENERGY_CHOICES, default='WIND',
                                   max_length=10, verbose_name='에너지원'),
        ),
        migrations.AddField(
            model_name='siteevaluation',
            name='energy_type',
            field=models.CharField(choices=ENERGY_CHOICES, default='WIND',
                                   max_length=10, verbose_name='에너지원'),
        ),
        migrations.AlterField(
            model_name='lawreference',
            name='purpose',
            field=models.CharField(blank=True, max_length=300,
                                   verbose_name='사업에서의 역할'),
        ),
    ]
