import { useEffect, useMemo, useState } from 'react'
import SitePicker, { type PickMode } from './SitePicker'
import { windsiteApi } from './api'
import {
  CONFIDENCE_HINT,
  CONFIDENCE_LABEL,
  DIFFICULTY_LABEL,
  STATUS_LABEL,
  UNKNOWN_REASON_HINT,
  UNKNOWN_REASON_LABEL,
  type UnknownReason,
  type AnalysisItem,
  type CompareCandidate,
  type CompareResult,
  type AreaResult,
  type EvaluationResult,
  type LatLng,
  type LawRef,
  ORDINANCE_STATE_LABEL,
  type ProviderConfigRow,
} from './types'

type Tab = 'result' | 'compare' | 'permits' | 'laws' | 'config';

/** 검토 반경 기본값·허용범위 — 백엔드 engine.py의 같은 이름 상수와 맞춘다 */
const DEFAULT_RADIUS_M = 100;
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

  // 사업구역(폴리곤) 검토 — 점 검토와 별도 상태로 둔다. 둘을 한 변수에
  // 합치면 모드를 오갈 때 서로의 입력을 지우게 된다.
  const [pickMode, setPickMode] = useState<PickMode>('point');
  const [ring, setRing] = useState<LatLng[]>([]);
  const [turbineR, setTurbineR] = useState(500);
  /**
   * 발전기별 주소. ring과 인덱스를 맞춰 둔다.
   * 클릭할 때가 아니라 ring 변화를 보고 채운다 — 되돌리기·지우기로 목록이
   * 줄어드는 경우까지 한 곳에서 맞추기 위해서다.
   */
  const [turbineAddrs, setTurbineAddrs] = useState<(string | null)[]>([]);
  const [corridorR, setCorridorR] = useState(100);
  /** 발전사업허가일 — 조례 시행일보다 앞서면 부칙 경과조치 검토 대상이 된다 */
  const [permitDate, setPermitDate] = useState('');
  const [areaResult, setAreaResult] = useState<AreaResult | null>(null);
  const [areaLoading, setAreaLoading] = useState(false);
  const [areaReporting, setAreaReporting] = useState(false);

  /** 검토와 보고서가 같은 입력을 쓰도록 한 곳에서 만든다 */
  function areaBody() {
    return pickMode === 'layout'
      ? { turbines: ring, turbine_radius_m: turbineR,
          corridor_radius_m: corridorR, permit_date: permitDate }
      : { ring, permit_date: permitDate };
  }

  async function downloadAreaReport() {
    setAreaReporting(true); setError('');
    try {
      await windsiteApi.downloadAreaReport(areaBody());
    } catch (e) {
      setError(e instanceof Error ? e.message : '보고서 생성에 실패했습니다.');
    } finally {
      setAreaReporting(false);
    }
  }

  async function runArea() {
    const layout = pickMode === 'layout';
    if (layout ? ring.length < 1 : ring.length < 3) {
      setError(layout ? '발전기 위치를 1기 이상 찍어주세요.'
                      : '사업구역 꼭짓점을 3개 이상 찍어주세요.');
      return;
    }
    setAreaLoading(true); setError('');
    try {
      setAreaResult(layout
        ? await windsiteApi.evaluateLayout(ring, turbineR, corridorR, permitDate)
        : await windsiteApi.evaluateArea(ring, permitDate));
    } catch (e) {
      setError(e instanceof Error ? e.message : '구역 검토에 실패했습니다.');
    } finally {
      setAreaLoading(false);
    }
  }

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

  // ── 발전기 위치 → 주소 역지오코딩 ──────────────────────────────────
  useEffect(() => {
    if (pickMode !== 'layout') return;
    // 길이를 먼저 맞춘다. 되돌리기로 줄면 뒤쪽 주소를 버리고,
    // 늘면 빈 칸(null)을 만들어 아래에서 채운다.
    setTurbineAddrs(prev => {
      if (prev.length === ring.length) return prev;
      const next = prev.slice(0, ring.length);
      while (next.length < ring.length) next.push(null);
      return next;
    });

    let alive = true;
    (async () => {
      for (let i = 0; i < ring.length; i++) {
        // 이미 채워진 칸은 건너뛴다 — 점을 하나 추가할 때마다 전체를
        // 다시 조회하면 호출이 제곱으로 는다.
        if (turbineAddrs[i]) continue;
        const [a, o] = ring[i];
        try {
          const g = await windsiteApi.geocode({ lat: a, lng: o });
          if (!alive) return;
          const addr = g.address || g.road_address || '';
          setTurbineAddrs(prev => {
            const next = [...prev];
            next[i] = addr || '(주소 없음)';
            return next;
          });
          // 시·도/시·군·구는 1호기 기준으로 채운다. 조례 조회는 구역
          // 전체를 시군구로 다시 나누므로 여기 값은 표기용이다.
          if (i === 0) { setSido(g.sido || ''); setSigungu(g.sigungu || ''); }
        } catch {
          if (!alive) return;
          setTurbineAddrs(prev => {
            const next = [...prev];
            next[i] = '(주소 조회 실패)';
            return next;
          });
        }
      }
    })();
    return () => { alive = false; };
    // turbineAddrs를 의존성에 넣으면 갱신할 때마다 다시 돌아 무한루프가 된다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pickMode, ring]);

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
            <div className="ws-modebar">
              {(['point', 'layout', 'area'] as const).map(m => (
                <button key={m} type="button"
                  className={pickMode === m ? 'on' : ''}
                  onClick={() => {
                    setPickMode(m); setRing([]); setAreaResult(null); setTurbineAddrs([]);
                  }}>
                  {m === 'point' ? '지점 검토' : m === 'layout' ? '배치선 검토' : '구역 검토'}
                </button>
              ))}
              {pickMode !== 'point' && (
                <span className="ws-modeacts">
                  <button type="button" disabled={!ring.length}
                    onClick={() => setRing(ring.slice(0, -1))}>되돌리기</button>
                  <button type="button" disabled={!ring.length}
                    onClick={() => {
                      setRing([]); setAreaResult(null); setTurbineAddrs([]);
                    }}>지우기</button>
                  <button type="button" className="run"
                    disabled={ring.length < (pickMode === 'layout' ? 1 : 3) || areaLoading}
                    onClick={runArea}>
                    {areaLoading ? '검토 중…'
                      : pickMode === 'layout' ? '배치선 검토 실행' : '구역 검토 실행'}
                  </button>
                  {areaResult && (
                    <button type="button" disabled={areaReporting}
                      onClick={downloadAreaReport}>
                      {areaReporting ? '생성 중…' : '보고서 (docx)'}
                    </button>
                  )}
                </span>
              )}
            </div>
            {pickMode === 'layout' && (
              <div className="ws-radrow">
                <label>발전기 검토반경 (m)
                  <input type="number" min={50} max={20000} step={50} value={turbineR}
                    onChange={e => setTurbineR(Number(e.target.value) || 500)} /></label>
                <label>연결선 검토반경 (m)
                  <input type="number" min={50} max={20000} step={10} value={corridorR}
                    onChange={e => setCorridorR(Number(e.target.value) || 100)} /></label>
                <em>이격거리 조례는 발전기 위치에만 적용됩니다 (연결선은 소음원이 아님)</em>
              </div>
            )}
            {pickMode !== 'point' && (
              <div className="ws-radrow">
                <label>발전사업허가일 <span className="opt">(선택)</span>
                  <input type="date" value={permitDate}
                    onChange={e => setPermitDate(e.target.value)} /></label>
                <em>조례 시행일보다 앞서면 부칙 경과조치 검토 대상으로 표시합니다</em>
              </div>
            )}
            <SitePicker
              lat={lat} lng={lng} radiusM={radiusM}
              onPick={pickSite}
              mode={pickMode}
              ring={ring}
              onRingChange={setRing}
              turbineRadiusM={turbineR}
              corridorRadiusM={corridorR}
              overlays={areaResult?.overlays ?? null}
            />
            {pickMode !== 'point' && areaResult && (
              <AreaSummary r={areaResult} />
            )}
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
                사업지 주소{' '}
                <em>
                  {pickMode === 'layout'
                    ? `(발전기 ${ring.length}기 · 클릭 시 자동 입력)`
                    : locating ? '(주소 조회 중…)' : '(지도 클릭 시 자동 입력)'}
                </em>
              </span>
              {pickMode === 'layout' ? (
                ring.length === 0 ? (
                  <p className="ws-addr-empty">지도에서 발전기 위치를 찍으면 호기별 주소가 표시됩니다.</p>
                ) : (
                  <ol className="ws-addrlist">
                    {ring.map(([a, o], i) => (
                      <li key={i}>
                        <span className="no">{i + 1}</span>
                        <span className="addr">
                          {turbineAddrs[i] ?? '조회 중…'}
                        </span>
                        <span className="ll">{a.toFixed(5)}, {o.toFixed(5)}</span>
                      </li>
                    ))}
                  </ol>
                )
              ) : (
                <input type="text" value={address} placeholder="경상북도 ○○군 ○○면 산 ○○번지"
                  onChange={e => setAddress(e.target.value)} />
              )}
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
        {/* 판정 결과(status)와 근거의 단단함(confidence)은 다른 축이다.
            사유 배지를 함께 붙여 '조회 실패'와 '원래 자동 판정이 안 되는 항목'을 구분한다. */}
        <UnknownReasonBadge item={item} />
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
  return (
    <span className={`ws-conf c-${c}`} title={`근거 수준 — ${CONFIDENCE_HINT[c]}`}>
      근거 {CONFIDENCE_LABEL[c]}
    </span>
  );
}

/** UNKNOWN 항목에만 붙는 사유 배지 — 재시도로 풀리는지 아닌지가 한눈에 보이게 한다 */
function UnknownReasonBadge({ item }: { item: AnalysisItem }) {
  if (item.status !== 'UNKNOWN') return null;
  const why = (item.unknown_reason || '') as UnknownReason;
  const label = UNKNOWN_REASON_LABEL[why];
  if (!label) return null;
  return (
    <span className={`ws-why w-${why}`} title={UNKNOWN_REASON_HINT[why]}>{label}</span>
  );
}

/** 종합등급 배지 — 비교표에서 후보지별 등급을 한눈에 보이게 한다 */
function StatusBadge({ s }: { s: keyof typeof STATUS_LABEL }) {
  return <span className={`ws-status s-${s}`}>{STATUS_LABEL[s]}</span>;
}

/**
 * 사업구역 검토 결과 요약.
 *
 * 가용면적을 두 가지로 병기한다. 이 시스템은 생태자연도 1등급도 백두대간
 * 핵심구역도 '조건부'로 판정하므로(법률상 예외 행위가 있다), 배제/가용을
 * 하나로 자르면 코드가 법령에 없는 금지를 만들어내게 된다. 어느 쪽을
 * 쓸지는 사업 판단이다.
 */
function AreaSummary({ r }: { r: AreaResult }) {
  const ha = (b: { ha: number; ratio: number }) =>
    `${b.ha.toLocaleString()} ha (${(b.ratio * 100).toFixed(1)}%)`;
  const provisional = r.by_reason.some(x => x.provisional);

  return (
    <div className="ws-area">
      <div className="ws-area-hd">
        {r.layout
          ? `발전기 ${r.layout.turbines.length}기 배치선 · 검토 ${r.total.ha.toLocaleString()} ha`
          : `사업구역 ${r.total.ha.toLocaleString()} ha`}
        <em>{r.jurisdictions.map(j => j.sigungu).join(' · ')}</em>
      </div>
      {r.layout && (
        <p className="ws-area-note">
          발전기 반경 {r.layout.turbine_radius_m.toLocaleString()}m
          ({(r.layout.turbine_area_m2 / 1e4).toFixed(1)} ha) ·
          연결선 반경 {r.layout.corridor_radius_m.toLocaleString()}m
          ({(r.layout.corridor_area_m2 / 1e4).toFixed(1)} ha)
          {r.layout.corridor_area_m2 < 1 &&
            ' — 발전기 반경이 연결선을 모두 덮었습니다'}
        </p>
      )}

      <div className="ws-area-bars">
        {([['blocked', '배제'], ['conditional', '조건부'], ['free', '제약 없음'],
           ['pending', '판정 보류']] as const).map(([k, label]) => (
          <div key={k} className={`ws-area-bar b-${k}`}>
            <span className="l">{label}</span>
            <span className="v">{ha(r[k])}</span>
          </div>
        ))}
      </div>

      <div className="ws-area-avail">
        <div><span>엄격 가용</span><b>{ha(r.available_strict)}</b>
          <em>어떤 규제 레이어에도 걸리지 않는 면적</em></div>
        <div><span>협의 포함</span><b>{ha(r.available_with_consultation)}</b>
          <em>위 + 조건부 (협의·저감으로 진행 가능한 범위)</em></div>
      </div>

      {provisional && (
        <p className="ws-area-warn">
          <b>잠정치</b> — 이격 버퍼는 용도가 확인되지 않은 건물 전체에 조례 최대
          반경을 씌운 값이라 실제보다 넓습니다. 건물 용도 분류가 반영되면 줄어듭니다.
        </p>
      )}
      {r.grandfathering && r.grandfathering.ordinances.length > 0 && (
        <div className={`ws-area-gf${r.grandfathering.review_required ? ' hot' : ''}`}>
          <h5>조례 경과규정 검토</h5>
          <ul>
            {r.grandfathering.ordinances.map((o, i) => (
              <li key={i}>
                {o.sigungu} · {o.ordinance} {o.article}
                <br />
                기준일 <b>{o.cutoff_date}</b>{' '}
                <em className="basis">
                  {o.cutoff_is_transition
                    ? '(경과조치를 담은 개정의 시행일)'
                    : '(경과조치 부칙을 찾지 못해 조례 최신 시행일로 대신함)'}
                </em>
                {o.effective_date && o.effective_date !== o.cutoff_date && (
                  <em className="basis"> · 조례 최신 시행일 {o.effective_date}</em>
                )}
                {o.permit_earlier && <em className="flag"> 허가일이 앞섬</em>}
              </li>
            ))}
          </ul>
          <p>{r.grandfathering.note}</p>
          {r.grandfathering.review_required &&
            r.grandfathering.free_if_exempt_m2 != null && (
            <p className="scenario">
              조례 이격을 적용하지 않을 경우 제약 없음 면적{' '}
              <b>{(r.grandfathering.free_if_exempt_m2 / 1e4).toLocaleString()} ha</b>
              {' '}({((r.grandfathering.free_if_exempt_m2 / r.total.area_m2) * 100).toFixed(1)}%)
              <em> — 참고용이며 면제 확정이 아닙니다</em>
            </p>
          )}
          {r.grandfathering.ordinances.some(o => o.addenda) && (
            <details>
              <summary>부칙 원문 보기</summary>
              <pre>{r.grandfathering.ordinances.map(o => o.addenda).filter(Boolean)[0]}</pre>
            </details>
          )}
        </div>
      )}
      {r.fetch_failures.length > 0 && (
        <p className="ws-area-warn err">
          <b>조회 실패 {r.fetch_failures.length}건</b> — 보지 못한 제약이 있어
          가용면적이 실제보다 크게 나올 수 있습니다: {r.fetch_failures.join(' / ')}
        </p>
      )}

      <table className="ws-area-tbl">
        <thead><tr><th>제약 사유</th><th>판정</th><th>면적</th><th>비율</th></tr></thead>
        <tbody>
          {r.by_reason.length === 0 && (
            <tr><td colSpan={4} className="muted">해당 없음</td></tr>
          )}
          {r.by_reason.map((b, i) => (
            <tr key={i}>
              <td>{b.layer}{b.provisional && <em className="prov"> 잠정</em>}</td>
              <td>{STATUS_LABEL[b.status]}</td>
              <td>{b.ha.toLocaleString()} ha</td>
              <td>{(b.ratio * 100).toFixed(1)}%</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="ws-area-side">
        <div>
          <h5>용도지역</h5>
          <p className="muted">
            국토계획법이 전 국토를 4종으로 나눈 분류라 제약 면적과 축이 다릅니다.
          </p>
          <ul>
            {r.zoning.map((z, i) => (
              <li key={i}>{z.layer} <b>{(z.ratio * 100).toFixed(1)}%</b></li>
            ))}
            {r.zoning.length === 0 && <li className="muted">조회되지 않음</li>}
          </ul>
        </div>
        <div>
          <h5>구역 전체 조건</h5>
          <p className="muted">구역을 통째로 덮어 위치를 가르지 못하는 항목입니다.</p>
          <ul>
            {r.blanket.map((b, i) => <li key={i}>{b.layer}</li>)}
            {r.blanket.length === 0 && <li className="muted">해당 없음</li>}
          </ul>
        </div>
        <div>
          <h5>관할 지자체 · 조례</h5>
          <ul>
            {r.jurisdictions.map(j => (
              <li key={j.code}>
                {j.sigungu} <b>{(j.ratio * 100).toFixed(1)}%</b>
                {' · '}{ORDINANCE_STATE_LABEL[j.ordinance_state]}
                {j.rule_count > 0 && ` (최대 ${j.max_distance_m.toLocaleString()}m)`}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
