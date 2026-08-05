"""
범용 SHP → DB 적재 로더
---------------------------------------------------------------
공개 API가 없거나 API로는 상세 속성을 받을 수 없는 공간데이터를
ZIP(또는 디렉토리) 안의 SHP째로 적재한다. GDAL 없이 pyshp만 사용한다.

사용 예
  # ZIP 안의 모든 SHP을 적재 (국가유산청 지정유산)
  python manage.py load_spatial_shp \
      --zip data/heritage/heritage_designated.zip \
      --prefix heritage --category 국가유산 --source 국가유산청

  # 특정 SHP만 / 기존 데이터 교체
  python manage.py load_spatial_shp --zip ... --only 국가지정유산 --replace

특징
  - ZIP 내부 한글 파일명(CP437로 저장된 CP949)을 복원한다.
  - `.prj`에서 EPSG를 판독한다. 판독 실패 시 `--srs`로 지정해야 하며,
    **추측해서 넘어가지 않는다.**
  - 좌표는 원본 좌표계 그대로 WKB로 저장한다(국내 데이터는 EPSG:5179 =
    검토 엔진의 계량 좌표계라 재투영이 불필요).
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.windsite.models import SpatialDataset, SpatialFeature

#: 명칭 속성 자동 판별 후보 (앞쪽 우선)
NAME_FIELD_CANDIDATES = [
    '국가유산명', '문화재명', '명칭', '구역명', '지구명', '지역명', '시설명',
    'name', 'NAME', 'nm', 'NM', 'uname', 'UNAME',
]
#: 유형 속성 후보
KIND_FIELD_CANDIDATES = ['종목명', '지정종목', '구역명', '유형', 'kind', 'TYPE']
SIDO_CANDIDATES = ['시도명', 'sido_name', 'SIDO_NM', '시도']
SIGUNGU_CANDIDATES = ['시군구명', 'sigg_name', 'SIGUNGU_NM', '시군구']

BATCH = 500


def _pick(fields: list[str], candidates: list[str]) -> str:
    for c in candidates:
        if c in fields:
            return c
    return ''


def _decode_zip_name(name: str) -> str:
    """ZIP 헤더에 CP437로 저장된 한글 파일명을 복원."""
    try:
        return name.encode('cp437').decode('cp949')
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


class Command(BaseCommand):
    help = 'SHP(ZIP 또는 디렉토리)을 SpatialDataset/SpatialFeature로 적재합니다.'

    def add_arguments(self, parser):
        parser.add_argument('--zip', help='SHP이 든 ZIP 경로')
        parser.add_argument('--dir', help='SHP이 든 디렉토리 경로')
        parser.add_argument('--only', action='append', default=[],
                            help='적재할 SHP 이름(확장자 제외). 반복 지정 가능')
        parser.add_argument('--prefix', default='', help='데이터셋 키 접두사')
        parser.add_argument('--category', default='', help='분류')
        parser.add_argument('--source', default='', help='출처 기관')
        parser.add_argument('--source-url', default='')
        parser.add_argument('--encoding', default='cp949', help='DBF 인코딩 (기본 cp949)')
        parser.add_argument('--srs', type=int, default=None,
                            help='.prj 판독 실패 시 사용할 EPSG 코드')
        parser.add_argument('--name-field', default='', help='명칭 속성 강제 지정')
        parser.add_argument('--replace', action='store_true',
                            help='같은 키의 기존 피처를 삭제하고 다시 적재')
        parser.add_argument('--dry-run', action='store_true',
                            help='적재하지 않고 구조만 출력')

    # ------------------------------------------------------------------
    def handle(self, *args, **o):
        try:
            import shapefile                                    # noqa: F401
            from pyproj import CRS                              # noqa: F401
            from shapely.geometry import shape                  # noqa: F401
        except ImportError as e:
            raise CommandError(
                f'공간연산 의존성이 없습니다 ({e}). requirements.txt 반영 후 '
                'backend 이미지를 재빌드하십시오.'
            ) from e

        if not o['zip'] and not o['dir']:
            raise CommandError('--zip 또는 --dir 중 하나를 지정하십시오.')

        sources = self._collect(o)
        if not sources:
            raise CommandError('적재할 SHP을 찾지 못했습니다.')

        total = 0
        for label, parts in sources.items():
            if o['only'] and label not in o['only']:
                continue
            total += self._load_one(label, parts, o)

        self.stdout.write(self.style.SUCCESS(f'\n총 {total:,}건 적재 완료'))

    # ------------------------------------------------------------------
    def _collect(self, o) -> dict[str, dict[str, bytes | Path]]:
        """{레이어명: {'shp':..., 'dbf':..., 'shx':..., 'prj':...}}"""
        out: dict[str, dict] = {}

        if o['zip']:
            path = self._resolve(o['zip'])
            with zipfile.ZipFile(path) as zf:
                for raw in zf.namelist():
                    kor = _decode_zip_name(raw)
                    stem, _, ext = kor.rpartition('.')
                    if ext.lower() not in ('shp', 'dbf', 'shx', 'prj'):
                        continue
                    label = Path(stem).name
                    out.setdefault(label, {'_src': str(path)})[ext.lower()] = zf.read(raw)
        else:
            d = self._resolve(o['dir'])
            for p in sorted(Path(d).glob('*.shp')):
                entry = {'_src': str(p)}
                for ext in ('shp', 'dbf', 'shx', 'prj'):
                    f = p.with_suffix('.' + ext)
                    if f.exists():
                        entry[ext] = f.read_bytes()
                out[p.stem] = entry
        return out

    def _resolve(self, raw: str) -> Path:
        p = Path(raw)
        if not p.is_absolute():
            p = Path(settings.BASE_DIR) / raw
        if not p.exists():
            raise CommandError(f'경로를 찾을 수 없습니다: {p}')
        return p

    # ------------------------------------------------------------------
    def _load_one(self, label: str, parts: dict, o) -> int:
        import shapefile
        from pyproj import CRS
        from shapely.geometry import shape

        if 'shp' not in parts or 'dbf' not in parts:
            self.stdout.write(self.style.WARNING(f'[{label}] .shp/.dbf 누락 — 건너뜁니다.'))
            return 0

        # --- 좌표계 판독 (추측 금지) ---
        epsg = o['srs']
        if 'prj' in parts and not epsg:
            try:
                epsg = CRS.from_wkt(parts['prj'].decode('utf-8', 'replace')).to_epsg()
            except Exception:                                   # noqa: BLE001
                epsg = None
        if not epsg:
            raise CommandError(
                f'[{label}] .prj에서 EPSG를 판독하지 못했습니다. --srs로 명시하십시오. '
                '좌표계를 추측하면 거리 계산이 통째로 틀어집니다.'
            )

        reader = shapefile.Reader(
            shp=io.BytesIO(parts['shp']),
            dbf=io.BytesIO(parts['dbf']),
            shx=io.BytesIO(parts['shx']) if 'shx' in parts else None,
            encoding=o['encoding'], encodingErrors='replace',
        )
        fields = [f[0] for f in reader.fields[1:]]
        name_f = o['name_field'] or _pick(fields, NAME_FIELD_CANDIDATES)
        kind_f = _pick(fields, KIND_FIELD_CANDIDATES)
        sido_f = _pick(fields, SIDO_CANDIDATES)
        sigg_f = _pick(fields, SIGUNGU_CANDIDATES)

        self.stdout.write(
            f'[{label}] {len(reader):,}건 · {reader.shapeTypeName} · EPSG:{epsg} · '
            f'명칭={name_f or "(미판별)"} 유형={kind_f or "-"}'
        )
        self.stdout.write(f'         속성: {", ".join(fields)}')

        if o['dry_run']:
            return 0
        if not name_f:
            self.stdout.write(self.style.WARNING(
                '         명칭 속성을 판별하지 못했습니다 — --name-field로 지정하십시오.'))

        code = f"{o['prefix']}_{label}" if o['prefix'] else label
        ds, _ = SpatialDataset.objects.update_or_create(
            code=code,
            defaults=dict(
                name=label, category=o['category'], source=o['source'],
                source_file=Path(parts['_src']).name, srs_epsg=epsg,
                name_field=name_f, source_url=o['source_url'],
                loaded_at=timezone.now(), is_active=True,
            ),
        )
        if o['replace']:
            deleted, _ = SpatialFeature.objects.filter(dataset=ds).delete()
            if deleted:
                self.stdout.write(f'         기존 {deleted:,}행 삭제')
        elif SpatialFeature.objects.filter(dataset=ds).exists():
            self.stdout.write(self.style.WARNING(
                '         이미 적재되어 있습니다. 다시 적재하려면 --replace를 쓰십시오.'))
            return 0

        return self._insert(reader, ds, epsg, name_f, kind_f, sido_f, sigg_f, shape)

    # ------------------------------------------------------------------
    def _insert(self, reader, ds, epsg, name_f, kind_f, sido_f, sigg_f, shape) -> int:
        from apps.windsite import geo

        to_wgs = None
        if epsg != int(geo.METRIC_CRS.split(':')[1]):
            from pyproj import Transformer
            to_wgs = Transformer.from_crs(f'EPSG:{epsg}', geo.GEOGRAPHIC_CRS, always_xy=True)
        else:
            to_wgs = None       # 5179 → geo.to_geographic_xy 사용

        buf: list[SpatialFeature] = []
        count = skipped = 0

        for sr in reader.iterShapeRecords():
            try:
                g = shape(sr.shape.__geo_interface__)
            except Exception:                                   # noqa: BLE001
                skipped += 1
                continue
            if g.is_empty:
                skipped += 1
                continue
            if not g.is_valid:
                g = g.buffer(0)
                if g.is_empty:
                    skipped += 1
                    continue

            rec = sr.record.as_dict()
            minx, miny, maxx, maxy = g.bounds
            try:
                p = g.representative_point()
                if to_wgs is not None:
                    lng, lat = to_wgs.transform(p.x, p.y)
                else:
                    lng, lat = geo.to_geographic_xy(p.x, p.y)
            except Exception:                                   # noqa: BLE001
                lat = lng = None

            buf.append(SpatialFeature(
                dataset=ds,
                name=str(rec.get(name_f, '') or '')[:300],
                kind=str(rec.get(kind_f, '') or '')[:100],
                sido=str(rec.get(sido_f, '') or '')[:50],
                sigungu=str(rec.get(sigg_f, '') or '')[:50],
                attrs={k: (v if isinstance(v, (str, int, float, bool, type(None))) else str(v))
                       for k, v in rec.items()},
                geom_wkb=g.wkb,
                min_x=minx, min_y=miny, max_x=maxx, max_y=maxy,
                centroid_lat=lat, centroid_lng=lng,
            ))
            if len(buf) >= BATCH:
                count += self._flush(buf)
                self.stdout.write(f'         … {count:,}건', ending='\r')

        count += self._flush(buf)
        SpatialDataset.objects.filter(pk=ds.pk).update(feature_count=count)
        msg = f'         적재 {count:,}건'
        if skipped:
            msg += f' (도형 오류로 제외 {skipped:,}건)'
        self.stdout.write(self.style.SUCCESS(msg))
        return count

    @transaction.atomic
    def _flush(self, buf: list) -> int:
        if not buf:
            return 0
        SpatialFeature.objects.bulk_create(buf, batch_size=BATCH)
        n = len(buf)
        buf.clear()
        return n
