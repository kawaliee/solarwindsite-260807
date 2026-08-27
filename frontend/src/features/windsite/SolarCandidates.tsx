import { useEffect, useState } from 'react'
import { windsiteApi } from './api'
import type { SitePlan, SiteProject } from './types'

/**
 * 후보 필지 담기·비교 (기획서 §3.3 · R-04)
 * ---------------------------------------------------------------
 * 발굴한 사업구역을 후보로 쌓고 나란히 견준다.
 *
 * 화면 구조는 풍력의 '저장한 배치안'(SavedPlans)과 같게 맞췄다. 두 화면이
 * 하는 일이 같은데 생김새가 다르면 쓰는 사람이 매번 다시 익혀야 한다.
 *
 * ■ 무엇을 저장하고 무엇을 저장하지 않는가
 *
 * 저장하는 것은 **좌표와 검토 조건**이다. 62개 항목의 판정 전문은 담지
 * 않는다(§4.8). 규제·조례는 개정되므로 몇 달 뒤에 옛 판정을 그대로 펼쳐
 * 보이면 지금도 그런 줄 알게 된다. 좌표만 남기면 언제 불러도 그 시점의
 * 규제로 다시 판정할 수 있다.
 *
 * 다만 면적·발전시간 같은 **요약은 산출 시점과 함께** 남긴다. 후보를 견줄
 * 때마다 몇 분씩 재검토할 수는 없기 때문이다.
 */

interface Props {
  /** 새로고침 신호 — 저장 직후 목록을 다시 받는다 */
  refreshKey: number;
  /** 후보를 지도·검토 조건으로 되돌린다 */
  onLoad: (plan: SitePlan) => void;
}

/**
 * 검토 시각. **분까지 보인다.**
 *
 * 날짜만 적으면 같은 날 여러 번 검토한 것이 구분되지 않는다. 규제·조례는
 * 개정되고 조회 자료도 바뀌므로, 같은 부지를 오전과 오후에 검토하면 결과가
 * 다를 수 있다 — 어느 것이 최신인지 가리려면 시각이 있어야 한다.
 */
const stamp = (s: string) => (s || '').replace('T', ' ').slice(0, 16);

/** 저장된 요약에서 꺼내 쓰는 값. 없으면 '-'로 둔다 — 0으로 채우지 않는다. */
function num(v: unknown, digits = 1): string {
  return typeof v === 'number' ? v.toLocaleString(undefined, {
    minimumFractionDigits: digits, maximumFractionDigits: digits }) : '-';
}

export default function SolarCandidates({ refreshKey, onLoad }: Props) {
  const [projects, setProjects] = useState<SiteProject[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [picked, setPicked] = useState<string[]>([]);
  const [editing, setEditing] = useState<SitePlan | null>(null);
  const [draft, setDraft] = useState({ name: '', note: '', reviewer: '' });

  async function reload() {
    setLoading(true);
    try {
      // 에너지원으로 가른다. 태양광 후보가 풍력 배치안 목록에 섞이면
      // 무엇을 견주는 것인지 알 수 없다.
      setProjects((await windsiteApi.projects('SOLAR')).results);
      setError('');
    } catch (e) {
      setError(e instanceof Error ? e.message : '후보를 불러오지 못했습니다.');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void reload(); /* eslint-disable-next-line */ }, [refreshKey]);

  const toggle = (id: string) =>
    setPicked(p => (p.includes(id) ? p.filter(x => x !== id) : [...p, id]));

  const plans = projects.flatMap(p => p.plans ?? []);
  const chosen = plans.filter(x => picked.includes(x.id));

  async function removeProject(p: SiteProject) {
    if (!window.confirm(`"${p.name}" 사업과 후보 ${p.plan_count}건을 지웁니다.`)) return;
    try {
      await windsiteApi.deleteProject(p.id);
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : '삭제하지 못했습니다.');
    }
  }

  async function removePlan(x: SitePlan) {
    if (!window.confirm(`"${x.name}"을 지웁니다.`)) return;
    try {
      await windsiteApi.deletePlan(x.id);
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : '삭제하지 못했습니다.');
    }
  }

  async function saveEdit() {
    if (!editing) return;
    try {
      await windsiteApi.patchPlan(editing.id,
        { name: draft.name.trim(), note: draft.note,
          reviewer: draft.reviewer.trim() });
      setEditing(null);
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : '수정하지 못했습니다.');
    }
  }

  if (loading) return <p className="ops-empty">불러오는 중…</p>;

  return (
    <div className="ws-saved">
      {error && <p className="ws-error">{error}</p>}

      {!plans.length ? (
        <p className="ops-empty">
          담아 둔 후보가 없습니다. 지도에서 사업구역을 그린 뒤 <b>후보로 담기</b>를
          누르면 여기에 쌓이고, 여러 후보를 골라 나란히 견줄 수 있습니다.
        </p>
      ) : (
        projects.map(p => (
          <div key={p.id} className="ws-proj">
            <div className="ws-proj-hd">
              <div>
                <b>{p.name}</b>
                <em>{p.plan_count}건</em>
                {p.description && <span className="desc">{p.description}</span>}
              </div>
              <button className="ws-link danger" onClick={() => void removeProject(p)}>
                사업 삭제
              </button>
            </div>

            <div className="ws-planlist">
              {(p.plans ?? []).map(x => {
                const s = (x.summary ?? {}) as Record<string, unknown>;
                return (
                  <div key={x.id} className={`ws-plan${picked.includes(x.id) ? ' on' : ''}`}>
                    <label className="pick">
                      <input type="checkbox" checked={picked.includes(x.id)}
                        onChange={() => toggle(x.id)} />
                    </label>
                    <div className="body">
                      <div className="ln1">
                        <b>{x.name}</b>
                        {typeof s.parcels === 'number' && (
                          <span className="chip">{s.parcels}필지</span>
                        )}
                        {typeof s.total_ha === 'number' && (
                          <span className="chip">{num(s.total_ha, 2)} ha</span>
                        )}
                        {x.capacity_mw ? <span className="chip">{x.capacity_mw} MW</span> : null}
                        {typeof s.capacity_factor === 'number' && (
                          <span className="chip">
                            {num(s.hours_per_day, 2)} h/일 · {num(s.capacity_factor, 1)}%
                          </span>
                        )}
                      </div>
                      {x.note && <p className="note">{x.note}</p>}
                      <p className="meta">
                        {x.sigungu && <>{x.sido} {x.sigungu} · </>}
                        저장 {stamp(x.created_at)}
                        {/* 판정은 그 시점 규제 기준이다. 날짜를 반드시 붙인다. */}
                        {x.evaluated_at
                          ? ` · 판정 ${stamp(x.evaluated_at)} 기준`
                          : ' · 판정 없이 좌표만 저장'}
                        {x.reviewer && <> · 검토자 <b>{x.reviewer}</b></>}
                        {x.permit_date && ` · 허가일 ${x.permit_date}`}
                      </p>
                    </div>
                    <div className="acts">
                      <button className="ws-btn2" onClick={() => onLoad(x)}>불러오기</button>
                      <button className="ws-link" onClick={() => {
                        setEditing(x); setDraft({ name: x.name, note: x.note, reviewer: x.reviewer || '' })
                      }}>이름·메모</button>
                      <button className="ws-link danger"
                        onClick={() => void removePlan(x)}>삭제</button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))
      )}

      {chosen.length >= 2 && <Compare plans={chosen} />}
      {chosen.length === 1 && (
        <p className="ws-hint">견주려면 후보를 <b>2건 이상</b> 고르십시오.</p>
      )}

      {editing && (
        <div className="ws-modal-bg" onClick={() => setEditing(null)}>
          <div className="ws-modal" onClick={e => e.stopPropagation()}>
            <b>후보 수정</b>
            <label className="ws-fld">
              <span>후보명</span>
              <input value={draft.name} maxLength={120}
                onChange={e => setDraft(d => ({ ...d, name: e.target.value }))} />
            </label>
            <label className="ws-fld">
              <span>검토자 <em>(담당자명)</em></span>
              <input value={draft.reviewer} maxLength={60} placeholder="예) 홍길동"
                onChange={e => setDraft(d => ({ ...d, reviewer: e.target.value }))} />
            </label>
            <label className="ws-fld">
              <span>메모 <em>(무엇을 바꿨는지 적어두면 나중에 알아봅니다)</em></span>
              <textarea rows={3} value={draft.note}
                onChange={e => setDraft(d => ({ ...d, note: e.target.value }))} />
            </label>
            <div className="ws-modal-acts">
              <button className="ws-btn2" onClick={() => setEditing(null)}>취소</button>
              <button className="btn-primary" onClick={() => void saveEdit()}
                disabled={!draft.name.trim()}>저장</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * 후보 비교.
 *
 * 순위를 매기지 않는다. 면적이 넓은 곳과 발전시간이 좋은 곳과 제약이 적은
 * 곳이 서로 다르고, 무엇을 우선할지는 사업 판단이기 때문이다. 값을 나란히
 * 놓고 **각 항목의 최고값만 표시**해 비교를 돕는 데까지가 역할이다.
 */
function Compare({ plans }: { plans: SitePlan[] }) {
  const rows: { key: string; label: string; unit: string; digits: number }[] = [
    { key: 'parcels', label: '필지 수', unit: '필지', digits: 0 },
    { key: 'total_ha', label: '검토 면적', unit: 'ha', digits: 2 },
    { key: 'free_ha', label: '가용(엄격)', unit: 'ha', digits: 2 },
    { key: 'conditional_ha', label: '조건부', unit: 'ha', digits: 2 },
    { key: 'blocked_ha', label: '배제', unit: 'ha', digits: 2 },
    { key: 'hours_per_day', label: '일 평균 발전시간', unit: 'h/일', digits: 2 },
    { key: 'capacity_factor', label: '이용률', unit: '%', digits: 1 },
  ];
  //: 값이 클수록 좋은 항목. 배제·조건부는 작을수록 좋아 최고값을 칠하지 않는다.
  const higherIsBetter = new Set(
    ['parcels', 'total_ha', 'free_ha', 'hours_per_day', 'capacity_factor']);

  const val = (x: SitePlan, k: string) => {
    const v = (x.summary as Record<string, unknown> | undefined)?.[k];
    return typeof v === 'number' ? v : null;
  };

  return (
    <div className="ws-group">
      <p className="ops-section-t">후보 비교 <em className="ws-catcount"> {plans.length}건</em></p>
      <p className="ws-area-note">
        저장 시점의 요약입니다. 규제·조례는 개정되므로 판정 기준일이 서로 다르면
        그대로 견주기 어렵습니다 — 다시 검토해 값을 갱신하십시오.
      </p>
      <div className="ws-table-wrap">
        <table className="ws-table">
          <thead>
            <tr>
              <th>항목</th>
              {plans.map(x => (
                <th key={x.id}>{x.project_name}<br />
                  <span className="ws-cand-sub">{x.name}</span></th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(r => {
              const vs = plans.map(x => val(x, r.key));
              const nums = vs.filter((v): v is number => v != null);
              const best = higherIsBetter.has(r.key) && nums.length > 1
                ? Math.max(...nums) : null;
              return (
                <tr key={r.key}>
                  <th>{r.label}</th>
                  {vs.map((v, i) => (
                    <td key={i} className={best != null && v === best ? 'ws-best' : ''}>
                      {v == null ? '-'
                        : `${v.toLocaleString(undefined, {
                            minimumFractionDigits: r.digits,
                            maximumFractionDigits: r.digits })} ${r.unit}`}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="ws-footnote">
        <b>순위를 매기지 않습니다.</b> 면적이 넓은 곳과 발전시간이 좋은 곳과 제약이
        적은 곳이 서로 다르며, 무엇을 우선할지는 사업 판단입니다. 굵게 표시한 것은
        각 항목의 최고값일 뿐 종합 우열이 아닙니다.
      </p>
    </div>
  );
}
