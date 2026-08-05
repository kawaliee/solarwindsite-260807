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

        hi = sum(1 for r in rows if r['confidence'] == 'HIGH')
        self.stdout.write(self.style.SUCCESS(
            f'\n완료 — 원문 확인 {hi} / 전체 {len(rows)}'))

    # ------------------------------------------------------------------
    def _collect(self, only: list[str]) -> dict[str, set[str]]:
        """법령명 → 확인해야 할 조문 표기 집합"""
        targets: dict[str, set[str]] = {}

        def add(name: str, article: str = ''):
            name = (name or '').strip()
            if not name or '해당 없음' in name:
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

        exact = next((h for h in hits if h['name'] == name and h['status'] == '현행'), None)
        if not exact:
            exact = next((h for h in hits if h['name'] == name), None)
        if not exact:
            rec['note'] = ('현행 법령에서 같은 이름을 찾지 못했습니다. '
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
        found_any = False
        for label in sorted(labels):
            art = lawapi.find_article(body['articles'], label)
            rec['articles'].append((label, bool(art), art['title'] if art else ''))
            if art:
                found_any = True
                self._store(rec, label, art, body['meta'], exact)
        if found_any and all(ok for _, ok, _ in rec['articles']):
            rec['confidence'] = 'HIGH'
        return rec

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
