import { useEffect, useMemo, useRef, useState } from 'react'
import DateInput from '../../components/DateInput'
import SitePicker, { type PickMode } from './SitePicker'
import { windsiteApi } from './api'
import {
  CONFIDENCE_HINT,
  CONFIDENCE_LABEL,
  DIFFICULTY_LABEL,
  STATUS_LABEL,
  UNKNOWN_REASON_HINT,
  UNKNOWN_REASON_LABEL,
  type CompareCandidate,
  type CompareResult,
  type AreaItem,
  type PermitStep,
  type AreaResult,
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
  const [address, setAddress] = useState('');
  const [sido, setSido] = useState('');
  const [sigungu, setSigungu] = useState('');
  const [capacity, setCapacity] = useState('');

  const [laws, setLaws] = useState<LawRef[]>([]);
  const [config, setConfig] = useState<ProviderConfigRow[]>([]);
  const [tab, setTab] = useState<Tab>('result');
  const [error, setError] = useState('');

  // 보고서 내려받기

  // 후보지 비교 — 현재 지점을 후보로 담아 최대 5곳까지 비교한다
  const [candidates, setCandidates] = useState<CompareCandidate[]>([]);
  const [compareResult, setCompareResult] = useState<CompareResult | null>(null);
  const [comparing, setComparing] = useState(false);

  // 지도 클릭 → 역지오코딩 진행 상태

  // 사업구역(폴리곤) 검토 — 점 검토와 별도 상태로 둔다. 둘을 한 변수에
  // 합치면 모드를 오갈 때 서로의 입력을 지우게 된다.
  const [pickMode, setPickMode] = useState<PickMode>('layout');
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
  /** 진행 중인 보고서 작업 — 중단할 때 서버에 알릴 id와 fetch 취소 핸들 */
  const reportJob = useRef<{ id: string; abort: AbortController } | null>(null);
  const [reportProgress, setReportProgress] = useState<{ percent: number; stage: string } | null>(null);

  /** 검토와 보고서가 같은 입력을 쓰도록 한 곳에서 만든다 */
  function areaBody(extra: Record<string, unknown> = {}) {
    const base = pickMode === 'layout'
      ? { turbines: ring, turbine_radius_m: turbineR, corridor_radius_m: corridorR }
      : { ring };
    return { ...base, permit_date: permitDate, capacity_mw: capacity || null, ...extra };
  }

  /**
   * 규제 62개 항목까지 함께 받을지.
   * 지점이 적으면 몇 초라 바로 보여주고, 많으면 버튼으로 따로 부른다 —
   * 검토 실행이 매번 수 분 걸리면 배치를 다듬을 수가 없다.
   */
  const ITEMS_AUTO_MAX = 3;

  async function downloadAreaReport() {
    const id = (crypto.randomUUID?.() ?? String(Date.now()));
    const abort = new AbortController();
    reportJob.current = { id, abort };
    setAreaReporting(true); setError(''); setReportProgress({ percent: 0, stage: '시작' });

    // 동기 응답이라 진행률을 흘려보낼 수 없다. 서버가 Redis에 남긴 값을
    // 따로 물어본다. 작업이 끝나면 finally에서 멈춘다.
    const poll = window.setInterval(async () => {
      try {
        const p = await windsiteApi.areaReportProgress(id);
        if (p.running) setReportProgress({ percent: p.percent, stage: p.stage });
      } catch { /* 폴링 실패는 무시한다 — 본 작업과 무관하다 */ }
    }, 3000);

    try {
      await windsiteApi.downloadAreaReport({ ...areaBody(), job_id: id }, abort.signal);
    } catch (e) {
      // 사용자가 끊은 것은 오류가 아니다
      if (!(e instanceof DOMException && e.name === 'AbortError')) {
        setError(e instanceof Error ? e.message : '보고서 생성에 실패했습니다.');
      }
    } finally {
      window.clearInterval(poll);
      reportJob.current = null;
      setAreaReporting(false);
      setReportProgress(null);
    }
  }

  async function loadItems() {
    setAreaLoading(true); setError('');
    try {
      setAreaResult(await windsiteApi.evaluateArea(areaBody({ with_items: true })));
    } catch (e) {
      setError(e instanceof Error ? e.message : '규제 항목 조회에 실패했습니다.');
    } finally {
      setAreaLoading(false);
    }
  }

  function cancelAreaReport() {
    const job = reportJob.current;
    if (!job) return;
    // 서버에 먼저 알린 뒤 화면을 푼다. 순서를 바꾸면 fetch가 끊긴 뒤
    // 취소 요청이 도착해, 그 사이 서버가 다음 호기 조회를 시작한다.
    windsiteApi.cancelAreaReport(job.id).catch(() => {});
    job.abort.abort();
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
      setAreaResult(await windsiteApi.evaluateArea(
        areaBody({ with_items: ring.length <= ITEMS_AUTO_MAX })));
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

  /**
   * 후보지로 담기 — 지점 1곳을 찍었을 때만 담는다.
   *
   * 후보지 비교는 '여러 후보를 같은 기준으로 줄 세우는' 기능이라 후보 하나가
   * 한 지점이어야 뜻이 통한다. 배치선 전체를 후보로 담으면 무엇을 비교한
   * 것인지 알 수 없다.
   */
  const addCandidate = () => {
    if (pickMode !== 'layout' || ring.length !== 1) {
      setError('지점을 한 곳만 찍은 상태에서 담을 수 있습니다.');
      return;
    }
    const [lat, lng] = ring[0];
    if (candidates.length >= 5) {
      setError('한 번에 비교 가능한 후보는 최대 5곳입니다.');
      return;
    }
    setError('');
    setCandidates(prev => [...prev, {
      label: (turbineAddrs[0] || `후보 ${prev.length + 1}`),
      lat, lng, radius_m: turbineR,
      address: turbineAddrs[0] || '', sido, sigungu,
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


  /** 항목을 영역별로 묶는다 — 심각한 것이 위로 오도록 이미 정렬돼 온다 */
  const mergedByCategory = useMemo(() => {
    const m = new Map<string, AreaItem[]>();
    (areaResult?.items?.merged ?? []).forEach(i => {
      const arr = m.get(i.category) ?? [];
      arr.push(i);
      m.set(i.category, arr);
    });
    return Array.from(m.entries());
  }, [areaResult]);

  /**
   * 인허가 로드맵 — 설비용량만으로 정해지므로 지점 검토와 별개로 조회한다.
   * 종전에는 지점 검토 결과에 얹혀 있어, 검토를 돌려야만 볼 수 있었다.
   */
  const [permits, setPermits] = useState<PermitStep[]>([]);
  useEffect(() => {
    if (tab !== 'permits') return;
    windsiteApi.permits(capacity ? Number(capacity) : null)
      .then(d => setPermits(d.results))
      .catch(() => setPermits([]));
  }, [tab, capacity]);

  const notConfigured = config.filter(c => c.configured === false);

  return (
    <div className="ws-view">
      {/* ── 입력부 ── */}
      <div className="ws-top">
        <div className="ops-card ws-inputcard">
          <div className="ops-card-hd"><span className="tag">SITE</span> 사업지 지정</div>
          <div className="ops-card-bd">
            <div className="ws-modebar">
              {(['layout', 'area'] as const).map(m => (
                <button key={m} type="button"
                  className={pickMode === m ? 'on' : ''}
                  onClick={() => {
                    setPickMode(m); setRing([]); setAreaResult(null); setTurbineAddrs([]);
                  }}>
                  {m === 'layout' ? '지점·배치선 검토' : '구역 검토'}
                </button>
              ))}
              {(
                <span className="ws-modeacts">
                  <button type="button" disabled={!ring.length}
                    onClick={() => setRing(ring.slice(0, -1))}>되돌리기</button>
                  <button type="button" disabled={!ring.length}
                    onClick={() => {
                      setRing([]); setAreaResult(null); setTurbineAddrs([]);
                    }}>지우기</button>

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
            {(
              <div className="ws-radrow">
                <label>발전사업허가일 <span className="opt">(선택)</span>
                  <DateInput value={permitDate} onChange={setPermitDate} /></label>
                <em>조례 시행일보다 앞서면 부칙 경과조치 검토 대상으로 표시합니다</em>
              </div>
            )}
            <SitePicker
              lat={null} lng={null} radiusM={0}
              onPick={() => {}}
              mode={pickMode}
              ring={ring}
              onRingChange={setRing}
              turbineRadiusM={turbineR}
              corridorRadiusM={corridorR}
              overlays={areaResult?.overlays ?? null}
            />
            {areaResult && (
              <AreaSummary r={areaResult} />
            )}
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
                    ? `(지점 ${ring.length}곳 · 클릭 시 자동 입력)`
                    : '(구역 검토 — 지도에서 꼭짓점을 찍으십시오)'}
                </em>
              </span>
              {pickMode === 'layout' ? (
                ring.length === 0 ? (
                  <p className="ws-addr-empty">지도에서 지점을 찍으면 주소가 표시됩니다. 한 곳이면 지점 검토, 여러 곳이면 배치선 검토입니다.</p>
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
                <span>설비용량 (MW) <em>(선택)</em></span>
                <input type="number" min={0} step={0.1} value={capacity} placeholder="60"
                  onChange={e => setCapacity(e.target.value)} />
              </label>
            </div>

            {/* 실행 버튼은 모드에 따라 하는 일이 다르다. 지점 검토용 버튼을
                배치선 모드에서도 그대로 두면, 위·경도가 비어 있어 '사업지를
                지정하십시오'만 반복된다. */}
            <button className="btn-primary ws-run" onClick={runArea}
              disabled={areaLoading || ring.length < (pickMode === 'layout' ? 1 : 3)}>
              {areaLoading ? '검토 중…'
                : pickMode === 'layout' ? '입지타당성 검토 실행' : '구역 검토 실행'}
            </button>
            <div className="ws-actions">
              {areaReporting ? (
                <span className="ws-progress">
                  <span className="bar">
                    <i style={{ width: `${reportProgress?.percent ?? 0}%` }} />
                  </span>
                  <span className="txt">
                    {reportProgress?.percent ?? 0}%
                    {reportProgress?.stage ? ` · ${reportProgress.stage}` : ''}
                  </span>
                  <button className="ws-btn2 ws-cancel" onClick={cancelAreaReport}>
                    생성 중단
                  </button>
                </span>
              ) : (
                <>
                  <button className="ws-btn2" onClick={downloadAreaReport}
                    disabled={!areaResult || areaLoading}>
                    보고서 내려받기 (docx)
                  </button>
                  {pickMode === 'layout' && ring.length === 1 && (
                    <button className="ws-btn2" onClick={addCandidate}
                      disabled={areaLoading}>
                      후보지로 담기 {candidates.length > 0 && `(${candidates.length})`}
                    </button>
                  )}
                </>
              )}
            </div>
            <p className="ws-hint">
              {ring.length === 0
                ? (pickMode === 'layout'
                    ? '지도를 클릭해 지점을 찍으십시오. 한 곳이면 지점 검토, 여러 곳이면 배치선 검토가 됩니다.'
                    : '지도를 클릭해 사업구역 꼭짓점을 3개 이상 찍으십시오.')
                : !areaResult
                  ? '검토를 실행하면 규제 항목과 면적 분포, 제약도가 표시됩니다.'
                  : '보고서에는 종합판정·규제 62개 항목·제약도·풍황·계통·조례 경과규정이 '
                    + '포함됩니다. 지점마다 규제를 조회하므로 같은 배치의 첫 생성은 '
                    + '5~10분 걸리고, 이후 재생성은 10초 내로 끝납니다.'}
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
        !areaResult ? (
          <p className="ops-empty">지도에서 지점을 찍고 검토를 실행하십시오.</p>
        ) : !areaResult.items?.points?.length ? (
          <div className="ws-gaps">
            <b>규제 항목 상세가 아직 조회되지 않았습니다</b>
            <p>
              지점마다 62개 항목을 조회하므로 지점이 많으면 몇 분 걸립니다.
              필요할 때만 불러오도록 분리했습니다.
            </p>
            <button className="ws-btn2" onClick={loadItems} disabled={areaLoading}>
              {areaLoading ? '조회 중…' : '규제 항목 상세 불러오기'}
            </button>
          </div>
        ) : (
          <>
            <div className="ws-pointgrid">
              {areaResult.items.points.map(p => (
                <div key={p.no} className={`ws-pointcard g-${p.grade}`}>
                  <div className="no">{p.no}</div>
                  <div className="body">
                    <b>{p.score}점 · {STATUS_LABEL[p.grade]}</b>
                    <span>{p.address || `${p.lat.toFixed(5)}, ${p.lng.toFixed(5)}`}</span>
                  </div>
                </div>
              ))}
            </div>

            {mergedByCategory.map(([cat, items]) => (
              <div key={cat} className="ws-group">
                <p className="ops-section-t">
                  {cat}
                  <em className="ws-catcount"> {items.length}개</em>
                </p>
                <div className="ws-items">
                  {items.map(m => (
                    <div key={m.item_name} className={`ws-item s-${m.status}`}>
                      <div className="ws-item-hd">
                        <b>{m.item_name}</b>
                        <span className={`ws-status s-${m.status}`}>
                          {STATUS_LABEL[m.status]}
                        </span>
                      </div>
                      <p>{m.reason}</p>
                      <div className="ws-item-ft">
                        {m.law && <span>{m.law} {m.article}</span>}
                        {m.status !== 'POSSIBLE' && m.total > 1 && (
                          <span className="hit">
                            {m.hits}/{m.total}지점
                            {m.worst_no ? ` · 최악 ${m.worst_no}번` : ''}
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
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
        permits.length === 0 ? (
          <p className="ops-empty">인허가 로드맵을 불러오는 중입니다.</p>
        ) : (
          <div className="ws-roadmap">
            {permits.map(s => (
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

/** 판정 기준의 검증 수준 — 원문 대조 여부를 한눈에 보이게 한다 */
function ConfidenceBadge({ c }: { c: 'HIGH' | 'MEDIUM' | 'LOW' }) {
  return (
    <span className={`ws-conf c-${c}`} title={CONFIDENCE_HINT[c]}>
      근거 {CONFIDENCE_LABEL[c]}
    </span>
  );
}
