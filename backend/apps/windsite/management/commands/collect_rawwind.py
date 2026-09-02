"""
재현바람장 수집 — 사업지 대표 지점의 1년치 풍황을 미리 받아 둔다.

API가 느려(30분 간격 1년치 약 50분) 보고서 생성 중에 받을 수 없다. 이
명령으로 미리 받아 두면 보고서는 저장된 값을 읽어 바로 낸다.

  # 배치안의 대표 지점(배치선 중심)으로 80·140m 1년치
  python manage.py collect_rawwind --plan <배치안 UUID>

  # 좌표를 직접 주고 기간·고도를 지정
  python manage.py collect_rawwind --lat 37.5069 --lon 128.2456 \
      --from 20240101 --to 20241231 --heights 80,140

⚠️ 오래 걸린다. `docker compose exec -d` 로 띄우거나 화면을 열어 두고 쓴다.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError

from apps.windsite import geo, rawwind
from apps.windsite.models import RawWindSample, SitePlan


class Command(BaseCommand):
    help = '기상청 재현바람장을 받아 RawWindSample에 저장합니다.'

    def add_arguments(self, parser):
        parser.add_argument('--plan', help='배치안 UUID (대표 지점을 자동 계산)')
        parser.add_argument('--lat', type=float)
        parser.add_argument('--lon', type=float)
        parser.add_argument('--from', dest='dt_from', default='',
                            help='시작일 YYYYMMDD (기본: 종료일 1년 전)')
        parser.add_argument('--to', dest='dt_to', default='',
                            help='종료일 YYYYMMDD (기본: 자료 종료 시점)')
        parser.add_argument('--heights', default='80,140',
                            help='바람 고도 목록 (기본 80,140)')
        parser.add_argument('--itv', type=int, default=rawwind.ITV_COARSE,
                            help='자료 간격(분) — 10 또는 30만 유효')

    def handle(self, *args, **o):
        plan = None
        lat, lng = o.get('lat'), o.get('lon')
        if o.get('plan'):
            try:
                plan = SitePlan.objects.get(id=o['plan'])
            except SitePlan.DoesNotExist as e:
                raise CommandError(f"배치안을 찾지 못했습니다: {o['plan']}") from e
            lat, lng = _representative(plan)
            self.stdout.write(f'배치안 {plan} — 대표 지점 {lat:.5f},{lng:.5f}')
        if lat is None or lng is None:
            raise CommandError('--plan 또는 --lat/--lon 이 필요합니다.')

        end = (_parse_day(o['dt_to'], end=True) if o['dt_to']
               else rawwind.AVAILABLE_TO)
        start = (_parse_day(o['dt_from']) if o['dt_from']
                 else end - timedelta(days=365))
        heights = [int(h) for h in str(o['heights']).split(',') if h.strip()]
        itv = int(o['itv'])

        self.stdout.write(
            f'기간 {start:%Y-%m-%d} ~ {end:%Y-%m-%d} · 고도 {heights} · {itv}분 간격')
        for h in heights:
            self._one(plan, lat, lng, start, end, h, itv)

    # ------------------------------------------------------------------
    def _one(self, plan, lat, lng, start, end, height_m, itv):
        self.stdout.write(f'\n[{height_m}m] 수집 시작 — 오래 걸립니다')

        def progress(done, total):
            self.stdout.write(f'  {done}/{total}일', ending='\r')
            self.stdout.flush()

        rows = rawwind.collect(lat, lng, start, end, height_m=height_m,
                               itv=itv, on_progress=progress)
        stats = rawwind.summarize(rows, lat, lng, height_m)
        if stats is None:
            self.stdout.write(self.style.WARNING(
                f'\n[{height_m}m] 표본이 없습니다 — 저장하지 않습니다.'))
            return

        # 기대 표본 수 — 빠짐없이 받았다면 나왔을 개수. 실제와의 차이가
        # 곧 결측·조각 실패분이라, 얼마나 성긴 자료인지 문서가 밝힐 수 있다.
        expected = int((end - start).total_seconds() // (itv * 60))
        # 재현바람장 시각은 KST다. naive로 저장하면 Django가 경고를 내고
        # 나중에 UTC로 읽혀 9시간 어긋난다 — 자료 시각을 잘못 말하게 된다.
        obj, created = RawWindSample.objects.update_or_create(
            lat=round(lat, 5), lng=round(lng, 5), height_m=height_m,
            start=_kst(start), end=_kst(end),
            defaults={'plan': plan, 'interval_min': itv,
                      'stats': stats.to_dict(), 'samples': stats.samples,
                      'expected_samples': expected},
        )
        self.stdout.write(self.style.SUCCESS(
            f"\n[{height_m}m] {'저장' if created else '갱신'} — "
            f'평균 {stats.mean_ms:.2f} m/s · 주풍향 '
            f'{rawwind.dir_ko(stats.prevailing_dir)} · '
            f'표본 {stats.samples:,}/{expected:,} ({obj.coverage * 100:.0f}%)'))


def _representative(plan: SitePlan) -> tuple[float, float]:
    """
    배치안의 대표 지점 — 배치선 전체의 중앙.

    호기마다 받으면 정확하지만 호기 수만큼 시간이 곱해져(8기 × 2고도 ×
    1년이면 열 시간이 넘는다) 현실적이지 않다. 대표 한 곳으로 받고, 그
    사실을 보고서가 밝힌다.
    """
    pts = plan.turbines or []
    if not pts:
        raise CommandError('배치안에 호기 좌표가 없습니다.')
    if len(pts) == 1:
        return float(pts[0][0]), float(pts[0][1])
    line = geo.corridor(pts, 1)          # 폭 1m 회랑 → 중심을 잡기 위한 도형
    rep = geo.representative_latlng(line) if line is not None else None
    if rep:
        return rep
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def _kst(dt: datetime) -> datetime:
    """
    naive datetime → 현재 시간대(settings.TIME_ZONE = Asia/Seoul) 부착.

    재현바람장 시각은 KST다. 그대로 저장하면 Django가 경고를 내고, 나중에
    UTC로 해석되어 자료 시각이 9시간 어긋난 채 보고서에 실린다.
    """
    from django.utils import timezone
    return dt if timezone.is_aware(dt) else timezone.make_aware(dt)


def _parse_day(s: str, end: bool = False) -> datetime:
    try:
        d = datetime.strptime(s.strip(), '%Y%m%d')
    except ValueError as e:
        raise CommandError(f'날짜 형식이 올바르지 않습니다(YYYYMMDD): {s}') from e
    return d.replace(hour=23, minute=30) if end else d.replace(hour=0, minute=0)
