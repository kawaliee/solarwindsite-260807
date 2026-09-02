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
        parser.add_argument('--cover', action='store_true',
                            help='유효지역이 하나로 안 될 때 필요한 신청좌표 '
                                 '전부를 수집합니다 (좌표 수만큼 오래 걸립니다)')
        parser.add_argument('--itv', type=int, default=rawwind.ITV_COARSE,
                            help='자료 간격(분) — 10 또는 30만 유효')

    def handle(self, *args, **o):
        plan = None
        spots: list[tuple[float, float, str]] = []
        lat, lng = o.get('lat'), o.get('lon')
        if o.get('plan'):
            try:
                plan = SitePlan.objects.get(id=o['plan'])
            except SitePlan.DoesNotExist as e:
                raise CommandError(f"배치안을 찾지 못했습니다: {o['plan']}") from e
            self.stdout.write(f'배치안 {plan} ({len(plan.turbines or [])}기)')
            best = _coverage_report(self, plan)
            if o.get('cover'):
                spots = [(c['lat'], c['lng'],
                          f"{c['index'] + 1}호기 · {len(c['assigned'])}기 담당")
                         for c in rawwind.cover_points(plan.turbines or [])]
            else:
                spots = [(best['lat'], best['lng'],
                          f"{best['index'] + 1}호기 · "
                          f"{len(best['covered'])}기 담당")]
        if not spots:
            if lat is None or lng is None:
                raise CommandError('--plan 또는 --lat/--lon 이 필요합니다.')
            spots = [(float(lat), float(lng), '지정 좌표')]

        end = (_parse_day(o['dt_to'], end=True) if o['dt_to']
               else rawwind.AVAILABLE_TO)
        start = (_parse_day(o['dt_from']) if o['dt_from']
                 else end - timedelta(days=365))
        heights = [int(h) for h in str(o['heights']).split(',') if h.strip()]
        itv = int(o['itv'])

        self.stdout.write(
            f'기간 {start:%Y-%m-%d} ~ {end:%Y-%m-%d} · 고도 {heights} · {itv}분 간격 '
            f'· 신청좌표 {len(spots)}곳')
        for lat, lng, label in spots:
            self.stdout.write('')
            self.stdout.write(f'■ 신청좌표 {lat:.5f},{lng:.5f} — {label}')
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
    배치안의 **신청좌표 후보** — 유효지역에 가장 많은 호기를 담는 호기 자리.

    종전에는 배치선의 기하 중심을 썼다. 그러나 고시가 정하는 유효지역은
    *신청좌표를 중심으로 반지름 2km인 원*이고 그 안에 블레이드 회전 투영면이
    들어와야 하므로, 기준점은 '가운데'가 아니라 **가장 많은 호기를 담는 점**
    이어야 한다. 실측에서 평창 8기는 4호기(여유 387m), 완도 10기는 어느
    호기를 잡아도 한 기가 벗어난다 — 중심을 잘못 잡으면 이 사실이 안 보인다.
    """
    pts = plan.turbines or []
    if not pts:
        raise CommandError('배치안에 호기 좌표가 없습니다.')
    b = rawwind.best_center(pts)
    return float(b['lat']), float(b['lng'])


def _coverage_report(cmd, plan: SitePlan) -> dict:
    """유효지역 진단 — 몇 기가 담기고, 안 담기면 무엇을 해야 하는지."""
    pts = plan.turbines or []
    n = len(pts)
    b = rawwind.best_center(pts)
    rotor = b['rotor_m']
    cmd.stdout.write(
        f"유효지역 반경 {rawwind.VALID_RADIUS_M:,}m · 블레이드 회전 반지름 "
        f"{rotor:,.0f}m → 신청좌표에서 {rawwind.VALID_RADIUS_M - rotor:,.0f}m "
        f"안에 호기가 있어야 합니다.")
    cmd.stdout.write(
        f"  호기 중심 최적: {b['index'] + 1}호기 — {len(b['covered'])}/{n}기 포함 "
        f"(최원 {b['max_dist_m']:,.0f}m, 여유 {b['margin_m']:+,.0f}m)")
    if len(b['covered']) == n:
        return b

    out = [i + 1 for i in b['uncovered']]
    cmd.stdout.write(cmd.style.WARNING(
        f"  ⚠️ {len(out)}기가 유효지역 밖입니다: {', '.join(map(str, out))}호기"))
    f = rawwind.free_center(pts)
    if f and f.get('usable'):
        cmd.stdout.write(cmd.style.WARNING(
            f"  → 신청좌표를 호기 자리에 두지 않으면 한 유효지역으로 들어갑니다: "
            f"{f['lat']:.5f},{f['lng']:.5f} (최원 {f['max_dist_m']:,.0f}m, "
            f"여유 {f['margin_m']:+,.0f}m, 육지). `--lat {f['lat']:.5f} "
            f"--lon {f['lng']:.5f}` 로 받으십시오."))
    elif f and f['fits'] and f.get('on_land') is False:
        # 기하학적으로는 되지만 그 점이 바다다. 육상풍력 신청좌표로 쓸 수 없다.
        cmd.stdout.write(cmd.style.WARNING(
            f"  → 기하학적 최적 좌표({f['lat']:.5f},{f['lng']:.5f})는 한 "
            f"유효지역에 담기지만 **해상**입니다(해안선까지 "
            f"{f['offshore_m']:,.0f}m). 육상풍력 신청좌표로 쓸 수 없으므로 "
            f"호기 자리 기준으로 진행하고, 벗어난 호기는 배치 조정 또는 별도 "
            f"신청좌표로 다루십시오."))
    elif f and f['fits'] and f.get('on_land') is None:
        cmd.stdout.write(cmd.style.WARNING(
            f"  → 기하학적 최적 좌표({f['lat']:.5f},{f['lng']:.5f})는 한 "
            f"유효지역에 담기지만, 육지 여부를 확인하지 못했습니다. 해상이면 "
            f"신청좌표로 쓸 수 없으니 직접 확인하십시오."))
    else:
        cov = rawwind.cover_points(pts)
        cmd.stdout.write(cmd.style.WARNING(
            f"  → 자유 좌표로도 한 유효지역에 담기지 않습니다. 전부 덮으려면 "
            f"신청좌표 {len(cov)}개가 필요합니다: "
            + ', '.join(f"{c['index'] + 1}호기({len(c['assigned'])}기)"
                        for c in cov)
            + ". 유효지역은 곧 발전사업허가의 단위이므로, 허가를 나눠야 하는지"
              " 검토하십시오. `--cover` 로 좌표 전부를 수집합니다."))
    return b


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
