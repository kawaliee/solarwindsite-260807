import { useState, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { documentsApi, type MessageSource } from '../../api/client'

interface SourceViewerProps {
  isOpen: boolean;
  onClose: () => void;
  source: MessageSource | null;
}

interface Chunk {
  id: string;
  chunk_index: number;
  content: string;
  page_number: number | null;
  section_title: string;
  sheet_name: string;
  cell_range: string;
}

export default function SourceViewer({ isOpen, onClose, source }: SourceViewerProps) {
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeSheet, setActiveSheet] = useState<string>('');
  const contentRef = useRef<HTMLDivElement>(null);

  // 문서 청크 목록 로드
  useEffect(() => {
    if (isOpen && source?.document_id) {
      setLoading(true);
      documentsApi.chunks(source.document_id)
        .then((data) => {
          setChunks(data);
          // 엑셀일 경우 초기 활성화 시트 지정
          if (data.length > 0) {
            const firstWithSheet = data.find(c => c.sheet_name);
            if (firstWithSheet) {
              const targetSheet = data.find(c => c.id === source.document_chunk_id)?.sheet_name;
              setActiveSheet(targetSheet || firstWithSheet.sheet_name);
            }
          }
        })
        .catch((err) => {
          console.error('Failed to load chunks:', err);
          setChunks([]);
        })
        .finally(() => {
          setLoading(false);
        });
    } else {
      setChunks([]);
    }
  }, [isOpen, source]);

  // 로딩 완료 & 렌더링 이후 하이라이트 단락으로 스크롤 이동
  useEffect(() => {
    if (!loading && chunks.length > 0 && source?.document_chunk_id) {
      const timer = setTimeout(() => {
        const highlightedEl = document.querySelector('.chunk-item.highlight');
        if (highlightedEl) {
          highlightedEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      }, 300); // 렌더링 및 탭 전환 안정성을 위해 약간의 딜레이
      return () => clearTimeout(timer);
    }
  }, [loading, chunks, activeSheet, source]);

  if (!isOpen || !source) return null;

  // 엑셀 시트 탭 목록 추출
  const sheets = Array.from(new Set(chunks.map(c => c.sheet_name).filter(Boolean)));
  const isExcel = sheets.length > 0;

  // 현재 렌더링할 청크 필터링
  const displayedChunks = isExcel
    ? chunks.filter(c => c.sheet_name === activeSheet)
    : chunks;

  // 확장자 아이콘 지정
  const getFileIcon = (title: string) => {
    const lower = title.toLowerCase();
    if (lower.endsWith('.pdf')) {
      return (
        <span className="file-icon pdf">
          PDF
        </span>
      );
    }
    if (lower.endsWith('.xlsx') || lower.endsWith('.xls') || lower.endsWith('.xlsm')) {
      return (
        <span className="file-icon excel">
          EXCEL
        </span>
      );
    }
    return (
      <span className="file-icon doc">
        WORD
      </span>
    );
  };

  return createPortal(
    <div className={`source-viewer-drawer ${isOpen ? 'open' : ''}`}>
      {/* 백드롭 레이어 */}
      <div className="viewer-backdrop" onClick={onClose}></div>

      {/* 본체 드로워 */}
      <div className="viewer-content">
        {/* 헤더 */}
        <div className="viewer-header">
          <div className="title-area">
            {getFileIcon(source.display_title)}
            <div className="title-text">
              <h4>{source.display_title}</h4>
              <p>{source.location_label || '원문 보기'}</p>
            </div>
          </div>
          <button className="close-btn" onClick={onClose} title="닫기">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18"></line>
              <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
          </button>
        </div>

        {/* 엑셀 시트 탭 */}
        {isExcel && (
          <div className="viewer-tabs">
            {sheets.map(sheet => (
              <button
                key={sheet}
                className={`tab-item ${activeSheet === sheet ? 'active' : ''}`}
                onClick={() => setActiveSheet(sheet)}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="tab-icon">
                  <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
                  <line x1="9" y1="3" x2="9" y2="21"></line>
                  <line x1="15" y1="3" x2="15" y2="21"></line>
                  <line x1="3" y1="9" x2="21" y2="9"></line>
                  <line x1="3" y1="15" x2="21" y2="15"></line>
                </svg>
                {sheet}
              </button>
            ))}
          </div>
        )}

        {/* 본문 텍스트 영역 */}
        <div className="viewer-body" ref={contentRef}>
          {loading ? (
            <div className="viewer-loading">
              <div className="spinner"></div>
              <p>원문 데이터를 불러오는 중입니다...</p>
            </div>
          ) : displayedChunks.length === 0 ? (
            <div className="viewer-empty">
              <p>표시할 원문 텍스트가 없습니다.</p>
            </div>
          ) : (
            <div className="chunks-list">
              {displayedChunks.map((chunk) => {
                const isHighlighted = chunk.id === source.document_chunk_id;
                return (
                  <div
                    key={chunk.id}
                    className={`chunk-item ${isHighlighted ? 'highlight' : ''}`}
                  >
                    {/* 메타 인덱스 (페이지 또는 줄 번호) */}
                    <div className="chunk-meta">
                      {chunk.page_number && <span className="meta-page">p.{chunk.page_number}</span>}
                      {chunk.section_title && <span className="meta-sec">{chunk.section_title}</span>}
                      {chunk.cell_range && <span className="meta-cell">{chunk.cell_range}</span>}
                      {isHighlighted && <span className="meta-badge">참조 영역</span>}
                    </div>
                    {/* 청크 텍스트 본문 */}
                    <div className="chunk-text">
                      {chunk.content.split('\n').map((line, i) => (
                        <p key={i}>{line}</p>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body
  );
}
