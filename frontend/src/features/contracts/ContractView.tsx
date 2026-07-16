import { useState, useEffect } from 'react'
import { contractsApi, type ContractTemplate, type ContractDraft, type ContractReview } from '../../api/client'

export default function ContractView() {
  const [activeTab, setActiveTab] = useState<'generate' | 'review'>('generate');

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'auto' }}>
      <div className="tabs">
        <button className={`tab ${activeTab === 'generate' ? 'active' : ''}`} onClick={() => setActiveTab('generate')}>
          계약서 신규 생성
        </button>
        <button className={`tab ${activeTab === 'review' ? 'active' : ''}`} onClick={() => setActiveTab('review')}>
          계약서 검토
        </button>
      </div>

      {activeTab === 'generate' ? <GeneratePane /> : <ReviewPane />}
    </div>
  );
}

// ─── 계약서 신규 생성 (K-1) ───
function GeneratePane() {
  const [templates, setTemplates] = useState<ContractTemplate[]>([]);
  const [selectedCode, setSelectedCode] = useState('');
  const [keyTerms, setKeyTerms] = useState({
    parties: '',
    capacity: '',
    period: '',
    price: '',
    etc: '',
  });
  const [draft, setDraft] = useState<ContractDraft | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    contractsApi.getTemplates()
      .then(data => {
        const list = data.results || [];
        setTemplates(list);
        if (list.length > 0) setSelectedCode(list[0].code);
      })
      .catch(() => {});
  }, []);

  const handleGenerate = async () => {
    if (!selectedCode) return;
    setIsLoading(true);
    setDraft(null);

    try {
      const result = await contractsApi.createDraft({
        template_code: selectedCode,
        key_terms: keyTerms,
        title: `${templates.find(t => t.code === selectedCode)?.name_ko || ''} 초안`,
      });
      setDraft(result);
    } catch (error) {
      console.error('Draft generation error:', error);
    } finally {
      setIsLoading(false);
    }
  };

  const selectedTemplate = templates.find(t => t.code === selectedCode);

  return (
    <div className="fade-in">
      <div className="section-head">
        <h2>계약서 신규 생성</h2>
        <p>핵심 조건(Key-term)을 입력하면 표준 계약서 양식을 바탕으로 초안을 생성합니다.</p>
      </div>

      <div className="pane-body">
        <div className="grid-2 lean">
          {/* 입력 패널 */}
          <div className="card block">
            <h3><span className="num">1</span>핵심 조건 입력</h3>
            <div className="h-sub">계약의 주요 Key-term을 입력하세요.</div>

            <label className="fld">
              <span className="lab">계약 유형</span>
              <select className="inp" value={selectedCode} onChange={e => setSelectedCode(e.target.value)}>
                {templates.map(t => (
                  <option key={t.code} value={t.code}>{t.name_ko} ({t.code})</option>
                ))}
              </select>
            </label>

            <label className="fld">
              <span className="lab">계약 당사자</span>
              <input className="inp" value={keyTerms.parties} placeholder="예: (주)재생E파워 / ○○에너지(주)"
                onChange={e => setKeyTerms({...keyTerms, parties: e.target.value})} />
            </label>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <label className="fld">
                <span className="lab">계약 용량</span>
                <input className="inp" value={keyTerms.capacity} placeholder="예: 80 MW"
                  onChange={e => setKeyTerms({...keyTerms, capacity: e.target.value})} />
              </label>
              <label className="fld">
                <span className="lab">계약 기간</span>
                <input className="inp" value={keyTerms.period} placeholder="예: 20년"
                  onChange={e => setKeyTerms({...keyTerms, period: e.target.value})} />
              </label>
            </div>

            <label className="fld">
              <span className="lab">매매 단가 / 정산 조건</span>
              <input className="inp" value={keyTerms.price} placeholder="예: SMP+REC 연동, 가중치 1.2"
                onChange={e => setKeyTerms({...keyTerms, price: e.target.value})} />
            </label>

            <label className="fld">
              <span className="lab">기타 관철 조건 (자유 입력)</span>
              <textarea className="inp" value={keyTerms.etc}
                placeholder="예: 계통 접속 지연 시 책임소재, 불가항력 범위, 위약금 상한 등"
                onChange={e => setKeyTerms({...keyTerms, etc: e.target.value})} />
            </label>

            <button className="btn-primary" onClick={handleGenerate} disabled={isLoading || !selectedCode}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2a7 7 0 0 0-7 7c0 3 2 5 4 7l3 6 3-6c2-2 4-4 4-7a7 7 0 0 0-7-7Z"/>
              </svg>
              {isLoading ? '생성 중...' : '계약서 초안 생성'}
            </button>
          </div>

          {/* 결과 패널 */}
          <div className="card block">
            <div className="result-head">
              <div className="rh-l">
                생성된 초안
                {draft && <span className="badge">미리보기</span>}
              </div>
              {draft && (
                <a href={contractsApi.downloadDraft(draft.id)} className="btn-ghost" target="_blank" rel="noreferrer">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                    <path d="M7 10l5 5 5-5M12 15V3"/>
                  </svg>
                  Word 다운로드
                </a>
              )}
            </div>

            {isLoading ? (
              <div style={{ textAlign: 'center', padding: 40, color: 'var(--ink-faint)' }}>
                <div className="loading-dots" style={{ justifyContent: 'center' }}>
                  <span></span><span></span><span></span>
                </div>
                <div style={{ marginTop: 12 }}>AI가 계약서를 작성하고 있습니다...</div>
              </div>
            ) : draft ? (
              <>
                <div className="doc-preview">
                  {draft.generated_content.split('\n').map((line, i) => {
                    if (line.startsWith('# ')) return <h4 key={i}>{line.slice(2)}</h4>;
                    if (line.startsWith('## ')) return <h4 key={i}>{line.slice(3)}</h4>;
                    if (line.startsWith('### ')) return <h4 key={i}>{line.slice(4)}</h4>;
                    if (!line.trim()) return <br key={i} />;
                    return <div className="clause" key={i}>{line}</div>;
                  })}
                </div>
                <div className="note" style={{ marginTop: 14 }}>
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                    <circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>
                  </svg>
                  입력한 Key-term이 {selectedTemplate?.name_ko || '표준'} 양식의 해당 조항에 자동 매핑되었습니다.
                </div>
              </>
            ) : (
              <div style={{ textAlign: 'center', padding: 60, color: 'var(--ink-faint)', fontSize: 13 }}>
                좌측에서 Key-term을 입력하고 "계약서 초안 생성"을 클릭하면<br/>
                AI가 표준 양식 기반 초안을 생성합니다.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── 계약서 검토 (K-2) ───
function ReviewPane() {
  const [file, setFile] = useState<File | null>(null);
  const [instruction, setInstruction] = useState('');
  const [review, setReview] = useState<ContractReview | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) setFile(f);
  };

  const handleReview = async () => {
    if (!instruction.trim()) return;
    setIsLoading(true);
    setReview(null);

    try {
      const formData = new FormData();
      if (file) formData.append('file', file);
      formData.append('review_instruction', instruction);
      formData.append('title', file?.name || '계약서 검토');

      const result = await contractsApi.createReview(formData);
      setReview(result);
    } catch (error) {
      console.error('Review error:', error);
    } finally {
      setIsLoading(false);
    }
  };

  const severityLabels: Record<string, string> = { high: '독소', mid: '불리', low: '누락' };

  return (
    <div className="fade-in">
      <div className="section-head">
        <h2>계약서 검토</h2>
        <p>검토 중인 계약서를 업로드하면, 지시에 따라 조항별 검토 의견을 제시합니다.</p>
      </div>

      <div className="pane-body">
        <div className="grid-2 lean">
          {/* 입력 패널 */}
          <div className="card block">
            <h3><span className="num">1</span>검토 대상 업로드</h3>
            <div className="h-sub">PDF · Word 파일을 올리세요.</div>

            <label htmlFor="review-file-input">
              <div className="drop">
                <div className="d-ic">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                    <path d="M17 8l-5-5-5 5M12 3v12"/>
                  </svg>
                </div>
                <b>파일을 끌어다 놓거나 클릭하여 업로드</b>
                <span>PDF, Word · 최대 50MB</span>
              </div>
            </label>
            <input id="review-file-input" type="file" accept=".pdf,.docx,.doc" style={{ display: 'none' }}
              onChange={handleFileChange} />

            {file && (
              <div className="file-pill">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                  <path d="M14 2v6h6"/>
                </svg>
                {file.name}
                <span className="x" onClick={() => setFile(null)}>✕</span>
              </div>
            )}

            <label className="fld" style={{ marginTop: 18 }}>
              <span className="lab">검토 지시</span>
              <textarea className="inp" value={instruction} onChange={e => setInstruction(e.target.value)}
                placeholder="예: 우리가 매수인 입장일 때 불리한 조항을 찾아주고, 협상에서 관철할 수정안을 제시해줘" />
            </label>

            <button className="btn-primary" onClick={handleReview} disabled={isLoading || !instruction.trim()}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>
              </svg>
              {isLoading ? '검토 중...' : '계약서 검토 실행'}
            </button>
          </div>

          {/* 결과 패널 */}
          <div className="card block">
            <div className="result-head">
              <div className="rh-l">
                검토 결과
                {review && review.findings && (
                  <span className="badge">조항 {review.findings.length}건 지적</span>
                )}
              </div>
              {review && (
                <a href={contractsApi.downloadReview(review.id)} className="btn-ghost" target="_blank" rel="noreferrer">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                    <path d="M7 10l5 5 5-5M12 15V3"/>
                  </svg>
                  Word 다운로드
                </a>
              )}
            </div>

            {isLoading ? (
              <div style={{ textAlign: 'center', padding: 40, color: 'var(--ink-faint)' }}>
                <div className="loading-dots" style={{ justifyContent: 'center' }}>
                  <span></span><span></span><span></span>
                </div>
                <div style={{ marginTop: 12 }}>AI가 계약서를 검토하고 있습니다...</div>
              </div>
            ) : review && review.findings ? (
              <>
                <table className="tbl">
                  <thead>
                    <tr><th>조항</th><th>위험도</th><th>지적 내용 · 수정 방향</th></tr>
                  </thead>
                  <tbody>
                    {review.findings.map(f => (
                      <tr key={f.id}>
                        <td><span className="cell-ref">{f.clause_ref || '—'}</span></td>
                        <td><span className={`sev ${f.severity}`}>{severityLabels[f.severity] || f.severity}</span></td>
                        <td>
                          {f.finding}
                          {f.suggestion && <> → {f.suggestion}</>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {review.summary && (
                  <div className="note" style={{ marginTop: 16 }}>
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                      <circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>
                    </svg>
                    {review.summary}
                  </div>
                )}
              </>
            ) : (
              <div style={{ textAlign: 'center', padding: 60, color: 'var(--ink-faint)', fontSize: 13 }}>
                계약서 파일을 업로드하고 검토 지시를 입력한 후<br/>
                "계약서 검토 실행"을 클릭하세요.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
