"""
RAG 파이프라인 — 미디어 폴더 일괄 인덱싱 커맨드

사용법:
  python manage.py ingest_media                  # 전체 적재
  python manage.py ingest_media --dry-run        # 파일 목록만 확인
  python manage.py ingest_media --reset          # Qdrant 컬렉션 리셋 후 재적재
  python manage.py ingest_media --dir "001. 당진1_1"  # 특정 폴더만

폴더 → 프로젝트 매핑:
  001. 당진1_*  → 당진 태양광 1단지 (PF)
  002. 당진2    → 당진 태양광 2단지
  003. 홍성1    → 홍성 태양광 1단지
  004. 태평     → 태평 태양광
  005. 영농형   → 영농형 태양광 (화성)
  15. EPC / 0_15. EPC / 23. 전력거래 / 재생E법령 → 전사 공용 코퍼스 (project=None)
"""
import os
import uuid
import hashlib
import logging
import tempfile
import zipfile
import shutil
from pathlib import Path

from django.core.management.base import BaseCommand
from django.conf import settings

logger = logging.getLogger(__name__)

# ── 지원 확장자 ──────────────────────────────────────────────
SUPPORTED_EXTENSIONS = {'.pdf', '.docx', '.doc', '.xlsx', '.xls', '.xlsm', '.pptx', '.ppt', '.hwp', '.hwpx', '.zip'}

# ── 폴더 → 프로젝트 매핑 규칙 ────────────────────────────────
FOLDER_PROJECT_MAP = [
    # (폴더명 접두어 또는 전체명, 프로젝트명)  — None이면 전사 공용 코퍼스
    ('당진PJT(당진행복솔라)_1단계', '당진 태양광 1단계 (당진행복솔라)'),
    ('당진PJT(당진행복솔라)_2단계', '당진 태양광 2단계 (당진행복솔라)'),
    ('태평 PJT', '태평 태양광 (신안증도)'),
    ('홍성PJT', '홍성 태양광'),
    # ── 기존 폴더명 (하위 호환) ──────────────────────────
    ('01. 당진PJT', '당진 태양광 (PJT)'),
    ('001.', '당진 태양광 1단지 (PF)'),
    ('002.', '당진 태양광 2단지'),
    ('003.', '홍성 태양광 1단지'),
    ('004.', '태평 태양광'),
    ('005.', '영농형 태양광 (화성)'),
    ('15.', None),           # EPC 계약서 → 전사 공용
    ('0_15.', None),         # EPC.zip 압축 해제본 → 전사 공용
    ('23.', None),           # 전력거래 → 전사 공용
    ('재생E', None),          # 법령 → 전사 공용
]


def _get_project_name_for_dir(top_dir_name: str):
    """최상위 폴더명으로 프로젝트명 반환 (None이면 전사 공용)"""
    for prefix, project_name in FOLDER_PROJECT_MAP:
        if top_dir_name.startswith(prefix):
            return project_name
    return None  # 매핑 없으면 전사 공용으로 처리


def _get_or_create_project(workspace, project_name, admin_user):
    """프로젝트 DB 레코드 가져오거나 생성"""
    from apps.workspaces.models import Project
    proj, _ = Project.objects.get_or_create(
        workspace=workspace,
        name=project_name,
        defaults={
            'description': f'{project_name} 사업 관련 문서 코퍼스',
            'is_shared': False,
            'created_by': admin_user,
        }
    )
    return proj


def _get_safe_path(filepath: str) -> str:
    """NFC/NFD 자모분리 불일치로 인한 FileNotFoundError를 방지하는 실존 경로 반환"""
    if os.path.exists(filepath):
        return filepath
    import unicodedata
    nfc = unicodedata.normalize('NFC', filepath)
    if os.path.exists(nfc):
        return nfc
    nfd = unicodedata.normalize('NFD', filepath)
    if os.path.exists(nfd):
        return nfd
    return filepath  # 기본값 반환


def _get_checksum(filepath: str) -> str:
    """파일 MD5 체크섬"""
    safe_path = _get_safe_path(filepath)
    h = hashlib.md5()
    with open(safe_path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def _collect_files(media_dir: str, filter_dir: str = None):
    """
    지원 파일 수집 — ZIP 압축 해제 처리 및 DOCX 우선 가드
    Returns (files_list, temp_dirs_list)
    """
    media_path = Path(media_dir)
    all_files = {}  # stem_path -> {ext: filepath}
    temp_dirs = []

    def extract_zip(zip_filepath, parent_root, top_dir_name):
        try:
            tmp_dir = tempfile.mkdtemp(prefix="rag_zip_")
            temp_dirs.append(tmp_dir)
            
            with zipfile.ZipFile(zip_filepath, 'r') as zip_ref:
                for member in zip_ref.infolist():
                    # 한글 파일명 깨짐 방지 디코딩
                    filename = member.filename
                    try:
                        filename = member.filename.encode('cp437').decode('cp949')
                    except Exception:
                        try:
                            filename = member.filename.encode('cp437').decode('utf-8')
                        except Exception:
                            pass
                    
                    filename = os.path.basename(filename)
                    if not filename:
                        continue
                        
                    source = zip_ref.open(member)
                    target_path = os.path.join(tmp_dir, filename)
                    with open(target_path, "wb") as target:
                        shutil.copyfileobj(source, target)
                        
            # 압축 해제된 임시 파일 스캔
            for fname in os.listdir(tmp_dir):
                ext = Path(fname).suffix.lower()
                if ext not in SUPPORTED_EXTENSIONS or ext == '.zip':
                    continue
                full_path = os.path.join(tmp_dir, fname)
                zip_stem = Path(zip_filepath).stem
                stem_key = os.path.join(parent_root, zip_stem, Path(fname).stem)
                if stem_key not in all_files:
                    all_files[stem_key] = {}
                all_files[stem_key][ext] = (full_path, top_dir_name)
        except Exception as e:
            logger.error(f"ZIP extraction failed for {zip_filepath}: {e}")

    for top_dir in sorted(media_path.iterdir()):
        if not top_dir.is_dir():
            continue
        if filter_dir and top_dir.name != filter_dir:
            continue

        for root, dirs, filenames in os.walk(top_dir):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for fname in filenames:
                ext = Path(fname).suffix.lower()
                if ext not in SUPPORTED_EXTENSIONS:
                    continue
                
                full_path = os.path.join(root, fname)
                if ext == '.zip':
                    extract_zip(full_path, root, top_dir.name)
                    continue
                    
                stem_key = os.path.join(root, Path(fname).stem)
                if stem_key not in all_files:
                    all_files[stem_key] = {}
                all_files[stem_key][ext] = (full_path, top_dir.name)

    # DOCX 우선 선택: 같은 stem에 docx/doc 있으면 PDF 제외
    DOCX_EXTS = {'.docx', '.doc'}
    PDF_EXT = '.pdf'
    files = []
    for stem_key, ext_map in all_files.items():
        has_docx = any(e in ext_map for e in DOCX_EXTS)
        for ext, (fp, top_dir_name) in sorted(ext_map.items()):
            if has_docx and ext == PDF_EXT:
                logger.info(f'PDF 건너뜀 (DOCX 우선): {os.path.basename(fp)}')
                continue
            files.append((fp, top_dir_name))

    return files, temp_dirs


class Command(BaseCommand):
    help = '미디어 폴더의 모든 문서를 RAG 파이프라인으로 인덱싱합니다.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='파일 목록만 출력 (적재 안 함)')
        parser.add_argument('--reset', action='store_true', help='Qdrant 컬렉션 리셋 후 재적재')
        parser.add_argument('--dir', type=str, default=None, help='특정 최상위 폴더명만 처리')
        parser.add_argument('--skip-existing', action='store_true', default=True,
                            help='이미 indexed 상태인 문서 건너뜀 (기본: True)')

    def handle(self, *args, **options):
        from apps.accounts.models import User
        from apps.workspaces.models import Workspace
        from apps.documents.models import Document, DocumentChunk
        from services.parser import parse_document
        from services.embedding import get_embeddings, get_sparse_embeddings
        from services.qdrant_service import ensure_collection, upsert_chunks

        import os as _os
        media_dir = _os.environ.get('MEDIA_ROOT_OVERRIDE', str(settings.MEDIA_ROOT))
        dry_run = options['dry_run']
        reset = options['reset']
        filter_dir = options.get('dir')

        self.stdout.write(self.style.SUCCESS('=== 재생E AI Agent — RAG 파이프라인 인덱싱 시작 ==='))

        # ── 전제 데이터 준비 ──────────────────────────────────
        admin_user = User.objects.filter(is_superuser=True).first()
        if not admin_user:
            self.stdout.write(self.style.ERROR('관리자 계정이 없습니다. createsuperuser를 먼저 실행하세요.'))
            return

        workspace, _ = Workspace.objects.get_or_create(
            slug='biz-dev',
            defaults={'name': '사업개발실', 'created_by': admin_user}
        )

        # ── Qdrant 컬렉션 준비 ───────────────────────────────
        if reset:
            self.stdout.write('Qdrant 컬렉션 리셋 중...')
            try:
                from qdrant_client import QdrantClient
                client = QdrantClient(url=settings.QDRANT_URL)
                if settings.QDRANT_COLLECTION in [c.name for c in client.get_collections().collections]:
                    client.delete_collection(settings.QDRANT_COLLECTION)
                    self.stdout.write(self.style.WARNING(f'컬렉션 삭제: {settings.QDRANT_COLLECTION}'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'Qdrant 리셋 실패: {e}'))

        ensure_collection()

        # ── 파일 수집 ─────────────────────────────────────────
        files, temp_dirs = _collect_files(media_dir, filter_dir)
        self.stdout.write(f'총 {len(files)}개 파일 발견')

        if dry_run:
            self.stdout.write('\n[DRY-RUN] 파일 목록:')
            for fp, top_dir in files:
                pname = _get_project_name_for_dir(top_dir)
                self.stdout.write(f'  [{pname or "전사공용"}] {os.path.relpath(fp, media_dir)}')
            # 드라이런 후에도 임시 생성된 디렉토리를 정리해야 함
            if temp_dirs:
                for tmp_dir in temp_dirs:
                    try:
                        shutil.rmtree(tmp_dir)
                    except Exception:
                        pass
            return

        # ── 프로젝트 캐시 ─────────────────────────────────────
        project_cache = {}

        # ── 메인 처리 루프 ───────────────────────────────────
        success_count = 0
        skip_count = 0
        error_count = 0
        doc_stats = []

        try:
            for idx, (filepath, top_dir_name) in enumerate(files, 1):
                filepath = _get_safe_path(filepath)
                rel_path = os.path.relpath(filepath, media_dir)
                fname = os.path.basename(filepath)
                ext = Path(fname).suffix.lower().strip('.')

                self.stdout.write(f'\n[{idx}/{len(files)}] {rel_path}')

                try:
                    # 체크섬으로 중복 방지
                    checksum = _get_checksum(filepath)
                    if options['skip_existing']:
                        existing = Document.objects.filter(checksum=checksum, status='indexed').first()
                        if existing:
                            self.stdout.write(f'  → 건너뜀 (이미 인덱싱 완료: {existing.title})')
                            skip_count += 1
                            continue

                    # ── 1. 메타데이터 추출 ────────────────────────────────
                    from services.metadata_extractor import extract_metadata_from_path, get_chunking_strategy
                    meta = extract_metadata_from_path(filepath, media_dir)
                    
                    # 프로젝트 결정 로직 대체 (추출된 meta 사용)
                    project_name = meta['project_name'] or _get_project_name_for_dir(top_dir_name)
                    project = None
                    if project_name:
                        if project_name not in project_cache:
                            project_cache[project_name] = _get_or_create_project(
                                workspace, project_name, admin_user
                            )
                        project = project_cache[project_name]

                    file_size = os.path.getsize(filepath)

                    # Document 레코드 생성/업데이트
                    doc, created = Document.objects.get_or_create(
                        checksum=checksum,
                        defaults={
                            'project': project,
                            'title': fname,
                            'original_filename': fname,
                            'file_type': ext,
                            'storage_uri': filepath,
                            'file_size': file_size,
                            'status': 'parsing',
                            'uploaded_by': admin_user,
                            'metadata': {
                                'source_dir': top_dir_name,
                                'relative_path': rel_path,
                                **meta
                            }
                        }
                    )
                    if not created:
                        doc.status = 'parsing'
                        doc.storage_uri = filepath
                        doc.metadata.update(meta)
                        doc.save(update_fields=['status', 'storage_uri', 'metadata'])

                    # ── 2. 파싱 및 청킹 ──────────────────────────────────
                    strategy = get_chunking_strategy(meta['document_type'])
                    self.stdout.write(f'  → 파싱 중 ({ext}, {strategy} 전략)...')
                    
                    parsed = parse_document(filepath, ext, strategy)
                    chunks_data = parsed.get('chunks', [])
                    page_count = parsed.get('page_count', 0)
                    parsed_status = parsed.get('status', 'indexed')
                    doc_meta = parsed.get('doc_meta', {})

                    # 수동 검토 대기 처리 (글자수 부족 등 구조 붕괴)
                    if parsed_status == 'review_pending':
                        self.stdout.write(self.style.WARNING(f'  → 구조 붕괴/파싱 실패 감지. 수동 검토 대기(review_pending) 상태로 격리.'))
                        doc.status = 'review_pending'
                        doc.save(update_fields=['status'])
                        error_count += 1
                        continue

                    if not chunks_data:
                        self.stdout.write(self.style.WARNING(f'  → 텍스트 없음 (스캔 PDF OCR 실패 또는 빈 파일), 건너뜀'))
                        doc.status = 'failed'
                        doc.save(update_fields=['status'])
                        error_count += 1
                        continue

                    self.stdout.write(f'  → {len(chunks_data)}개 청크, {page_count}페이지')

                    # 통계 수집용
                    clause_chunks = [c for c in chunks_data if c.get('chunk_role') == 'parent']
                    avg_len = sum(len(c['content']) for c in clause_chunks) // len(clause_chunks) if clause_chunks else 0
                    doc_stats.append({
                        'title': fname,
                        'clause_count': len(clause_chunks),
                        'avg_length': avg_len,
                        'is_anomalous': len(clause_chunks) < 3 or avg_len < 100
                    })

                    # 기존 청크 삭제 후 재생성
                    doc.chunks.all().delete()

                    # ── 3. DB 청크 저장 및 Parent-Child 매핑 ─────────────
                    chunk_objects = []
                    parent_map = {}  # index -> UUID
                    
                    # Qdrant UUID 미리 생성하여 의존성 해결
                    for i, c in enumerate(chunks_data):
                        c['id_uuid'] = uuid.uuid4()
                        if c.get('chunk_role') == 'parent':
                            parent_map[i] = c['id_uuid']
                            
                    for i, c in enumerate(chunks_data):
                        # child인 경우 부모 참조
                        parent_chunk_id = None
                        if c.get('chunk_role') == 'child' and 'parent_ref_index' in c:
                            parent_chunk_id = parent_map.get(c['parent_ref_index'])

                        chunk = DocumentChunk(
                            id=c['id_uuid'],
                            document=doc,
                            chunk_index=i,
                            content=c['content'].replace('\x00', ''),
                            page_number=c.get('page_number'),
                            section_title=c.get('section_title', '').replace('\x00', ''),
                            char_start=c.get('char_start', 0),
                            char_end=c.get('char_end', 0),
                            sheet_name=c.get('sheet_name', '').replace('\x00', ''),
                            token_count=len(c['content']) // 4,
                            qdrant_point_id=c['id_uuid'],
                            chunk_role=c.get('chunk_role', 'standalone'),
                            parent_chunk_id=parent_chunk_id,
                            project_name=meta.get('project_name', ''),
                            spc_name=meta.get('spc_name', ''),
                            document_type=meta.get('document_type', ''),
                            sub_type=meta.get('sub_type', ''),
                            metadata={
                                'article_number': c.get('article_number'),
                                'article_title': c.get('article_title', '').replace('\x00', ''),
                                'chunk_type': c.get('chunk_type', 'general'),
                                **c.get('metadata', {})
                            },
                        )
                        chunk_objects.append(chunk)

                    # 일괄 저장
                    DocumentChunk.objects.bulk_create(chunk_objects, batch_size=200)

                    # 임베딩 (배치 처리)
                    doc.status = 'embedding'
                    doc.page_count = page_count

                    # 0단계: 문서 전체 요약 생성 (Contextual Retrieval용)
                    self.stdout.write(f'  → 문서 전역 요약 생성 중...')
                    try:
                        from services.llm import generate_document_summary
                        full_doc_text = "\n".join([c.content for c in chunk_objects])
                        doc_summary = generate_document_summary(doc.title, full_doc_text)
                    except Exception as lme:
                        logger.warning(f"Failed to generate summary: {lme}")
                        doc_summary = f"{doc.title} 문서는 사내 비즈니스 관련 참고 자료입니다."

                    # 문서 레벨 메타데이터 업데이트
                    doc_meta_data = doc.metadata or {}
                    doc_meta_data.update(doc_meta)
                    doc_meta_data['global_summary'] = doc_summary
                    doc.metadata = doc_meta_data
                    doc.save(update_fields=['status', 'page_count', 'metadata'])

                    self.stdout.write(f'  → 임베딩 중 (API 호출)...')
                    
                    # Contextual Retrieval Prepend 적용
                    texts_for_embedding = []
                    for chunk_obj in chunk_objects:
                        loc_label = chunk_obj.section_title or ""
                        proj_label = chunk_obj.project_name or "전사공용"
                        
                        context_prefix = f"[문서 맥락: {proj_label} - {doc.title}"
                        if loc_label:
                            context_prefix += f" / 위치: {loc_label}"
                        context_prefix += f"]\n(문서 개요: {doc_summary})\n\n[본문]\n"
                        
                        texts_for_embedding.append(context_prefix + chunk_obj.content)

                    # 배치 50개씩 처리 및 0.5초 대기 (API Rate limit 배려)
                    all_dense_embeddings = []
                    batch_size = 50
                    import time
                    for b_start in range(0, len(texts_for_embedding), batch_size):
                        batch_texts = texts_for_embedding[b_start:b_start + batch_size]
                        batch_embeddings = get_embeddings(batch_texts)
                        all_dense_embeddings.extend(batch_embeddings)
                        time.sleep(0.5)

                    self.stdout.write(f'  → Sparse 임베딩 중 (로컬 BM25)...')
                    all_sparse_embeddings = get_sparse_embeddings(texts_for_embedding)

                    # None 임베딩 수 확인
                    valid_count = sum(1 for e in all_dense_embeddings if e is not None)
                    self.stdout.write(f'  → 임베딩 완료: {valid_count}/{len(texts_for_embedding)}개 성공')

                    # Qdrant 업서트
                    self.stdout.write(f'  → Qdrant 업서트 중...')
                    upsert_chunks(doc, chunk_objects, all_dense_embeddings, all_sparse_embeddings)

                    # 완료 처리
                    doc.status = 'indexed'
                    from django.utils import timezone
                    doc.indexed_at = timezone.now()
                    doc.save(update_fields=['status', 'indexed_at'])

                    self.stdout.write(self.style.SUCCESS(f'  ✓ 완료'))
                    success_count += 1

                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'  ✗ 오류: {e}'))
                    logger.exception(f'ingest_media error for {filepath}: {e}')
                    error_count += 1
                    try:
                        Document.objects.filter(storage_uri=filepath).update(status='failed')
                    except Exception:
                        pass

            # ── 최종 결과 및 품질 리포트 출력 ─────────────────────────
            self.stdout.write('\n' + '=' * 60)
            self.stdout.write(self.style.SUCCESS(f'인덱싱 완료!'))
            self.stdout.write(f'  성공: {success_count}개')
            self.stdout.write(f'  건너뜀: {skip_count}개 (이미 인덱싱)')
            self.stdout.write(f'  실패/검토대기: {error_count}개')
            
            self.stdout.write('\n' + '-' * 20 + ' 적재 품질 통계 리포트 ' + '-' * 20)
            for stat in doc_stats:
                flag_str = " [⚠️ 비정상 플래그]" if stat['is_anomalous'] else ""
                self.stdout.write(f"- {stat['title']}: 조항 수 {stat['clause_count']}개, 평균 글자수 {stat['avg_length']}자{flag_str}")
            self.stdout.write('=' * 60)

            # Qdrant 포인트 수 확인
            try:
                from qdrant_client import QdrantClient
                client = QdrantClient(url=settings.QDRANT_URL)
                info = client.get_collection(settings.QDRANT_COLLECTION)
                self.stdout.write(f'  Qdrant 총 포인트: {info.points_count}개')
            except Exception:
                pass

        finally:
            if temp_dirs:
                self.stdout.write('\n임시 디렉토리 정리 중...')
                for tmp_dir in temp_dirs:
                    try:
                        shutil.rmtree(tmp_dir)
                        self.stdout.write(f"  → 임시 폴더 삭제 완료: {tmp_dir}")
                    except Exception as e:
                        self.stdout.write(f"  → 임시 폴더 삭제 실패 ({tmp_dir}): {e}")
