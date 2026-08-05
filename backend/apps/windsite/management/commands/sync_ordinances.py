"""
지자체 이격거리 조례 원문 대조
---------------------------------------------------------------
자치법규 OPEN API로 조례 원문을 받아 **풍력 이격거리 조항을 찾아내고**
DB(LocalOrdinance)의 값과 대조한다.

왜 필요한가
  시드값은 언론보도 등 2차 자료 기반이었다. 실제로 화순군 조례를 원문 대조한 결과
  DB의 1,200m/800m가 아니라 **2,000m(10호 이상 취락) / 1,500m(10호 미만 취락)**
  이었다(제20조의2 발전시설 허가의 기준 제3항, 2025.12.22 개정). 기준이 틀리면
  동심원 분석 결과가 통째로 틀어진다.

동작
  1) 지자체명으로 자치법규를 검색해 '계획 조례'류를 고른다 (제명이 바뀐 사례 있음)
  2) 조문 전문에서 '풍력' + 거리 표현이 있는 조를 찾는다
  3) 거리 수치를 추출해 DB값과 diff를 보고한다
  4) --apply 를 주면 **추출이 명확한 항목만** 반영한다 (모호하면 건드리지 않음)

사용
  python manage.py sync_ordinances                       # 전체 조례 대조·보고
  python manage.py sync_ordinances --sigungu 화순군 --apply
  python manage.py sync_ordinances --sigungu 영양군 --sido 경상북도 --apply  # 신규 등록
"""
from __future__ import annotations

import re
import time
from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.windsite import lawapi
from apps.windsite.models import LawArticle, LocalOrdinance

#: 조례명 후보 — 지자체마다 제명이 다르다
ORDINANCE_KEYWORDS = ('도시계획 조례', '군계획 조례', '도시·군계획 조례', '도시군계획 조례')

#: 거리 수치 표현. '2,000미터' / '2000m' / '1.5킬로미터'
_DIST = r'([0-9][0-9,\.]*)\s*(미터|m|M|킬로미터|km|KM)'

#: 이격 대상 판별 — LocalOrdinance.TARGET_CHOICES 로 매핑
TARGET_PATTERNS: list[tuple[str, str]] = [
    ('RESIDENTIAL', r'(취락|주거밀집|주거지역|주택|가구|세대|호 이상|호 미만)'),
    ('QUIET_FACILITY', r'(정온시설|학교|병원|요양|어린이집|경로당|공공시설)'),
    ('ROAD', r'(도로|국도|지방도|군도|고속도로)'),
    ('RAILWAY', r'(철도|역사)'),
]


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
                self._one(sgg, o)
            except Exception as e:                              # noqa: BLE001
                self.stdout.write(self.style.ERROR(f'  실패: {type(e).__name__}: {e}'))
            time.sleep(o['sleep'])

        if not o['apply']:
            self.stdout.write(self.style.WARNING(
                '\n대조만 수행했습니다. DB에 반영하려면 --apply 를 붙이십시오.'))

    # ------------------------------------------------------------------
    def _one(self, sigungu: str, o: dict) -> None:
        hits = lawapi.search_ordinance(sigungu, '계획 조례')
        cand = [h for h in hits
                if sigungu in h['org'] and any(k in h['name'] for k in ORDINANCE_KEYWORDS)]
        if not cand:
            cand = [h for h in hits if sigungu in h['org'] and '계획' in h['name']]
        if not cand:
            self.stdout.write(self.style.ERROR(
                f'  조례를 찾지 못했습니다. 검색 결과: '
                f'{", ".join(h["name"] for h in hits[:5]) or "없음"}'))
            return

        target = cand[0]
        self.stdout.write(
            f"  조례: {target['name']} ({target['org']}) "
            f"· 시행 {_fmt(target['effective_date'])}")

        body = lawapi.fetch_ordinance_articles(target['mst'])
        found = self._extract(body['articles'])
        if not found:
            self.stdout.write(self.style.WARNING(
                '  풍력 이격거리 조항을 찾지 못했습니다. '
                '해당 지자체에 풍력 이격 규정이 없거나 다른 조례에 있을 수 있습니다.'))
            return

        art, entries = found
        label = _article_label(art['no'])
        self.stdout.write(f"  조문: {label} {art['title']}")

        # 원문 보관 — 판정 근거를 원문으로 되돌릴 수 있게
        LawArticle.objects.update_or_create(
            law_name=target['name'], article_label=label,
            defaults=dict(
                source_type='ORDINANCE', org=target['org'],
                law_id=target['ordin_id'], mst=target['mst'],
                article_no=art['no'], article_title=art['title'],
                article_text=art['text'][:20000],
                effective_date=target['effective_date'],
                promulgated_date=target['promulgated'],
                source_url='https://www.law.go.kr/LSW/ordinInfoP.do?ordinSeq=' + target['mst'],
                via_demo_account=lawapi.is_demo_account(),
            ),
        )

        current = {(r.target, r.target_detail): r
                   for r in LocalOrdinance.objects.filter(sigungu=sigungu,
                                                          energy_type__in=['WIND', 'ALL'])}
        for e in entries:
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

        if o['apply']:
            self._apply(sigungu, o['sido'], target, label, entries, current)

    # ------------------------------------------------------------------
    def _extract(self, articles: list[dict]) -> tuple[dict, list[dict]] | None:
        """풍력 이격 조항을 담은 조를 찾아 (조, 추출항목)을 돌려준다."""
        for art in articles:
            text = art['text'] or ''
            if '풍력' not in text:
                continue
            # 풍력 문단만 잘라낸다 — 태양광 조항의 수치를 섞지 않기 위함
            block = _wind_block(text)
            if not block:
                continue
            entries = []
            for line in re.split(r'(?=\n?\s*\d+\.\s)', block):
                m = re.search(_DIST, line)
                if not m:
                    continue
                dist = _to_meters(m.group(1), m.group(2))
                if dist is None:
                    continue
                target = 'OTHER'
                for code, pat in TARGET_PATTERNS:
                    if re.search(pat, line):
                        target = code
                        break
                detail = _detail_of(line)
                entries.append({
                    'target': target, 'detail': detail, 'distance_m': dist,
                    'sentence': re.sub(r'\s+', ' ', line).strip(),
                })
            if entries:
                return art, entries
        return None

    # ------------------------------------------------------------------
    @transaction.atomic
    def _apply(self, sigungu: str, sido: str, target: dict, label: str,
               entries: list[dict], current: dict) -> None:
        sido = sido or next(
            (r.sido for r in current.values() if r.sido), '') or target['org'].split()[0]
        n = 0
        for e in entries:
            obj, created = LocalOrdinance.objects.update_or_create(
                sigungu=sigungu, energy_type='WIND',
                target=e['target'], target_detail=e['detail'],
                defaults=dict(
                    sido=sido,
                    distance_m=e['distance_m'],
                    ordinance_name=target['name'],
                    article=label,
                    difficulty='HIGH',
                    confidence='HIGH',                 # 원문 대조 완료
                    verified_at=date.today(),
                    source_url='https://www.law.go.kr/LSW/ordinInfoP.do?ordinSeq='
                               + target['mst'],
                    note=f"원문 대조 (시행 {_fmt(target['effective_date'])}): "
                         f"{e['sentence'][:250]}",
                ),
            )
            n += 1
        # 원문에서 확인되지 않은 기존 레코드는 지우지 않고 표시만 한다
        stale = LocalOrdinance.objects.filter(
            sigungu=sigungu, energy_type='WIND', confidence__in=['LOW', 'MEDIUM'],
        ).exclude(target_detail__in=[e['detail'] for e in entries])
        for s in stale:
            s.note = ((s.note or '') +
                      f'\n⚠️ {date.today()} 원문 대조에서 확인되지 않은 항목입니다. '
                      '개정으로 삭제됐거나 다른 조례에 있을 수 있어 수기 확인이 필요합니다.')
            s.save(update_fields=['note'])
        self.stdout.write(self.style.SUCCESS(
            f'    → 반영 {n}건' + (f' / 미확인 표시 {stale.count()}건' if stale else '')))


# ----------------------------------------------------------------------
def _wind_block(text: str) -> str:
    """'풍력'이 등장하는 항(①②③…) 하나만 잘라낸다."""
    marks = '①②③④⑤⑥⑦⑧⑨⑩'
    positions = [(m.start(), m.group(0)) for m in re.finditer(f'[{marks}]', text)]
    if not positions:
        return text if '풍력' in text else ''
    for idx, (pos, _) in enumerate(positions):
        end = positions[idx + 1][0] if idx + 1 < len(positions) else len(text)
        block = text[pos:end]
        if '풍력' in block:
            return block
    return ''


def _to_meters(num: str, unit: str) -> int | None:
    try:
        v = float(num.replace(',', ''))
    except ValueError:
        return None
    if unit.lower() in ('킬로미터', 'km'):
        v *= 1000
    return int(round(v)) if v > 0 else None


def _detail_of(line: str) -> str:
    """'10호 이상 취락지역으로부터 2,000미터…' → '10호 이상 취락지역'"""
    m = re.search(r'(\d+호\s*(?:이상|미만)\s*[가-힣]+)', line)
    if m:
        return m.group(1).replace(' ', '')
    m = re.search(r'([가-힣·\s]{2,20}?)(?:으로부터|로부터|와의|과의)', line)
    if m:
        return m.group(1).strip()[:40]
    return line.strip()[:40]


def _article_label(no: str) -> str:
    a, sub = lawapi.ordinance_article_key(no)
    return f'제{a}조의{sub}' if sub else f'제{a}조'


def _fmt(yyyymmdd: str) -> str:
    s = (yyyymmdd or '').strip()
    return f'{s[:4]}-{s[4:6]}-{s[6:8]}' if len(s) == 8 else (s or '-')
