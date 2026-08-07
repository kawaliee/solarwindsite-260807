"""
지자체 이격거리 조례 원문 대조
---------------------------------------------------------------
자치법규 OPEN API로 조례 원문을 받아 **풍력 이격거리 조항을 찾아내고**
DB(LocalOrdinance)의 값과 대조한다.

추출·반영 로직 자체는 `apps.windsite.ordinances` 서비스에 있다.
검토 실행 중 자동 수집(`ensure_ordinances`)과 같은 코드를 쓰기 위함이다.
이 커맨드는 대량 대조와 **사람이 읽는 diff 보고**를 담당한다.

사용
  python manage.py sync_ordinances                       # 전체 조례 대조·보고
  python manage.py sync_ordinances --sigungu 화순군 --apply
  python manage.py sync_ordinances --sigungu 영양군 --sido 경상북도 --apply  # 신규 등록
"""
from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from apps.windsite import lawapi, ordinances
from apps.windsite.models import LocalOrdinance


class Command(BaseCommand):
    help = '지자체 조례 원문을 대조해 풍력 이격거리를 검증합니다.'

    def add_arguments(self, parser):
        parser.add_argument('--sigungu', action='append', default=[],
                            help='대상 시·군·구 (미지정 시 DB에 있는 전체)')
        parser.add_argument('--sido', default='', help='신규 등록 시 사용할 시·도명')
        parser.add_argument('--apply', action='store_true', help='DB 반영')
        parser.add_argument('--sleep', type=float, default=0.5)

    # ------------------------------------------------------------------
    def handle(self, *args, **o):
        if lawapi.is_demo_account():
            self.stdout.write(self.style.WARNING(
                '공용 데모 계정(OC=test)으로 조회합니다. 운영에는 LAW_API_OC를 설정하십시오.'))

        sigungus = o['sigungu'] or sorted(
            set(LocalOrdinance.objects.values_list('sigungu', flat=True)))
        if not sigungus:
            self.stdout.write(self.style.WARNING('대상 지자체가 없습니다.'))
            return

        for sgg in sigungus:
            self.stdout.write(f'\n=== {sgg} ===')
            try:
                self._one(o['sido'], sgg, o['apply'])
            except Exception as e:                              # noqa: BLE001
                self.stdout.write(self.style.ERROR(f'  실패: {type(e).__name__}: {e}'))
            time.sleep(o['sleep'])

        if not o['apply']:
            self.stdout.write(self.style.WARNING(
                '\n대조만 수행했습니다. DB에 반영하려면 --apply 를 붙이십시오.'))

    # ------------------------------------------------------------------
    def _one(self, sido: str, sigungu: str, apply: bool) -> None:
        # 반영 전 DB 상태를 미리 떠둔다 (diff 출력용)
        current = {(r.target, r.target_detail): r
                   for r in LocalOrdinance.objects.filter(
                       sigungu=sigungu, energy_type__in=['WIND', 'ALL'])}

        res = ordinances.sync_sigungu(sido, sigungu, apply=apply)

        target = res['ordinance']
        if not target:
            names = ', '.join(h['name'] for h in res['search_hits'][:5]) or '없음'
            self.stdout.write(self.style.ERROR(
                f'  조례를 찾지 못했습니다. 검색 결과: {names}'))
            return

        self.stdout.write(
            f"  조례: {target['name']} ({target['org']}) "
            f"· 시행 {ordinances.fmt_date(target['effective_date'])}")

        if not res['entries']:
            self.stdout.write(self.style.WARNING(
                '  풍력 이격거리 조항을 조문·별표 어디에서도 찾지 못했습니다. '
                '해당 지자체에 풍력 이격 규정이 없거나 다른 조례에 있을 수 있습니다.'))
            return

        self.stdout.write(f"  {res['source']}: {res['label']} {res['article_title']}")

        for e in res['entries']:
            key = (e['target'], e['detail'])
            old = current.get(key) or next(
                (r for k, r in current.items() if k[0] == e['target']), None)
            if old and old.distance_m == e['distance_m']:
                self.stdout.write(f"    ○ {e['detail']}: {e['distance_m']:,}m (일치)")
            elif old:
                self.stdout.write(self.style.WARNING(
                    f"    ! {e['detail']}: DB {old.distance_m:,}m → 원문 {e['distance_m']:,}m"))
            else:
                self.stdout.write(self.style.WARNING(
                    f"    + {e['detail']}: {e['distance_m']:,}m (DB에 없음)"))
            self.stdout.write(f"        “{e['sentence'][:110]}”")

        if apply:
            # 수동 수집에 성공했으면 자동 수집의 '없음' 기록을 지운다
            ordinances.forget_miss(sido, sigungu)
            msg = f"    → 반영 {res['applied']}건"
            if res['flagged']:
                msg += f" / 미확인 표시 {res['flagged']}건"
            self.stdout.write(self.style.SUCCESS(msg))
