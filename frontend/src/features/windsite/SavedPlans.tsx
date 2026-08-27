/**
 * 저장한 배치안 — 목록·불러오기·비교
 * ---------------------------------------------------------------
 * 배치선 검토는 한 번 찍고 끝나지 않는다. 이격거리에 걸린 호기를 옮기고
 * 다시 돌리기를 반복하며, 몇 달 뒤 "그때 그 배치가 왜 안 됐더라"를 다시
 * 들춘다. 좌표를 남겨두지 않으면 그 반복이 매번 처음부터다.
 *
 * 사업 아래 배치안이 여러 개 달리고, 배치안을 고르면 지도에 그대로
 * 복원된다. 저장된 판정 요약은 **산출 시점과 함께** 보여준다 — 규제와
 * 조례는 개정되므로, 몇 달 전 요약을 지금 값처럼 읽으면 안 된다.
 */
import { useEffect, useState } from 'react'
import { windsiteApi } from './api'
import { STATUS_LABEL, type SitePlan, type SiteProject } from './types'

type Props = {
  /** 배치안을 지도에 복원한다 */
  onLoad: (plan: SitePlan) => void
  /** 저장 직후 목록을 새로 읽게 하는 신호 */
  refreshKey: number
}

/** '2026-08-13T14:23:08' → '2026-08-13' */
/**
 * 검토 시각. **분까지 보인다.**
 *
 * 날짜만 적으면 같은 날 여러 번 검토한 것이 구분되지 않는다. 규제·조례는
 * 개정되고 조회 자료도 바뀌므로, 같은 부지를 오전과 오후에 검토하면 결과가
 * 다를 수 있다 — 어느 것이 최신인지 가리려면 시각이 있어야 한다.
 */
const stamp = (s: string) => (s || '').replace('T', ' ').slice(0, 16);

export default function SavedPlans({ onLoad, refreshKey }: Props) {
  const [projects, setProjects] = useState<SiteProject[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  /** 비교로 고른 배치안 id */
  const [picked, setPicked] = useState<string[]>([])
  /** 메모를 편집 중인 배치안 */
  const [editing, setEditing] = useState<SitePlan | null>(null)
  const [draft, setDraft] = useState({ name: '', note: '', reviewer: '' })

  const reload = async () => {
    setLoading(true)
    try {
      // 에너지원으로 가른다. 종전에는 전체를 받아 태양광 후보가 풍력
      // 배치안 목록에 섞여 나왔다.
      setProjects((await windsiteApi.projects('WIND')).results)
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : '저장된 배치안을 불러오지 못했습니다.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void reload() }, [refreshKey])

  const all = projects.flatMap(p => p.plans)
  const chosen = all.filter(x => picked.includes(x.id))

  const toggle = (id: string) =>
    setPicked(p => (p.includes(id) ? p.filter(x => x !== id) : [...p, id]))

  const save = async () => {
    if (!editing) return
    try {
      await windsiteApi.patchPlan(editing.id,
        { name: draft.name.trim(), note: draft.note,
          reviewer: draft.reviewer.trim() })
      setEditing(null)
      await reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : '수정에 실패했습니다.')
    }
  }

  const removePlan = async (x: SitePlan) => {
    if (!window.confirm(`"${x.name}"을(를) 삭제합니다. 되돌릴 수 없습니다.`)) return
    try {
      await windsiteApi.deletePlan(x.id)
      setPicked(p => p.filter(i => i !== x.id))
      await reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : '삭제에 실패했습니다.')
    }
  }

  const removeProject = async (p: SiteProject) => {
    if (!window.confirm(
      `"${p.name}" 사업과 배치안 ${p.plan_count}개를 모두 삭제합니다. 되돌릴 수 없습니다.`)) return
    try {
      await windsiteApi.deleteProject(p.id)
      await reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : '삭제에 실패했습니다.')
    }
  }

  if (loading) return <p className="ops-empty">불러오는 중…</p>

  return (
    <div className="ws-saved">
      {error && <p className="ws-error">{error}</p>}

      {!projects.length ? (
        <p className="ops-empty">
          저장된 배치안이 없습니다. 지도에 지점을 찍고 검토를 실행한 뒤
          <b> 배치안 저장</b>을 누르십시오.
        </p>
      ) : (
        projects.map(p => (
          <div key={p.id} className="ws-proj">
            <div className="ws-proj-hd">
              <div>
                <b>{p.name}</b>
                <em>{p.plan_count}안</em>
                {p.description && <span className="desc">{p.description}</span>}
              </div>
              <button className="ws-link danger" onClick={() => void removeProject(p)}>
                사업 삭제
              </button>
            </div>

            <div className="ws-planlist">
              {p.plans.filter(x => x.mode !== 'parcel' && x.mode !== 'area').map(x => (
                <div key={x.id} className={`ws-plan${picked.includes(x.id) ? ' on' : ''}`}>
                  <label className="pick">
                    <input type="checkbox" checked={picked.includes(x.id)}
                      onChange={() => toggle(x.id)} />
                  </label>
                  <div className="body">
                    <div className="ln1">
                      <b>{x.name}</b>
                      <span className="chip">{x.turbine_count}기</span>
                      <span className="chip">반경 {x.turbine_radius_m}m</span>
                      {x.capacity_mw ? <span className="chip">{x.capacity_mw} MW</span> : null}
                      {x.summary?.grade && (
                        <span className={`ws-status s-${x.summary.grade}`}>
                          {x.summary.score}점 · {STATUS_LABEL[x.summary.grade]}
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
              ))}
            </div>
          </div>
        ))
      )}

      {chosen.length >= 2 && (
        <div className="ws-group">
          <p className="ops-section-t">배치안 비교 <em className="ws-catcount"> {chosen.length}안</em></p>
          <p className="ws-area-note">
            저장 시점의 요약입니다. 규제·조례는 개정되므로 판정 기준일이 서로 다르면
            그대로 견주기 어렵습니다 — 다시 검토해 값을 갱신하십시오.
          </p>
          <div className="ws-table-wrap">
            <table className="ws-table">
              <thead>
                <tr>
                  <th>사업 · 배치안</th><th>호기</th><th>검토반경</th><th>용량</th>
                  <th>판정</th><th>제약없음</th><th>검토면적</th><th>판정 기준일</th>
                </tr>
              </thead>
              <tbody>
                {chosen.map(x => (
                  <tr key={x.id}>
                    <td>{x.project_name} · <b>{x.name}</b></td>
                    <td>{x.turbine_count}기</td>
                    <td>{x.turbine_radius_m}m</td>
                    <td>{x.capacity_mw ? `${x.capacity_mw} MW` : '-'}</td>
                    <td>
                      {x.summary?.grade
                        ? `${x.summary.score}점 · ${STATUS_LABEL[x.summary.grade]}`
                        : '-'}
                    </td>
                    <td>{x.summary?.free_ha != null ? `${x.summary.free_ha.toLocaleString()} ha` : '-'}</td>
                    <td>{x.summary?.total_ha != null ? `${x.summary.total_ha.toLocaleString()} ha` : '-'}</td>
                    <td>{x.evaluated_at ? stamp(x.evaluated_at) : '판정 없음'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {editing && (
        <div className="ws-modal-bg" onClick={() => setEditing(null)}>
          <div className="ws-modal" onClick={e => e.stopPropagation()}>
            <b>배치안 이름·메모</b>
            <label className="ws-fld">
              <span>배치안명</span>
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
              <textarea rows={4} value={draft.note}
                placeholder="예) 3호기를 능선 남쪽으로 300m 이설 — 주거 이격 회피"
                onChange={e => setDraft(d => ({ ...d, note: e.target.value }))} />
            </label>
            <div className="ws-modal-acts">
              <button className="ws-btn2" onClick={() => setEditing(null)}>취소</button>
              <button className="btn-primary" onClick={() => void save()}
                disabled={!draft.name.trim()}>저장</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
