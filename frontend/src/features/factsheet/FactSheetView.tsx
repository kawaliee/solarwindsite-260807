import { useCallback, useEffect, useMemo, useState } from 'react'
import { factsheetApi } from './api'
import {
  CONFIDENCE_CLASS,
  CONFIDENCE_CYCLE,
  STAGE_LABEL,
  type Cell,
  type ColumnDef,
  type FactSheet,
  type FactSheetSchema,
  type FactSheetSummary,
  type FieldDef,
  type PublishResult,
  type SectionDef,
  type SheetData,
} from './types'

const STAGE_ORDER: Record<string, number> = { dev: 1, build: 2, ops: 3 };

// ─────────────────────────────────────────────────────────────────────
// 데이터 헬퍼
// ─────────────────────────────────────────────────────────────────────

function sectionFields(section: SectionDef): FieldDef[] {
  return [...(section.fields ?? []), ...(section.groups ?? []).flatMap(g => g.fields)];
}

function readCell(data: SheetData, secId: string, key: string): Cell {
  const cell = data?.[secId]?.[key];
  return cell && typeof cell === 'object' ? (cell as Cell) : {};
}

function readNote(data: SheetData, secId: string): string {
  const n = data?.[secId]?._note;
  return typeof n === 'string' ? n : '';
}

function readRows(data: SheetData, secId: string, key: string): Record<string, string>[] {
  const v = readCell(data, secId, key).v;
  return Array.isArray(v) ? v : [];
}

/**
 * 양식이 미리 채워 둔 열(마일스톤명·협의명 등)의 키.
 * 이를 구분하지 않으면 폼을 열어 두기만 해도 모든 표가 '입력됨'으로 집계된다.
 * 백엔드 schema.preset_keys 와 같은 규칙이다.
 */
function presetKeys(field: FieldDef): Set<string> {
  const keys = new Set<string>();
  (field.rows ?? []).forEach(r => {
    Object.entries(r).forEach(([k, v]) => { if (String(v ?? '').trim()) keys.add(k); });
  });
  return keys;
}

/** 표의 한 행에 사용자가 실제로 채운 값이 있는가 */
function rowHasInput(row: Record<string, string>, field: FieldDef): boolean {
  const preset = presetKeys(field);
  return Object.entries(row).some(([k, v]) => !preset.has(k) && String(v ?? '').trim());
}

/** 필드 하나에 입력값이 있는가 */
function fieldHasInput(data: SheetData, secId: string, field: FieldDef): boolean {
  if (field.type === 'table') {
    return readRows(data, secId, field.key).some(r => rowHasInput(r, field));
  }
  return !!String(readCell(data, secId, field.key).v ?? '').trim();
}

/** 스키마에 기본 행이 정의된 표(마일스톤·개별법 협의 등)를 최초 1회 채워 넣는다. */
function hydrate(data: SheetData, schema: FactSheetSchema): SheetData {
  const next: SheetData = { ...(data ?? {}) };
  schema.sections.forEach(sec => {
    sectionFields(sec).forEach(f => {
      if (f.type !== 'table' || !f.rows?.length) return;
      if (readRows(next, sec.id, f.key).length) return;
      next[sec.id] = {
        ...(next[sec.id] ?? {}),
        [f.key]: { v: f.rows.map(r => ({ ...r })) },
      };
    });
  });
  return next;
}

// ─────────────────────────────────────────────────────────────────────
// 메인 뷰
// ─────────────────────────────────────────────────────────────────────

export default function FactSheetView() {
  const [schema, setSchema] = useState<FactSheetSchema | null>(null);
  const [sheets, setSheets] = useState<FactSheetSummary[]>([]);
  const [sheet, setSheet] = useState<FactSheet | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [toast, setToast] = useState('');
  const [openIds, setOpenIds] = useState<Record<string, boolean>>({ basic: true });
  const [preview, setPreview] = useState<{ filename: string; markdown: string } | null>(null);
  const [newName, setNewName] = useState<string | null>(null);
  const [published, setPublished] = useState<PublishResult | null>(null);

  // ── 초기 로드 ──────────────────────────────────────────────
  useEffect(() => {
    factsheetApi.schema().then(setSchema).catch(() => setError('입력 항목 정의를 불러오지 못했습니다.'));
    refreshList();
  }, []);

  const refreshList = useCallback(async () => {
    try {
      const d = await factsheetApi.list();
      setSheets(d.results);
      return d.results;
    } catch {
      setSheets([]);
      return [];
    }
  }, []);

  const flash = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(''), 2600);
  };

  // ── 선택 / 생성 / 삭제 ─────────────────────────────────────
  const select = async (id: string) => {
    if (dirty && !confirm('저장하지 않은 변경사항이 있습니다. 이동하시겠습니까?')) return;
    setError('');
    try {
      const s = await factsheetApi.get(id);
      setSheet(schema ? { ...s, data: hydrate(s.data, schema) } : s);
      setDirty(false);
      setOpenIds({ basic: true });
      setPublished(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : '불러오기에 실패했습니다.');
    }
  };

  const createNew = async () => {
    const name = (newName ?? '').trim();
    if (!name) return;
    setBusy(true);
    try {
      const s = await factsheetApi.create({ name, stage: 'dev', data: {} });
      await refreshList();
      setSheet(schema ? { ...s, data: hydrate(s.data, schema) } : s);
      setDirty(false);
      setOpenIds({ basic: true });
      setNewName(null);
      setPublished(null);
      flash('새 Fact-sheet를 만들었습니다.');
    } catch (e) {
      setError(e instanceof Error ? e.message : '생성에 실패했습니다.');
    } finally {
      setBusy(false);
    }
  };

  const removeSheet = async () => {
    if (!sheet) return;
    if (!confirm(`「${sheet.name}」 Fact-sheet를 삭제하시겠습니까? 되돌릴 수 없습니다.`)) return;
    setBusy(true);
    try {
      await factsheetApi.remove(sheet.id);
      setSheet(null);
      setDirty(false);
      await refreshList();
      flash('삭제했습니다.');
    } catch (e) {
      setError(e instanceof Error ? e.message : '삭제에 실패했습니다.');
    } finally {
      setBusy(false);
    }
  };

  // ── 편집 ───────────────────────────────────────────────────
  const patch = (p: Partial<FactSheet>) => {
    setSheet(s => (s ? { ...s, ...p } : s));
    setDirty(true);
  };

  const setCell = (secId: string, key: string, cell: Cell) => {
    setSheet(s => {
      if (!s) return s;
      const section = { ...(s.data?.[secId] ?? {}) };
      section[key] = { ...readCell(s.data, secId, key), ...cell };
      return { ...s, data: { ...s.data, [secId]: section } };
    });
    setDirty(true);
  };

  const setNote = (secId: string, note: string) => {
    setSheet(s => (s ? { ...s, data: { ...s.data, [secId]: { ...(s.data?.[secId] ?? {}), _note: note } } } : s));
    setDirty(true);
  };

  // ── 저장 / 내보내기 / 발행 ─────────────────────────────────
  const save = async () => {
    if (!sheet) return;
    setBusy(true);
    setError('');
    try {
      const saved = await factsheetApi.update(sheet.id, {
        name: sheet.name,
        aliases: sheet.aliases,
        spc_name: sheet.spc_name,
        stage: sheet.stage,
        as_of_date: sheet.as_of_date || null,
        author: sheet.author,
        pjt_folder: sheet.pjt_folder,
        data: sheet.data,
      });
      setSheet(schema ? { ...saved, data: hydrate(saved.data, schema) } : saved);
      setDirty(false);
      await refreshList();
      flash('저장했습니다.');
    } catch (e) {
      setError(e instanceof Error ? e.message : '저장에 실패했습니다.');
    } finally {
      setBusy(false);
    }
  };

  const showPreview = async () => {
    if (!sheet) return;
    if (dirty) {
      setError('미리보기는 저장된 내용을 기준으로 만듭니다. 먼저 저장하십시오.');
      return;
    }
    setBusy(true);
    try {
      setPreview(await factsheetApi.markdown(sheet.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : '미리보기 생성에 실패했습니다.');
    } finally {
      setBusy(false);
    }
  };

  const publish = async () => {
    if (!sheet) return;
    if (dirty) {
      setError('저장하지 않은 변경사항이 있습니다. 먼저 저장하십시오.');
      return;
    }
    const folder = (sheet.pjt_folder || sheet.name).trim();
    if (!confirm(
      `media/${folder}/사업개요/ 폴더에 마크다운을 기록합니다.\n\n` +
      `기존 파일이 있으면 덮어씁니다. 계속하시겠습니까?`
    )) return;

    setBusy(true);
    setError('');
    try {
      const r = await factsheetApi.publish(sheet.id, folder);
      await refreshList();
      const s = await factsheetApi.get(sheet.id);
      setSheet(schema ? { ...s, data: hydrate(s.data, schema) } : s);
      setPublished(r);
      flash('마크다운을 코퍼스 폴더에 기록했습니다.');
    } catch (e) {
      setError(e instanceof Error ? e.message : '발행에 실패했습니다.');
    } finally {
      setBusy(false);
    }
  };

  // ── 파생값 ─────────────────────────────────────────────────
  const completeness = useMemo(() => {
    if (!schema || !sheet) return { filled: 0, total: 0 };
    let filled = 0;
    let total = 0;
    schema.sections.forEach(sec => {
      sectionFields(sec).forEach(f => {
        if (!f.star) return;
        total += 1;
        if (fieldHasInput(sheet.data, sec.id, f)) filled += 1;
      });
    });
    return { filled, total };
  }, [schema, sheet]);

  if (!schema) {
    return <div className="fs-view"><p className="ops-empty">{error || '입력 항목을 불러오는 중입니다…'}</p></div>;
  }

  const ratio = completeness.total ? completeness.filled / completeness.total : 0;

  return (
    <div className="fs-view">
      <div className="fs-layout">
        {/* ── 사업 목록 ── */}
        <aside className="fs-list-pane">
          <div className="ops-card">
            <div className="ops-card-hd">
              <span className="tag">SHEETS</span> 등록 사업
              <span className="fs-count">{sheets.length}</span>
            </div>
            <div className="fs-list">
              {sheets.length === 0 && <p className="fs-list-empty">등록된 사업이 없습니다.</p>}
              {sheets.map(s => (
                <button
                  key={s.id}
                  className={`fs-list-row${sheet?.id === s.id ? ' on' : ''}`}
                  onClick={() => select(s.id)}
                >
                  <span className={`fs-stage-dot ${s.stage}`} />
                  <span className="nm">
                    <b>{s.name}</b>
                    <small>
                      {s.spc_name || '법인명 미입력'}
                      {s.capacity_ac ? ` · ${s.capacity_ac} MW` : ''}
                    </small>
                  </span>
                  <span className="fs-mini-ratio" title="⭐ 핵심 항목 충족률">
                    {Math.round((s.completeness?.ratio ?? 0) * 100)}%
                  </span>
                </button>
              ))}
            </div>
            <div className="fs-list-foot">
              {newName === null ? (
                <button className="btn-primary fs-newbtn" onClick={() => setNewName('')} disabled={busy}>
                  + 새 사업 Fact-sheet
                </button>
              ) : (
                <div className="fs-newrow">
                  <input
                    className="fs-input" autoFocus value={newName}
                    placeholder="대표 사업명 (예: 당진행복솔라)"
                    onChange={e => setNewName(e.target.value)}
                    onKeyDown={e => {
                      if (e.key === 'Enter') createNew();
                      if (e.key === 'Escape') setNewName(null);
                    }}
                  />
                  <button className="btn-primary" onClick={createNew} disabled={busy || !newName.trim()}>
                    만들기
                  </button>
                  <button className="fs-btn" onClick={() => setNewName(null)}>취소</button>
                </div>
              )}
            </div>
          </div>

          <p className="fs-guide">
            <b>왜 사업마다 한 장인가</b><br />
            검색은 섹션 단위로 쪼개져 들어옵니다. 사업명이 없는 조각은 어느 사업 수치인지
            알 수 없어 사업 간 수치가 섞입니다. 발행되는 마크다운은 섹션 제목마다 사업명을
            반복하고, 빈칸은 <code>미확인</code>으로 채워 챗봇이 추측하지 않게 합니다.
          </p>
        </aside>

        {/* ── 입력 폼 ── */}
        <div className="fs-form-pane">
          {!sheet ? (
            <p className="ops-empty">왼쪽에서 사업을 선택하거나 새 Fact-sheet를 만드십시오.</p>
          ) : (
            <>
              {/* 식별 정보 */}
              <div className="ops-card fs-idcard">
                <div className="ops-card-hd">
                  <span className="tag">IDENTITY</span> 사업 식별 정보
                  <span className="fs-req">별칭까지 채워야 검색에서 사업을 놓치지 않습니다</span>
                </div>
                <div className="ops-card-bd">
                  <div className="fs-field">
                    <div className="fs-label">
                      대표 사업명 <span className="fs-star">필수</span>
                      <em>부르는 이름이 여럿이면 별칭으로 모두 등록</em>
                    </div>
                    <div className="fs-aliasrow">
                      <input
                        className="fs-input fs-name"
                        value={sheet.name}
                        placeholder="대표 사업명"
                        onChange={e => patch({ name: e.target.value })}
                      />
                      {sheet.aliases.map((a, i) => (
                        <span key={i} className="fs-alias">
                          <span className="fs-slash">/</span>
                          <input
                            className="fs-input fs-aliasinput"
                            value={a}
                            placeholder="유사 명칭"
                            onChange={e => {
                              const next = [...sheet.aliases];
                              next[i] = e.target.value;
                              patch({ aliases: next });
                            }}
                          />
                          <button
                            className="fs-alias-x"
                            title="별칭 삭제"
                            onClick={() => patch({ aliases: sheet.aliases.filter((_, j) => j !== i) })}
                          >×</button>
                        </span>
                      ))}
                      <button className="fs-addalias" onClick={() => patch({ aliases: [...sheet.aliases, ''] })}>
                        + 별칭
                      </button>
                    </div>
                  </div>

                  <div className="fs-grid4">
                    <label className="fs-field">
                      <div className="fs-label">SPC 법인명</div>
                      <input className="fs-input" value={sheet.spc_name} placeholder="예: 당진행복솔라 주식회사"
                        onChange={e => patch({ spc_name: e.target.value })} />
                    </label>
                    <label className="fs-field">
                      <div className="fs-label">기준일 <em>낡은 개요는 확신에 찬 오답이 된다</em></div>
                      <input className="fs-input" type="date" value={sheet.as_of_date ?? ''}
                        onChange={e => patch({ as_of_date: e.target.value })} />
                    </label>
                    <label className="fs-field">
                      <div className="fs-label">작성자</div>
                      <input className="fs-input" value={sheet.author} placeholder="작성자명"
                        onChange={e => patch({ author: e.target.value })} />
                    </label>
                    <label className="fs-field">
                      <div className="fs-label">RAG 폴더 <em>media 하위 PJT 폴더명</em></div>
                      <input className="fs-input" value={sheet.pjt_folder}
                        placeholder="예: 당진PJT(당진행복솔라)_1단계"
                        onChange={e => patch({ pjt_folder: e.target.value })} />
                    </label>
                  </div>
                </div>
              </div>

              {/* 단계 선택 */}
              <div className="fs-stages">
                {schema.stages.map(st => (
                  <button
                    key={st.key}
                    className={`fs-stage ${st.key}${sheet.stage === st.key ? ' on' : ''}`}
                    onClick={() => patch({ stage: st.key })}
                  >
                    <span className="no">{st.no}</span>
                    <b>{st.title}</b>
                    <span className="desc">{st.desc}</span>
                  </button>
                ))}
              </div>

              {/* 완성도 */}
              <div className="fs-progress">
                <div className="fs-progress-bar">
                  <span style={{ width: `${Math.round(ratio * 100)}%` }} />
                </div>
                <span className="fs-progress-txt">
                  ⭐ 핵심 항목 <b>{completeness.filled}</b> / {completeness.total}
                  <em>모르는 곳은 비워 두십시오 — 발행 시 「미확인」으로 표기됩니다</em>
                </span>
              </div>

              {/* 섹션 */}
              {schema.sections.map(sec => (
                <SectionCard
                  key={sec.id}
                  section={sec}
                  data={sheet.data}
                  activeStage={sheet.stage}
                  open={!!openIds[sec.id]}
                  onToggle={() => setOpenIds(o => ({ ...o, [sec.id]: !o[sec.id] }))}
                  onCell={setCell}
                  onNote={setNote}
                />
              ))}

              {published && (
                <div className="fs-publish-result">
                  <div className="fs-publish-hd">
                    <b>코퍼스 폴더에 기록했습니다</b>
                    <span>{published.bytes.toLocaleString()} bytes</span>
                    <button className="fs-modal-x" onClick={() => setPublished(null)}>×</button>
                  </div>
                  <p className="fs-publish-path">media/{published.published_path.replace(/\\/g, '/')}</p>
                  <p className="fs-publish-note">{published.note}</p>
                  <code className="fs-publish-cmd">
                    docker compose exec backend {published.ingest_command}
                  </code>
                </div>
              )}

              {error && <p className="fs-error">{error}</p>}

              {/* 하단 액션 */}
              <div className="fs-actions">
                <button className="fs-btn ghost danger" onClick={removeSheet} disabled={busy}>삭제</button>
                <span className="fs-spacer" />
                {sheet.published_at && (
                  <span className="fs-published" title={sheet.published_path}>
                    최근 발행 {new Date(sheet.published_at).toLocaleString('ko-KR')}
                  </span>
                )}
                <button className="fs-btn ghost" onClick={showPreview} disabled={busy}>MD 미리보기</button>
                <a className="fs-btn ghost" href={factsheetApi.downloadUrl(sheet.id)}>MD 내려받기</a>
                <button className="fs-btn ghost" onClick={publish} disabled={busy}>RAG 코퍼스로 발행</button>
                <button className="btn-primary" onClick={save} disabled={busy || !dirty}>
                  {busy ? '처리 중…' : dirty ? '저장' : '저장됨'}
                </button>
              </div>
            </>
          )}
        </div>
      </div>

      {toast && <div className="fs-toast">{toast}</div>}

      {preview && (
        <div className="fs-modal" onClick={() => setPreview(null)}>
          <div className="fs-modal-box" onClick={e => e.stopPropagation()}>
            <div className="fs-modal-hd">
              <b>{preview.filename}</b>
              <span className="fs-modal-meta">{preview.markdown.length.toLocaleString()}자</span>
              <button className="fs-modal-x" onClick={() => setPreview(null)}>×</button>
            </div>
            <pre className="fs-modal-body">{preview.markdown}</pre>
          </div>
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// 섹션 카드
// ─────────────────────────────────────────────────────────────────────

interface SectionCardProps {
  section: SectionDef;
  data: SheetData;
  activeStage: string;
  open: boolean;
  onToggle: () => void;
  onCell: (secId: string, key: string, cell: Cell) => void;
  onNote: (secId: string, note: string) => void;
}

function SectionCard({ section, data, activeStage, open, onToggle, onCell, onNote }: SectionCardProps) {
  const relevant =
    section.stage === null ||
    STAGE_ORDER[section.stage] <= (STAGE_ORDER[activeStage] ?? 1);

  const filled = useMemo(
    () => sectionFields(section).filter(f => fieldHasInput(data, section.id, f)).length,
    [section, data],
  );

  const blocks: [string | null, FieldDef[]][] = [
    ...(section.fields?.length ? ([[null, section.fields]] as [string | null, FieldDef[]][]) : []),
    ...(section.groups ?? []).map(g => [g.title, g.fields] as [string | null, FieldDef[]]),
  ];

  return (
    <div className={`fs-section${open ? ' open' : ''}${relevant ? ' relevant' : ''}`}
      data-stage={section.stage ?? 'all'}>
      <button className="fs-sec-hd" onClick={onToggle}>
        <span className="fs-sec-ic">{section.icon}</span>
        <span className="fs-sec-t">
          <b>{section.no}. {section.title}</b>
          <small>{section.subtitle}</small>
        </span>
        {filled > 0 && <span className="fs-sec-filled">{filled}건 입력</span>}
        {section.stage && (
          <span className={`fs-sec-badge ${section.stage}`}>{STAGE_LABEL[section.stage]}</span>
        )}
        <svg className="fs-sec-chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>

      {open && (
        <div className="fs-sec-bd">
          {section.note && <p className="fs-sec-note">{section.note}</p>}

          {blocks.map(([groupTitle, fields], gi) => (
            <div key={gi}>
              {groupTitle && <div className="fs-subtitle">{groupTitle}</div>}
              {fields.map(f =>
                f.type === 'table' ? (
                  <TableField
                    key={f.key} field={f}
                    rows={readRows(data, section.id, f.key)}
                    onChange={rows => onCell(section.id, f.key, { v: rows })}
                  />
                ) : (
                  <FieldRow
                    key={f.key} field={f}
                    cell={readCell(data, section.id, f.key)}
                    onChange={cell => onCell(section.id, f.key, cell)}
                  />
                )
              )}
            </div>
          ))}

          <div className="fs-field fs-noterow">
            <div className="fs-label">근거 · 비고 <em>근거 문서명을 남기면 챗봇이 원본을 짚어줍니다</em></div>
            <textarea
              className="fs-input fs-textarea"
              value={readNote(data, section.id)}
              placeholder="예: 당진시 도시계획과 공문 2026-118호, 투심위 보고서 p.12"
              onChange={e => onNote(section.id, e.target.value)}
            />
          </div>
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// 단일 필드
// ─────────────────────────────────────────────────────────────────────

function FieldRow({ field, cell, onChange }: {
  field: FieldDef;
  cell: Cell;
  onChange: (cell: Cell) => void;
}) {
  const value = typeof cell.v === 'string' ? cell.v : '';
  const set = (v: string) => onChange({ v });

  return (
    <div className="fs-field fs-row">
      <div className="fs-label">
        {field.star && <span className="fs-starmark" title="핵심 항목">★</span>}
        {field.label}
        {field.hint && <em>{field.hint}</em>}
      </div>
      <div className="fs-control">
        {field.type === 'select' ? (
          <select className="fs-input" value={value} onChange={e => set(e.target.value)}>
            <option value="">선택</option>
            {(field.options ?? []).map(o => <option key={o} value={o}>{o}</option>)}
          </select>
        ) : field.type === 'textarea' ? (
          <textarea className="fs-input fs-textarea" value={value} placeholder={field.placeholder}
            onChange={e => set(e.target.value)} />
        ) : field.type === 'number' ? (
          <span className="fs-unitwrap">
            <input className="fs-input" type="number" step="any" value={value}
              placeholder={field.placeholder ?? '0'} onChange={e => set(e.target.value)} />
            {field.unit && <span className="fs-unit">{field.unit}</span>}
          </span>
        ) : (
          <input className="fs-input" type={field.type === 'date' ? 'date' : 'text'} value={value}
            placeholder={field.placeholder} onChange={e => set(e.target.value)} />
        )}

        {field.status && (
          <ConfidenceToggle value={cell.s ?? ''} onChange={s => onChange({ s })} />
        )}
      </div>
    </div>
  );
}

function ConfidenceToggle({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const next = () => {
    const i = CONFIDENCE_CYCLE.indexOf(value);
    onChange(CONFIDENCE_CYCLE[(i + 1) % CONFIDENCE_CYCLE.length]);
  };
  return (
    <button
      className={`fs-conf ${CONFIDENCE_CLASS[value] ?? 'none'}`}
      onClick={next}
      title="확정도 — 클릭하여 전환 (확정 → 예상 → 미정 → 해당없음 → 미지정)"
    >
      <span className="dot" />{value || '확정도'}
    </button>
  );
}

// ─────────────────────────────────────────────────────────────────────
// 표 필드
// ─────────────────────────────────────────────────────────────────────

function TableField({ field, rows, onChange }: {
  field: FieldDef;
  rows: Record<string, string>[];
  onChange: (rows: Record<string, string>[]) => void;
}) {
  const cols = field.columns ?? [];
  const template = cols.map(c => c.width ?? '1fr').join(' ') + ' 32px';

  const setCellValue = (i: number, key: string, v: string) => {
    const next = rows.map((r, j) => (j === i ? { ...r, [key]: v } : r));
    onChange(next);
  };

  return (
    <div className="fs-tablefield">
      <div className="fs-label fs-tablelabel">
        {field.star && <span className="fs-starmark" title="핵심 항목">★</span>}
        {field.label}
        {field.hint && <em>{field.hint}</em>}
      </div>

      <div className="fs-tbl">
        <div className="fs-tbl-hd" style={{ gridTemplateColumns: template }}>
          {cols.map(c => <span key={c.key}>{c.label}{c.unit ? ` (${c.unit})` : ''}</span>)}
          <span />
        </div>

        {rows.length === 0 && <p className="fs-tbl-empty">행을 추가해 입력하십시오.</p>}

        {rows.map((row, i) => (
          <div className="fs-tbl-row" key={i} style={{ gridTemplateColumns: template }}>
            {cols.map(c => <TableCell key={c.key} col={c} value={row[c.key] ?? ''}
              onChange={v => setCellValue(i, c.key, v)} />)}
            <button className="fs-rowx" title="행 삭제"
              onClick={() => onChange(rows.filter((_, j) => j !== i))}>×</button>
          </div>
        ))}
      </div>

      <button
        className="fs-addrow"
        onClick={() => onChange([...rows, Object.fromEntries(cols.map(c => [c.key, ''])) as Record<string, string>])}
      >
        + 행 추가
      </button>
    </div>
  );
}

function TableCell({ col, value, onChange }: {
  col: ColumnDef;
  value: string;
  onChange: (v: string) => void;
}) {
  if (col.type === 'select') {
    return (
      <select className="fs-input" value={value} onChange={e => onChange(e.target.value)}>
        <option value="">—</option>
        {(col.options ?? []).map(o => <option key={o} value={o}>{o}</option>)}
      </select>
    );
  }
  return (
    <input
      className="fs-input"
      type={col.type === 'number' ? 'number' : col.type === 'date' ? 'date' : 'text'}
      step={col.type === 'number' ? 'any' : undefined}
      value={value}
      onChange={e => onChange(e.target.value)}
    />
  );
}
