"""
법령 원문 대조
---------------------------------------------------------------
LawReference / PermitStep / RegulationLayer에 적힌 법령명·조문을
국가법령정보 OPEN API의 **원문과 대조**하고, 조문 전문을 LawArticle에 보관한다.

confidence 정책 (추측 금지)
  HIGH   — 법령이 현행으로 확인되고, 지정한 조문이 원문에 실재함
  MEDIUM — 법령은 확인됐으나 조문 표기를 특정하지 못함(별표 참조 등)
  LOW    — 법령 자체를 찾지 못함 → 값을 바꾸지 않고 그대로 남긴다

사용
  python manage.py verify_laws              # 대조만 하고 보고 (기본)
  python manage.py verify_laws --apply      # confidence·verified_at 갱신까지
  python manage.py verify_laws --only 전기사업법
"""
from __future__ import annotations

import time
from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.windsite import lawapi
from apps.windsite.models import LawArticle, LawReference, PermitStep, RegulationLayer


#: 국가법령정보로 대조할 수 **없는** 근거 표기 → (걸러낼 조각, 왜)
#:
#: 이것들은 아직 확인하지 않은 법령이 아니라 애초에 법령이 아니다. 대조
#: 대상에 두면 영원히 '✘ 미검증'으로 남아, 정말로 확인이 필요한 항목이
#: 그 소음에 묻힌다(실측 2026-08: 전체 41건 중 3건이 이 상태로 고정).
#:
#: ⚠️ 지침·고시 등 행정규칙은 여기 넣지 않는다. `_verify_one`이
#:    `search_admin_rule`로 되짚어 MEDIUM까지 올려 주기 때문이다.
NOT_A_STATUTE = {
    '조례': '지자체 조례는 `sync_ordinances`가 따로 수집한다',
    '이용규정': '한전이 정하고 산업부가 인가하는 약관이라 법령이 아니다',
    '참고 정보': '규제가 아니라 현황 참고 자료다',
}


def _not_a_statute(name: str) -> str:
    """법령이 아니면 그 까닭을, 법령이면 빈 문자열을 돌려준다."""
    for frag, why in NOT_A_STATUTE.items():
        if frag in name:
            return why
    return ''


class Command(BaseCommand):
    help = '법령·조문을 국가법령정보 OPEN API 원문과 대조합니다.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='대조 결과를 DB(confidence/verified_at)에 반영')
        parser.add_argument('--only', action='append', default=[],
                            help='특정 법령명만 대조 (반복 지정 가능)')
        parser.add_argument('--sleep', type=float, default=0.4,
                            help='API 호출 간 간격(초)')

    # ------------------------------------------------------------------
    def handle(self, *args, **o):
        if lawapi.is_demo_account():
            self.stdout.write(self.style.WARNING(
                '공용 데모 계정(OC=test)으로 조회합니다. 사용량 제한이 있으니 '
                '운영에는 LAW_API_OC를 발급받아 설정하십시오. (https://open.law.go.kr)'))

        targets = self._collect(o['only'])
        self.stdout.write(f'대조 대상 법령 {len(targets)}건\n')

        cache: dict[str, dict] = {}
        rows: list[dict] = []
        for name, labels in sorted(targets.items()):
            rec = self._verify_one(name, labels, cache, o['sleep'])
            rows.append(rec)
            mark = {'HIGH': '✔', 'MEDIUM': '△', 'LOW': '✘'}[rec['confidence']]
            self.stdout.write(
                f"{mark} {name} — {rec['note']}"
            )
            for lb, ok, title in rec['articles']:
                self.stdout.write(f"      {'○' if ok else '✘'} {lb} {title}")

        if o['apply']:
            self._apply(rows)
        else:
            self.stdout.write(self.style.WARNING(
                '\n대조만 수행했습니다. DB에 반영하려면 --apply 를 붙이십시오.'))

        # 대조 대상에서 뺀 것은 **조용히 버리지 않고** 까닭과 함께 보여 준다.
        # 목록에서 사라지면 다음 사람이 "왜 빠졌지"를 다시 조사하게 된다.
        skipped = self._skipped()
        if skipped:
            self.stdout.write('\n대조 대상 아님 — 법령이 아니라 별도 경로로 확인한다')
            for nm, why in sorted(skipped.items()):
                self.stdout.write(f'   · {nm} — {why}')

        hi = sum(1 for r in rows if r['confidence'] == 'HIGH')
        self.stdout.write(self.style.SUCCESS(
            f'\n완료 — 원문 확인 {hi} / 전체 {len(rows)}'
            + (f' (대조 대상 아님 {len(skipped)}건 제외)' if skipped else '')))

    # ------------------------------------------------------------------
    @staticmethod
    def _skipped() -> dict[str, str]:
        """대조 대상에서 뺀 근거 표기 → 까닭."""
        out: dict[str, str] = {}
        names = {r.name for r in LawReference.objects.all()}
        names |= {s.law for s in PermitStep.objects.filter(is_active=True)}
        names |= {l.law for l in RegulationLayer.objects.filter(is_active=True)}
        for nm in names:
            nm = (nm or '').strip()
            if not nm:
                continue
            why = _not_a_statute(nm)
            if why:
                out[nm] = why
        return out

    # ------------------------------------------------------------------
    def _collect(self, only: list[str]) -> dict[str, set[str]]:
        """법령명 → 확인해야 할 조문 표기 집합"""
        targets: dict[str, set[str]] = {}

        def add(name: str, article: str = ''):
            name = (name or '').strip()
            if not name or '해당 없음' in name or _not_a_statute(name):
                return
            targets.setdefault(name, set())
            if article:
                targets[name].add(article.strip())

        for r in LawReference.objects.all():
            add(r.name)
            for part in (r.key_articles or '').replace('·', ',').split(','):
                if part.strip().startswith('제'):
                    add(r.name, part.strip())
        for s in PermitStep.objects.filter(is_active=True):
            add(s.law, s.article)
        for l in RegulationLayer.objects.filter(is_active=True):
            add(l.law, l.article)

        if only:
            targets = {k: v for k, v in targets.items()
                       if any(o in k for o in only)}
        return targets

    # ------------------------------------------------------------------
    def _verify_one(self, name: str, labels: set[str],
                    cache: dict, sleep: float) -> dict:
        rec = {'name': name, 'confidence': 'LOW', 'note': '', 'articles': [],
               'meta': {}, 'law_id': '', 'mst': ''}
        try:
            hits = cache.get(name) or lawapi.search_law(name, display=10)
            cache[name] = hits
            time.sleep(sleep)
        except Exception as e:                                  # noqa: BLE001
            rec['note'] = f'검색 실패 ({type(e).__name__})'
            return rec

        key = lawapi.normalize_law_name(name)

        def same(h):
            return lawapi.normalize_law_name(h['name']) == key

        exact = next((h for h in hits if same(h) and h['status'] == '현행'), None)
        if not exact:
            exact = next((h for h in hits if same(h)), None)
        if not exact:
            # 법령이 아니라 행정규칙(훈령·예규·고시·지침)일 수 있다
            try:
                rules = lawapi.search_admin_rule(name)
                time.sleep(sleep)
            except Exception:                                   # noqa: BLE001
                rules = []
            hit = next((r for r in rules
                        if lawapi.normalize_law_name(r['name']) == key), None)
            if hit:
                rec['confidence'] = 'MEDIUM'
                rec['note'] = (f"행정규칙으로 확인 ({hit['kind']}) · 시행 "
                               f"{_fmt(hit['effective_date'])} · 소관 {hit['ministry'] or '-'}")
                rec['law_id'] = hit['rule_id']
                rec['is_admin_rule'] = True
                return rec
            rec['note'] = ('현행 법령·행정규칙에서 같은 이름을 찾지 못했습니다. '
                           f'유사: {", ".join(h["name"] for h in hits[:3]) or "없음"}')
            return rec

        rec.update(law_id=exact['law_id'], mst=exact['mst'], meta=exact)
        rec['note'] = (f"현행 확인 · 시행 {_fmt(exact['effective_date'])} "
                       f"· 소관 {exact['ministry'] or '-'}")
        rec['confidence'] = 'MEDIUM'

        if not labels:
            return rec

        try:
            body = lawapi.fetch_law_articles(exact['mst'])
            time.sleep(sleep)
        except Exception as e:                                  # noqa: BLE001
            rec['note'] += f' / 본문 조회 실패 ({type(e).__name__})'
            return rec

        rec['meta'] = {**exact, **body['meta']}

        # 별표를 참조하는 표기가 있으면 별표 목록도 함께 받는다.
        # 환경영향평가 대상 규모처럼 판정 핵심 수치가 조문이 아닌 별표에 있는 경우가 많다.
        appendices: list[dict] = []
        if any(lawapi.parse_appendix_label(lb)[0] for lb in labels):
            decree = self._decree_of(name, labels, cache, sleep)
            if decree:
                try:
                    appendices = lawapi.fetch_law_appendices(decree['mst'])
                    time.sleep(sleep)
                except Exception:                               # noqa: BLE001
                    appendices = []

        found_any = False
        for label in sorted(labels):
            if lawapi.parse_appendix_label(label)[0]:
                ap = lawapi.find_appendix(appendices, label)
                rec['articles'].append((label, bool(ap), ap['title'][:60] if ap else ''))
                if ap:
                    found_any = True
                    self._store_appendix(rec, label, ap, body['meta'])
                continue
            art = lawapi.find_article(body['articles'], label)
            rec['articles'].append((label, bool(art), art['title'] if art else ''))
            if art:
                found_any = True
                self._store(rec, label, art, body['meta'], exact)
        if found_any and all(ok for _, ok, _ in rec['articles']):
            rec['confidence'] = 'HIGH'
        return rec

    # ------------------------------------------------------------------
    def _decree_of(self, name: str, labels: set[str], cache: dict, sleep: float) -> dict | None:
        """
        '시행령 별표3' 처럼 하위법령의 별표를 가리키는 경우 시행령을 따로 찾는다.
        '별표3'만 적혀 있으면 본법의 별표로 본다.
        """
        wants_decree = any('시행령' in lb for lb in labels)
        target_name = f'{name} 시행령' if wants_decree and '시행령' not in name else name
        try:
            hits = cache.get(target_name) or lawapi.search_law(target_name, display=5)
            cache[target_name] = hits
            time.sleep(sleep)
        except Exception:                                       # noqa: BLE001
            return None
        key = lawapi.normalize_law_name(target_name)
        return next((h for h in hits
                     if lawapi.normalize_law_name(h['name']) == key), None)

    def _store_appendix(self, rec: dict, label: str, ap: dict, meta: dict) -> None:
        LawArticle.objects.update_or_create(
            law_name=rec['name'], article_label=label,
            defaults=dict(
                source_type='LAW', org=meta.get('ministry', ''),
                law_id=rec['law_id'], mst=rec['mst'],
                article_no=ap['no'], article_sub_no=ap.get('sub_no', ''),
                article_title=ap['title'][:300], article_text=ap['text'][:20000],
                effective_date=meta.get('effective_date', ''),
                promulgated_date=meta.get('promulgated', ''),
                source_url=lawapi.law_detail_url(rec['law_id']),
                via_demo_account=lawapi.is_demo_account(),
            ),
        )

    # ------------------------------------------------------------------
    def _store(self, rec: dict, label: str, art: dict, meta: dict, hit: dict) -> None:
        LawArticle.objects.update_or_create(
            law_name=rec['name'], article_label=label,
            defaults=dict(
                source_type='LAW',
                org=meta.get('ministry') or hit.get('ministry', ''),
                law_id=rec['law_id'], mst=rec['mst'],
                article_no=art['no'], article_sub_no=art.get('sub_no', ''),
                article_title=art['title'], article_text=art['text'][:20000],
                effective_date=meta.get('effective_date', ''),
                promulgated_date=meta.get('promulgated', ''),
                source_url=lawapi.law_detail_url(rec['law_id']),
                via_demo_account=lawapi.is_demo_account(),
            ),
        )

    # ------------------------------------------------------------------
    @transaction.atomic
    def _apply(self, rows: list[dict]) -> None:
        today = date.today()
        changed = 0
        for r in rows:
            if r['confidence'] == 'LOW':
                continue                        # 확인 못 한 것은 건드리지 않는다
            url = lawapi.law_detail_url(r['law_id'])
            n = LawReference.objects.filter(name=r['name']).update(
                confidence=r['confidence'], verified_at=today, source_url=url)
            changed += n
            # 조문 단위로 확인된 경우에만 규칙·레이어의 confidence를 올린다
            ok_labels = {lb for lb, ok, _ in r['articles'] if ok}
            for label in ok_labels:
                changed += RegulationLayer.objects.filter(
                    law=r['name'], article__startswith=label).update(
                    confidence='HIGH', verified_at=today, source_url=url)
                changed += PermitStep.objects.filter(
                    law=r['name'], article__startswith=label).update(
                    confidence='HIGH', source_url=url)
        self.stdout.write(self.style.SUCCESS(f'DB 반영 — {changed}행 갱신'))


def _fmt(yyyymmdd: str) -> str:
    s = (yyyymmdd or '').strip()
    return f'{s[:4]}-{s[4:6]}-{s[6:8]}' if len(s) == 8 else (s or '-')
