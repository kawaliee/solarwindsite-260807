import { useEffect, useMemo, useState } from 'react'
import SitePicker from './SitePicker'
import { windsiteApi } from './api'
import {
  CONFIDENCE_LABEL,
  DIFFICULTY_LABEL,
  STATUS_LABEL,
  type AnalysisItem,
  type CompareCandidate,
  type CompareResult,
  type EvaluationResult,
  type LawRef,
  type ProviderConfigRow,
} from './types'

type Tab = 'result' | 'compare' | 'permits' | 'laws' | 'config';

/** 검토 반경 기본값·허용범위 — 백엔드 engine.py의 같은 이름 상수와 맞춘다 */
const DEFAULT_RADIUS_M = 50;
const MIN_RADIUS_M = 50;
const MAX_RADIUS_M = 20000;

export default function WindSiteView() {
  const [lat, setLat] = useState<number | null>(null);
  const [lng, setLng] = useState<number | null>(null);
  const [radiusM, setRadiusM] = useState(DEFAULT_RADIUS_M);
  const [address, setAddress] = useState('');
  const [sido, setSido] = useState('');
  const [sigungu, setSigungu] = useState('');
  const [capacity, setCapacity] = useState('');

  const [result, setResult] = useState<EvaluationResult | null>(null);
  const [laws, setLaws] = useState<LawRef[]>([]);
  const [config, setConfig] = useState<ProviderConfigRow[]>([]);
  const [tab, setTab] = useState<Tab>('result');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // 보고서 내려받기
  const [reporting, setReporting] = useState(false);

  // 후보지 비교 — 현재 지점을 후보로 담아 최대 5곳까지 비교한다
  const [candidates, setCandidates] = useState<CompareCandidate[]>([]);
  const [compareResult, setCompareResult] = useState<CompareResult | null>(null);
  const [comparing, setComparing] = useState(false);

  // 지도 클릭 → 역지오코딩 진행 상태
  const [locating, setLocating] = useState(false);

  /**
   * 지도에서 지점을 찍으면 주소·행정구역을 자동으로 채운다.
   *
   * 시·군·구는 조례 조회의 기준값이라 수기 입력에 의존하면 오타 하나로
   * 이격거리 판정이 통째로 UNKNOWN이 된다. 좌표에서 바로 끌어오는 편이 안전하다.
   * 조회에 실패해도 좌표 지정 자체는 유효하므로 검토를 막지 않는다.
   */
  const pickSite = async (a: number, o: number) => {
    setLat(a);
    setLng(o);
    setLocating(true);
    try {
      const g = await windsiteApi.geocode({ lat: a, lng: o });
      setAddress(g.address || g.road_address || '');
      setSido(g.sido || '');
      setSigungu(g.sigungu || '');
      setError('');
    } catch {
      // 바다·비주소 지역이거나 V-World 조회 실패. 좌표는 그대로 살린다.
      setError('클릭 지점의 주소를 찾지 못했습니다. 주소·행정구역을 직접 입력하십시오.');
    } finally {
      setLocating(false);
    }
  };

  useEffect(() => {
    windsiteApi.laws().then(d => setLaws(d.results)).catch(() => setLaws([]));
    windsiteApi.config().then(d => setConfig(d.results)).catch(() => setConfig([]));
  }, []);

  const run = async () => {
    if (lat == null || lng == null) {
      setError('지도를 클릭하거나 위·경도를 입력해 사업지를 지정하십시오.');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const r = await windsiteApi.evaluate({
        lat, lng, radius_m: radiusM, address, sido, sigungu,
        capacity_mw: capacity ? Number(capacity) : null,
      });
      setResult(r);
      setTab('result');
    } catch (e) {
      setError(e instanceof Error ? e.message : '검토 실행에 실패했습니다.');
    } finally {
      setLoading(false);
    }
  };

  const downloadReport = async () => {
    if (lat == null || lng == null) {
      setError('사업지를 먼저 지정하십시오.');
      return;
    }
    setReporting(true);
    setError('');
    try {
      await windsiteApi.downloadReport({
        lat, lng, radius_m: radiusM, address, sido, sigungu,
        capacity_mw: capacity ? Number(capacity) : null,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : '보고서 생성에 실패했습니다.');
    } finally {
      setReporting(false);
    }
  };

  const addCandidate = () => {
    if (lat == null || lng == null) {
      setError('지도를 클릭해 후보지를 지정한 뒤 담으십시오.');
      return;
    }
    if (candidates.length >= 5) {
      setError('한 번에 비교 가능한 후보는 최대 5곳입니다.');
      return;
    }
    setError('');
    setCandidates(prev => [...prev, {
      label: address || `후보 ${prev.length + 1}`,
      lat, lng, radius_m: radiusM, address, sido, sigungu,
      capacity_mw: capacity ? Number(capacity) : null,
    }]);
    setTab('compare');
  };

  const runCompare = async () => {
    if (candidates.length < 2) {
      setError('비교하려면 후보지를 2곳 이상 담으십시오.');
      return;
    }
    setComparing(true);
    setError('');
    try {
      setCompareResult(await windsiteApi.compare(candidates));
    } catch (e) {
      setError(e instanceof Error ? e.message : '후보지 비교에 실패했습니다.');
    } finally {
      setComparing(false);
    }
  };

  const grouped = useMemo(() => {
    const m = new Map<string, AnalysisItem[]>();
    (result?.analysis_items ?? []).forEach(i => {
      const arr = m.get(i.category) ?? [];
      arr.push(i);
      m.set(i.category, arr);
    });
    return Array.from(m.entries());
  }, [result]);

  const notConfigured = config.filter(c => c.configured === false);

  return (
    <div className="ws-view">
      {/* ── 입력부 ── */}
      <div className="ws-top">
        <div className="ops-card ws-inputcard">
          <div className="ops-card-hd"><span className="tag">SITE</span> 사업지 지정</div>
          <div className="ops-card-bd">
            <SitePicker
              lat={lat} lng={lng} radiusM={radiusM}
              onPick={pickSite}
            />
            <div className="ws-coordrow">
              <label>위도<input type="number" step="0.00001" value={lat ?? ''}
                placeholder="36.12345"
                onChange={e => setLat(e.target.value === '' ? null : Number(e.target.value))} /></label>
              <label>경도<input type="number" step="0.00001" value={lng ?? ''}
                placeholder="128.56789"
                onChange={e => setLng(e.target.value === '' ? null : Number(e.target.value))} /></label>
            </div>
          </div>
        </div>

        <div className="ops-card ws-formcard">
          <div className="ops-card-hd"><span className="tag">INPUT</span> 검토 조건</div>
          <div className="ops-card-bd">
            <label className="ws-fld">
              <span>
                사업지 주소 <em>{locating ? '(주소 조회 중…)' : '(지도 클릭 시 자동 입력)'}</em>
              </span>
              <input type="text" value={address} placeholder="경상북도 ○○군 ○○면 산 ○○번지"
                onChange={e => setAddress(e.target.value)} />
            </label>
            <div className="ws-two">
              <label className="ws-fld">
                <span>시·도</span>
                <input type="text" value={sido} placeholder="경상북도"
                  onChange={e => setSido(e.target.value)} />
              </label>
              <label className="ws-fld">
                <span>시·군·구 <em>(조례 조회 기준)</em></span>
                <input type="text" value={sigungu} placeholder="청도군"
                  onChange={e => setSigungu(e.target.value)} />
              </label>
            </div>
            <div className="ws-two">
              <label className="ws-fld">
                <span>검토 반경 (m)</span>
                <input type="number" min={MIN_RADIUS_M} max={MAX_RADIUS_M} step={50}
                  value={radiusM}
                  onChange={e => setRadiusM(Number(e.target.value) || DEFAULT_RADIUS_M)} />
              </label>
              <label className="ws-fld">
                <span>설비용량 (MW) <em>(선택)</em></span>
                <input type="number" min={0} step={0.1} value={capacity} placeholder="60"
                  onChange={e => setCapacity(e.target.value)} />
              </label>
            </div>

            <button className="btn-primary ws-run" onClick={run} disabled={loading}>
              {loading ? '검토 중…' : '입지타당성 검토 실행'}
            </button>

            <div className="ws-actions">
              <button className="ws-btn2" onClick={downloadReport} disabled={reporting || loading}>
                {reporting ? '보고서 생성 중…' : '보고서 내려받기 (docx)'}
              </button>
              <button className="ws-btn2" onClick={addCandidate} disabled={loading}>
                후보지로 담기 {candidates.length > 0 && `(${candidates.length})`}
              </button>
            </div>
            <p className="ws-hint">
              보고서에는 지적·규제·주변현황·이격거리 지도 4종이 포함되며 생성에 2~3분 걸립니다.
            </p>

            {error && <p className="ws-error">{error}</p>}

            {notConfigured.length > 0 && (
              <p className="ws-warn">
                API 인증키 미설정 {notConfigured.length}건 — 해당 항목은 <b>확인 필요</b>로 표시됩니다.
                <button className="ws-link" onClick={() => setTab('config')}>연동 현황 보기</button>
              </p>
            )}
          </div>
        </div>
      </div>

      {/* ── 탭 ── */}
      <div className="ws-tabs">
        {([
          ['result', '입지 검토 결과'],
          ['compare', `후보지 비교${candidates.length ? ` (${candidates.length})` : ''}`],
          ['permits', '인허가 로드맵'],
          ['laws', `관련 법령 (${laws.length})`],
          ['config', '데이터 연동 현황'],
        ] as [Tab, string][]).map(([k, label]) => (
          <button key={k} className={`ws-tab${tab === k ? ' on' : ''}`} onClick={() => setTab(k)}>
            {label}
          </button>
        ))}
      </div>

      {/* ── 결과 ── */}
      {tab === 'result' && (
        !result ? (
          <p className="ops-empty">사업지를 지정하고 검토를 실행하십시오.</p>
        ) : (
          <>
            <div className={`ws-overall g-${result.overall_feasibility.grade}`}>
              <div className="ws-score">
                <b>{result.overall_feasibility.score}</b><span>/100</span>
              </div>
              <div className="ws-verdict">
                <div className="ws-grade">{STATUS_LABEL[result.overall_feasibility.grade]}</div>
                <p>{result.overall_feasibility.summary}</p>
                <div className="ws-sitemeta">
                  {result.site_info.address} · 반경 {result.site_info.radius_m.toLocaleString()}m ·
                  검토면적 {(result.site_info.total_area_m2 / 10000).toFixed(1)}ha
                </div>
              </div>
            </div>

            {result.data_gaps.length > 0 && (
              <div className="ws-gaps">
                <b>확인이 필요한 항목 {result.data_gaps.length}건</b>
                <ul>{result.data_gaps.map((g, i) => <li key={i}>{g}</li>)}</ul>
              </div>
            )}

            {grouped.map(([cat, items]) => (
              <div key={cat} className="ws-group">
                <p className="ops-section-t">{cat}</p>
                <div className="ws-items">
                  {items.map(i => <ItemCard key={i.item_name} item={i} />)}
                </div>
              </div>
            ))}
          </>
        )
      )}

      {/* ── 후보지 비교 ── */}
      {tab === 'compare' && (
        <>
          <div className="ops-card ws-cmp-head">
            <div className="ws-cmp-list">
              {candidates.length === 0 ? (
                <p className="ops-empty">
                  지도에서 지점을 지정하고 <b>후보지로 담기</b>를 누르십시오. 2~5곳을 같은 기준으로 비교합니다.
                </p>
              ) : (
                candidates.map((c, i) => (
                  <span key={i} className="ws-chip">
                    {c.label}
                    <em>{c.lat?.toFixed(4)}, {c.lng?.toFixed(4)} · {c.radius_m}m</em>
                    <button onClick={() => setCandidates(p => p.filter((_, j) => j !== i))}>×</button>
                  </span>
                ))
              )}
            </div>
            {candidates.length > 0 && (
              <div className="ws-actions">
                <button className="btn-primary" onClick={runCompare}
                  disabled={comparing || candidates.length < 2}>
                  {comparing ? `비교 중… (후보당 2~3분)` : `${candidates.length}곳 비교 실행`}
                </button>
                <button className="ws-btn2" onClick={() => { setCandidates([]); setCompareResult(null); }}>
                  전체 비우기
                </button>
              </div>
            )}
          </div>

          {compareResult && (
            <>
              <p className="ws-note">{compareResult.note}</p>
              <div className="ops-card ws-tablecard">
                <table className="ws-table">
                  <thead>
                    <tr>
                      <th>순위</th><th>후보지</th><th>등급</th><th>점수</th>
                      <th>가용면적</th><th>최근접 변전소</th><th>조례 저촉</th><th>미확인</th>
                    </tr>
                  </thead>
                  <tbody>
                    {compareResult.comparison.map(r => (
                      <tr key={r.label}>
                        <td className="ws-rank">{r.rank}</td>
                        <td>{r.label}</td>
                        <td><StatusBadge s={r.grade} /></td>
                        <td>{r.score}</td>
                        <td>{r.usable_area_m2 != null
                          ? `${Math.round(r.usable_area_m2).toLocaleString()}㎡` : '—'}</td>
                        <td>{r.nearest_substation
                          ? `${r.nearest_substation.name} ${(r.nearest_substation.distance_m / 1000).toFixed(1)}km`
                          : '—'}</td>
                        <td>{r.ordinance_breaches.length
                          ? r.ordinance_breaches.join(' / ') : '없음'}</td>
                        <td>{r.unknown_count}건</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {compareResult.comparison.map(r => (
                (r.blockers.length > 0 || r.critical_conditions.length > 0) && (
                  <p key={r.label} className="ws-warn">
                    <b>{r.label}</b> —{' '}
                    {r.blockers.length > 0 && `불가 항목: ${r.blockers.join(', ')}. `}
                    {r.critical_conditions.length > 0 && `치명 조건부: ${r.critical_conditions.join(', ')}`}
                  </p>
                )
              ))}
            </>
          )}
        </>
      )}

      {/* ── 인허가 로드맵 ── */}
      {tab === 'permits' && (
        !result ? (
          <p className="ops-empty">검토를 실행하면 사업 조건에 맞는 인허가 로드맵이 표시됩니다.</p>
        ) : (
          <div className="ws-roadmap">
            {result.permit_roadmap.map(s => (
              <div key={s.order} className={`ws-step${s.applicable ? '' : ' off'}`}>
                <div className="ws-step-mark"><span className="ph">{s.phase}</span></div>
                <div className="ws-step-body">
                  <div className="ws-step-hd">
                    <b>{s.name}</b>
                    {!s.applicable && <span className="ws-badge off">해당 없음</span>}
                    <ConfidenceBadge c={s.confidence} />
                  </div>
                  <div className="ws-step-meta">
                    <span>소관 {s.authority}</span>
                    {s.law && <span>{s.law} {s.article}</span>}
                    {s.statutory_days != null && <span>법정 {s.statutory_days}일</span>}
                  </div>
                  {s.depends_on.length > 0 && (
                    <div className="ws-step-dep">선행: {s.depends_on.join(' · ')}</div>
                  )}
                  <p className="ws-step-reason">{s.applicability_reason}</p>
                  {s.note && <p className="ws-step-note">{s.note}</p>}
                </div>
              </div>
            ))}
          </div>
        )
      )}

      {/* ── 법령 ── */}
      {tab === 'laws' && (
        <div className="ws-laws">
          {laws.length === 0 && <p className="ops-empty">법령 데이터가 없습니다. 시드 명령을 실행하십시오.</p>}
          {laws.map(l => (
            <div key={l.name} className="ws-law">
              <div className="ws-law-hd">
                <b>{l.name}</b>
                <span className="ws-cat">{l.category}</span>
                <ConfidenceBadge c={l.confidence} />
              </div>
              {l.purpose && <p className="ws-law-purpose">{l.purpose}</p>}
              {l.key_articles && <p className="ws-law-art">{l.key_articles}</p>}
              {l.note && <p className="ws-step-note">{l.note}</p>}
            </div>
          ))}
        </div>
      )}

      {/* ── 연동 현황 ── */}
      {tab === 'config' && (
        <div className="ws-config">
          <table className="tbl">
            <thead>
              <tr><th>분석 항목</th><th>데이터 출처</th><th>상태</th><th>필요 설정</th></tr>
            </thead>
            <tbody>
              {config.map(c => (
                <tr key={c.item_name}>
                  <td>{c.item_name}</td>
                  <td>{c.data_source}</td>
                  <td>
                    {c.configured === null
                      ? <span className="ws-badge na">키 불필요</span>
                      : c.configured
                        ? <span className="ws-badge ok">연동됨</span>
                        : <span className="ws-badge no">키 미설정</span>}
                    {c.missing_optional?.length > 0 && (
                      <span className="ws-badge na" title="없어도 동작하지만 판정이 얕아집니다">
                        선택 키 미설정
                      </span>
                    )}
                  </td>
                  <td className="ws-mono">
                    {c.missing.length > 0
                      ? c.missing.join(', ')
                      : (c.active_keys?.join(', ') || '—')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="ws-footnote">
            <b>키 불필요</b>는 인증키 없이 동작하는 항목입니다(내부 적재 공간데이터·OpenStreetMap·조례 DB).
            좌표 기반 공개 API가 없어 자동 판정되지 않는 것은 <b>군사기지법상 보호구역</b>(통제·제한보호구역,
            비행안전구역 제1~6구역)과 <b>계통 접속 가능 용량 확정</b>, <b>허브고도 풍황</b>뿐이며
            관할부대·한전 협의와 현장 계측으로 처리하십시오.
          </p>
        </div>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------
function ItemCard({ item }: { item: AnalysisItem }) {
  return (
    <div className={`ws-item s-${item.status}`}>
      <div className="ws-item-hd">
        <span className={`ws-status s-${item.status}`}>{STATUS_LABEL[item.status]}</span>
        <b>{item.item_name}</b>
        <span className={`ws-diff d-${item.difficulty}`}>난이도 {DIFFICULTY_LABEL[item.difficulty]}</span>
        <ConfidenceBadge c={item.confidence} />
      </div>
      <p className="ws-item-reason">{item.reason}</p>
      {(item.law || item.article) && (
        <div className="ws-item-law">근거 · {item.law} {item.article}</div>
      )}
      {item.action_required && (
        <div className="ws-item-action">다음 조치 · {item.action_required}</div>
      )}
      {item.source_url && (
        <a className="ws-item-src" href={item.source_url} target="_blank" rel="noreferrer">
          출처 확인
        </a>
      )}
    </div>
  );
}

function ConfidenceBadge({ c }: { c: 'HIGH' | 'MEDIUM' | 'LOW' }) {
  return <span className={`ws-conf c-${c}`} title="판정 기준의 검증 수준">{CONFIDENCE_LABEL[c]}</span>;
}

/** 종합등급 배지 — 비교표에서 후보지별 등급을 한눈에 보이게 한다 */
function StatusBadge({ s }: { s: keyof typeof STATUS_LABEL }) {
  return <span className={`ws-status s-${s}`}>{STATUS_LABEL[s]}</span>;
}
