import { useEffect, useMemo, useRef, useState } from 'react'
import type L from 'leaflet'
import html2canvas from 'html2canvas'
import DateInput from '../../components/DateInput'
import SitePicker, { ENV_ZONING_KEY, type PickMode } from './SitePicker'
import SavedPlans from './SavedPlans'
import SolarCandidates from './SolarCandidates'
import { windsiteApi } from './api'
import { energyProfile, type EnergyType } from './energy'
import {
  CONFIDENCE_HINT,
  CONFIDENCE_LABEL,
  DIFFICULTY_LABEL,
  STATUS_LABEL,
  UNKNOWN_REASON_HINT,
  UNKNOWN_REASON_LABEL,
  type AdminBoundary,
  type CompareCandidate,
  type CompareResult,
  type AreaItem,
  type PermitStep,
  type AreaResult,
  type LatLng,
  type LawRef,
  ORDINANCE_STATE_LABEL,
  type ParcelInfo,
  type ParcelMiss,
  type ScreenResult,
  SCREEN_GRADE_LABEL,
  type ProviderConfigRow,
  type SitePlan,
} from './types'

type Tab = 'result' | 'saved' | 'cands' | 'compare' | 'permits' | 'laws' | 'config';

/** 검토 반경 기본값·허용범위 — 백엔드 engine.py의 같은 이름 상수와 맞춘다 */
const DEFAULT_RADIUS_M = 100;
const MIN_RADIUS_M = 50;
const MAX_RADIUS_M = 20000;

/**
 * 보고서에 넣을 지도 캡처의 배율과 품질.
 *
 * 배율을 2로 두면 화면(약 1,100×1,240)이 2,200×2,480이 되어 한 장이 5~8MB다.
 * 보고서 한 번에 최대 5장(제약도 + 항목별 4장)이라 요청 본문이 수십 MB로
 * 불어나 서버가 400을 돌려줬다. 1.5면 인쇄 해상도로 충분하다.
 */
const CAPTURE_SCALE = 1.5;
const CAPTURE_QUALITY = 0.85;

/**
 * 입지타당성 검토 화면 — 풍력·태양광 공용.
 *
 * 두 카테고리는 사이드바에서 완전히 갈라져 있고, 화면 상태도 서로 섞이지
 * 않는다(App.tsx가 view 이름을 key로 줘 화면을 새로 만든다). 여기서는
 * 프로파일이 정한 만큼만 달라진다 — 입력 모드, 저장·비교 기능, 안내 문구.
 */
export default function WindSiteView({ energy = 'WIND' }: { energy?: EnergyType }) {
  const P = energyProfile(energy);
  const [address, setAddress] = useState('');
  const [sido, setSido] = useState('');
  const [sigungu, setSigungu] = useState('');
  const [capacity, setCapacity] = useState('');

  // ── 주소 검색 ────────────────────────────────────────────────────────
  // 주소를 아는데 지도에서 그 자리를 찾아 들어가는 데 시간이 많이 든다.
  // 검색으로 옮겨 가고, 그 자리가 속한 읍·면 경계를 함께 깔아 준다 —
  // 경계 안에서 꼭짓점을 찍게 되어 사업지 지정이 정확해진다.
  const [query, setQuery] = useState('');
  const [searching, setSearching] = useState(false);
  const [searchMsg, setSearchMsg] = useState('');
  const [focus, setFocus] = useState<{ lat: number; lng: number; token: number } | null>(null);
  const [adminArea, setAdminArea] = useState<AdminBoundary | null>(null);

  async function searchAddress() {
    const q = query.trim();
    if (!q || searching) return;
    setSearching(true);
    setSearchMsg('');
    try {
      const g = await windsiteApi.geocode({ address: q });
      setFocus({ lat: g.lat, lng: g.lng, token: Date.now() });
      setAdminArea(g.boundary ?? null);
      // 시·도/시·군·구는 조례 조회 기준이라 검색 결과로 채워 준다. 손으로
      // 다시 적게 하면 표기가 어긋나 조례를 못 찾는 일이 생긴다.
      if (g.sido) setSido(g.sido);
      if (g.sigungu) setSigungu(g.sigungu);
      setSearchMsg(g.boundary
        ? `${g.boundary.full_name} — ${g.matched || q}`
        : `${g.matched || q} (행정구역 경계는 표시하지 못했습니다)`);
    } catch (e) {
      setSearchMsg(e instanceof Error ? e.message : '주소를 찾지 못했습니다.');
      setAdminArea(null);
    } finally {
      setSearching(false);
    }
  }

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
  const [pickMode, setPickModeRaw] = useState<PickMode>(P.modes[0]);
  /**
   * 프로파일이 허용하지 않는 모드로 들어가지 못하게 막는다.
   *
   * 태양광에서 필지 모드를 걷어냈는데도 후보 불러오기가 'parcel'을 넣어,
   * 모드바에는 '구역 검토'만 켜져 있으면서 내부는 필지 모드로 도는 상태가
   * 됐다. 실행 버튼이 '필지 검토 실행'으로 뜨고 구역 꼭짓점이 필지로 잡혔다.
   */
  const setPickMode = (m: PickMode) =>
    setPickModeRaw(P.modes.includes(m) ? m : P.modes[0]);
  const [ring, setRing] = useState<LatLng[]>([]);
  const [turbineR, setTurbineR] = useState(200);
  /**
   * 발전기별 주소. ring과 인덱스를 맞춰 둔다.
   * 클릭할 때가 아니라 ring 변화를 보고 채운다 — 되돌리기·지우기로 목록이
   * 줄어드는 경우까지 한 곳에서 맞추기 위해서다.
   */
  const [turbineAddrs, setTurbineAddrs] = useState<(string | null)[]>([]);
  const [corridorR, setCorridorR] = useState(100);
  /**
   * 필지 모드 — 클릭 좌표(ring)를 연속지적으로 되돌린 결과.
   * ring과 인덱스를 맞추지 않는다. 같은 필지를 두 번 눌러 합쳐지기도 하고,
   * 필지가 없는 자리를 눌러 빠지기도 해서 개수가 어긋나기 때문이다.
   */
  const [parcelList, setParcelList] = useState<ParcelInfo[]>([]);
  const [parcelMisses, setParcelMisses] = useState<ParcelMiss[]>([]);
  const [parcelBusy, setParcelBusy] = useState(false);
  /**
   * 후보만 보기 — '대상 아님'과 '배제'를 지도에서 감춘다.
   * 지우는 것이 아니라 접는 것이다. 집계에는 그대로 남고 언제든 펼 수 있다.
   *
   * 필지 채색 자체는 **구역 검토 실행 결과에 실려 온다**. 종전에는 지도를
   * 옮길 때마다 따로 조회했는데, 관심 밖 필지까지 훑느라 조회량의 절반을
   * 버렸다(실측 55%). 이제 사용자가 그린 구역 안만 본다.
   */
  const [candidatesOnly, setCandidatesOnly] = useState(true);
  /**
   * 지금 지도에 낼 것 — null이면 평소 제약도, 그 외에는 환경성 항목
   * 하나만 단독으로 그린다('zoning'은 용도지역 구성).
   *
   * 화면에서 직접 골라 볼 수 있게 하는 것과, 보고서 캡처가 항목마다
   * 이 값을 순서대로 바꿔가며 찍는 것, 두 가지 용도로 같이 쓴다.
   */
  const [activeEnvLayer, setActiveEnvLayer] = useState<string | null>(null);
  /**
   * 지도 시점 맞추기 신호. 저장한 사업지를 불러올 때만 올린다.
   * 좌표가 바뀔 때마다 맞추면 지도를 찍는 도중에 시점이 튄다.
   */
  const [fitToken, setFitToken] = useState(0);
  /** 발전사업허가일 — 조례 시행일보다 앞서면 부칙 경과조치 검토 대상이 된다 */
  const [permitDate, setPermitDate] = useState('');
  const [areaResult, setAreaResult] = useState<AreaResult | null>(null);
  // 새 검토 결과가 오면 이전 결과의 항목 지도 선택은 더 이상 유효하지
  // 않다 — 항목 이름이나 개수가 달라질 수 있어 평소 제약도로 되돌린다.
  useEffect(() => { setActiveEnvLayer(null); }, [areaResult]);
  const [areaLoading, setAreaLoading] = useState(false);
  const [areaReporting, setAreaReporting] = useState(false);
  /** 진행 중인 보고서 작업 — 중단할 때 서버에 알릴 id와 fetch 취소 핸들 */
  const reportJob = useRef<{ id: string; abort: AbortController } | null>(null);
  /** 보고서 제약도를 캡처할 지도. SitePicker가 만들어지면 채워진다. */
  const mapInstanceRef = useRef<L.Map | null>(null);
  const [reportProgress, setReportProgress] = useState<{ percent: number; stage: string } | null>(null);

  /** 검토와 보고서가 같은 입력을 쓰도록 한 곳에서 만든다 */
  function areaBody(extra: Record<string, unknown> = {}) {
    const base = pickMode === 'layout'
      ? { turbines: ring, turbine_radius_m: turbineR, corridor_radius_m: corridorR }
      // 필지는 좌표만 보낸다. 화면이 받아 둔 경계를 되돌려 보내지 않고
      // 서버가 연속지적에서 다시 받는다 — 판정 근거의 출처는 하나여야 한다.
      : pickMode === 'parcel'
        ? { parcels: ring }
        : { ring };
    // energy를 반드시 함께 보낸다. 빠지면 서버가 풍력으로 보고 조례 이격거리를
    // 풍력 기준(예: 주거 2,000m)으로 잡아, 태양광 가용면적이 통째로 틀어진다.
    // 사업명은 보고서 파일명과 표지에 그대로 들어간다. 저장 양식이 쓰는
    // 값과 같은 것을 보내야 저장된 배치안과 문서가 같은 이름으로 묶인다.
    return { ...base, energy: P.code, permit_date: permitDate,
             capacity_mw: capacity || null,
             project_name: saveForm.project.trim(), ...extra };
  }

  /**
   * 규제 62개 항목까지 함께 받을지.
   * 지점이 적으면 몇 초라 바로 보여주고, 많으면 버튼으로 따로 부른다 —
   * 검토 실행이 매번 수 분 걸리면 배치를 다듬을 수가 없다.
   */
  const ITEMS_AUTO_MAX = 3;

  /* ── 배치안 저장·불러오기 ─────────────────────────────────────── */
  const [saveOpen, setSaveOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedKey, setSavedKey] = useState(0);
  const [savedMsg, setSavedMsg] = useState('');
  /**
   * 검토자 이름은 **브라우저에 기억해 둔다.** 같은 사람이 하루에 여러 후보를
   * 저장하는데 매번 다시 치게 하면 빈칸으로 남기게 된다 — 비어 있으면 이력이
   * 쓸모없어진다.
   */
  const REVIEWER_KEY = 'windsite:reviewer';
  const [saveForm, setSaveForm] = useState({
    project: '', name: '', note: '',
    reviewer: localStorage.getItem(REVIEWER_KEY) || '',
  });
  /** 이미 있는 사업명 — 새 사업을 만들 셈으로 오타를 내면 목록이 갈라진다 */
  const [projectNames, setProjectNames] = useState<string[]>([]);
  useEffect(() => {
    if (!saveOpen) return;
    windsiteApi.projects(P.code)
      .then(d => setProjectNames(d.results.map(p => p.name)))
      .catch(() => setProjectNames([]));
  }, [saveOpen]);

  /**
   * 배치안 저장 — 좌표와 검토 조건을 남긴다.
   *
   * 62개 항목의 판정 전문은 담지 않는다. 규제·조례는 개정되므로 몇 달 뒤에
   * 옛 판정을 그대로 펼쳐 보이면 지금도 그런 줄 알게 된다. 점수·면적 같은
   * 요약만 산출 시점과 함께 남겨, 목록에서 견줄 때 쓴다.
   */
  async function savePlan() {
    const project = saveForm.project.trim();
    if (!project) { setError('사업명을 입력하십시오.'); return; }
    setSaving(true); setError('');
    const s = areaResult;
    try {
      // 발전시간·이용률은 후보를 견줄 때 매번 재검토할 수 없으므로 요약에
      // 함께 남긴다 — 산출 시점과 함께.
      const plan = await windsiteApi.savePlan({
        project, name: saveForm.name.trim(), note: saveForm.note,
        reviewer: saveForm.reviewer.trim(),
        energy: P.code, mode: pickMode,
        turbines: ring,
        turbine_radius_m: turbineR, corridor_radius_m: corridorR,
        capacity_mw: capacity || null, permit_date: permitDate,
        sido, sigungu,
        summary: s ? {
          points: ring.length,
          parcels: s.parcel?.count ?? s.screening?.counts?.POSSIBLE ?? undefined,
          hours_per_day: s.yield?.hours_per_day,
          capacity_factor: s.yield?.capacity_factor,
          total_ha: s.total.ha, free_ha: s.free.ha,
          blocked_ha: s.blocked.ha, conditional_ha: s.conditional.ha,
          // 여러 호기면 가장 나쁜 지점이 그 배치의 성적이다.
          ...(s.items?.points?.length
            ? (() => {
                const w = s.items!.points.reduce((a, b) => (a.score <= b.score ? a : b));
                return { score: w.score, grade: w.grade };
              })()
            : {}),
        } : {},
      });
      setSaveOpen(false);
      // 사업명과 검토자는 남긴다 — 같은 사람이 같은 사업의 후보를 잇달아
      // 저장하는 것이 보통이라, 지우면 매번 다시 치게 된다.
      setSaveForm(f => ({ project, name: '', note: '', reviewer: f.reviewer }));
      setSavedKey(k => k + 1);
      // 다음 저장 때 다시 치지 않도록 이름을 기억한다.
      if (saveForm.reviewer.trim()) {
        localStorage.setItem(REVIEWER_KEY, saveForm.reviewer.trim());
      }
      setSavedMsg(`${plan.project_name} · ${plan.name} 저장됨`);
      window.setTimeout(() => setSavedMsg(''), 4000);
    } catch (e) {
      setError(e instanceof Error ? e.message : '배치안 저장에 실패했습니다.');
    } finally {
      setSaving(false);
    }
  }

  /** 저장한 배치안을 지도와 검토 조건에 그대로 되돌린다 */
  function loadPlan(x: SitePlan) {
    setPickMode('layout');
    setRing(x.turbines);
    setTurbineAddrs([]);           // 주소는 좌표에서 다시 채운다
    setTurbineR(x.turbine_radius_m);
    setCorridorR(x.corridor_radius_m);
    setCapacity(x.capacity_mw != null ? String(x.capacity_mw) : '');
    setPermitDate(x.permit_date || '');
    setSido(x.sido); setSigungu(x.sigungu);
    // 저장된 요약은 그 시점 규제 기준이다. 결과 칸에 옛 판정을 남겨두면
    // 지금 판정으로 오인하므로 비우고 다시 실행하게 한다.
    setAreaResult(null);
    setSaveForm(f => ({ ...f, project: x.project_name, name: '', note: '' }));
    setTab('result');
    if (x.turbines.length) setFitToken(t => t + 1);
    setSavedMsg(`${x.project_name} · ${x.name} 불러옴 — 검토를 다시 실행하십시오.`);
    window.setTimeout(() => setSavedMsg(''), 6000);
  }

  /**
   * 지금 지도 화면을 PNG로 캡처한다 — 서버가 규제 레이어를 다시 그린 지도는
   * 정부 원본 데이터의 단순화나 필지 경계 처리 방식 차이로 화면과 미세하게
   * 달라 보일 수 있다(좁은 물길 근처 등). 화면을 그대로 캡처하면 그 문제
   * 자체가 성립하지 않는다 — 사용자가 실제로 본 그림이 그대로 보고서에
   * 들어간다.
   *
   * 실패해도(보안 정책으로 캔버스가 오염되는 등) 조용히 넘어간다 — 보고서는
   * 서버 렌더링으로 대체돼 계속 나온다. 지도 하나 때문에 보고서 전체를
   * 막을 이유가 없다.
   */
  async function captureMapImage(): Promise<string | null> {
    const map = mapInstanceRef.current;
    if (!map) return null;
    try {
      // 줌·레이어 선택 컨트롤은 사용자 UI지 지도 내용이 아니다. 축척
      // 막대(leaflet-control-scale)는 남긴다 — 보고서에서도 뜻이 있다.
      const canvas = await html2canvas(map.getContainer(), {
        useCORS: true,
        backgroundColor: '#ffffff',
        scale: CAPTURE_SCALE,
        ignoreElements: (el) =>
          el.classList.contains('leaflet-control-zoom')
          || el.classList.contains('leaflet-control-layers')
          || el.classList.contains('leaflet-control-attribution'),
      });
      // ⚠️ JPEG로 보낸다. 배경이 위성영상(사진)이라 PNG 무손실로 뜨면 한 장이
      //    5~8MB고, 보고서 한 번에 최대 5장이라 요청 본문이 수십 MB가 된다 —
      //    실제로 그 때문에 서버가 400(RequestDataTooBig)을 돌려줬다.
      //    품질 0.85면 지도 판독에는 차이가 없고 크기는 1/5~1/10이다.
      return canvas.toDataURL('image/jpeg', CAPTURE_QUALITY);
    } catch (e) {
      console.warn('지도 캡처 실패 — 서버 렌더링으로 대체합니다.', e);
      return null;
    }
  }

  /** state 갱신 → SitePicker의 지도 갱신 useEffect가 실제로 반영될 때까지 */
  async function waitFrame(ms = 300) {
    await new Promise(r => window.setTimeout(r, ms));
  }

  /**
   * 요약지도 + 환경성 항목 지도(용도지역 구성·개별 항목)를 화면 그대로
   * **순서대로** 캡처한다.
   *
   * "지도 보기"를 하나씩 바꿔가며 매번 찍는다 — 화면에 있는 선택 기능을
   * 그대로 자동화한 것이라, 사용자가 손으로 눌러 볼 수 있는 것과 보고서에
   * 실리는 것이 항상 같은 그림이다. 끝나면 원래 보던 화면(전체 제약도)으로
   * 되돌린다.
   */
  async function captureAllMaps(): Promise<{ mapImage: string | null; envImages: Record<string, string> }> {
    const mapImage = await captureMapImage();
    const layers = areaResult?.env_layers ?? [];
    const envImages: Record<string, string> = {};
    if (!layers.length) return { mapImage, envImages };

    const targets = [
      ...(layers.some(l => l.kind === 'zoning') ? [ENV_ZONING_KEY] : []),
      ...layers.filter(l => l.kind === 'item').map(l => l.name),
    ];
    for (const key of targets) {
      setActiveEnvLayer(key);
      await waitFrame();
      const img = await captureMapImage();
      if (img) envImages[key] = img;
    }
    setActiveEnvLayer(null);
    await waitFrame(100);
    return { mapImage, envImages };
  }

  async function downloadAreaReport() {
    const id = (crypto.randomUUID?.() ?? String(Date.now()));
    const abort = new AbortController();
    reportJob.current = { id, abort };
    setAreaReporting(true); setError('');
    setReportProgress({ percent: 0, stage: '지도 캡처' });

    const { mapImage, envImages } = await captureAllMaps();
    setReportProgress({ percent: 0, stage: '시작' });

    // 동기 응답이라 진행률을 흘려보낼 수 없다. 서버가 Redis에 남긴 값을
    // 따로 물어본다. 작업이 끝나면 finally에서 멈춘다.
    const poll = window.setInterval(async () => {
      try {
        const p = await windsiteApi.areaReportProgress(id);
        if (p.running) setReportProgress({ percent: p.percent, stage: p.stage });
      } catch { /* 폴링 실패는 무시한다 — 본 작업과 무관하다 */ }
    }, 3000);

    try {
      // 화면이 알고 있는 사업구역 경계를 함께 보낸다. 서버 재계산과
      // 다르면(그 사이 코드·데이터가 바뀐 경우) 서버가 캡처를 버리고
      // 모든 지도를 서버 렌더로 통일한다 — 문서 안에서 지도마다 다른
      // 경계가 나가는 일을 서버 쪽에서 차단한다.
      await windsiteApi.downloadAreaReport(
        { ...areaBody(), job_id: id, map_image: mapImage, env_images: envImages,
          site_rings: areaResult?.site_rings ?? null },
        abort.signal);
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
    if (pickMode === 'parcel') {
      if (!parcelList.length) {
        setError('지도에서 필지를 한 곳 이상 선택해 주세요.');
        return;
      }
    } else if (pickMode === 'layout' ? ring.length < 1 : ring.length < 3) {
      setError(pickMode === 'layout'
        ? '발전기 위치를 1기 이상 찍어주세요.'
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
   * 클릭한 좌표를 필지로 되돌린다.
   *
   * 좌표가 늘 때마다 **새로 찍은 것만** 조회한다. 전체를 다시 돌리면
   * 필지를 하나 추가할 때마다 호출이 제곱으로 는다.
   *
   * 필지를 못 찾은 좌표는 지우지 않고 사유와 함께 남긴다 — '여기엔 필지가
   * 없다'(도로·하천)와 '조회하지 못했다'는 전혀 다른 사실이고, 화면이
   * 그것을 구분해 말해야 다음 행동을 정할 수 있다.
   */
  useEffect(() => {
    if (pickMode !== 'parcel') return;
    if (!ring.length) { setParcelList([]); setParcelMisses([]); return; }

    let alive = true;
    (async () => {
      // 지운 좌표에 딸린 필지·실패기록을 먼저 떨어낸다
      const key = (a: number, o: number) => `${a.toFixed(5)},${o.toFixed(5)}`;
      const live = new Set(ring.map(([a, o]) => key(a, o)));
      let list = parcelList.filter(p => live.has(key(p.lat, p.lng)));
      let misses = parcelMisses.filter(m => live.has(key(m.lat, m.lng)));
      const known = new Set([...list.map(p => key(p.lat, p.lng)),
                             ...misses.map(m => key(m.lat, m.lng))]);
      const todo = ring.filter(([a, o]) => !known.has(key(a, o)));
      if (!todo.length) {
        if (list.length !== parcelList.length) setParcelList(list);
        if (misses.length !== parcelMisses.length) setParcelMisses(misses);
        return;
      }

      setParcelBusy(true);
      for (const [a, o] of todo) {
        try {
          const r = await windsiteApi.parcel({ lat: a, lng: o });
          if (!alive) return;
          if (r.found && r.parcel) {
            // 같은 필지를 두 번 눌렀으면 합친다 — 면적이 두 배로 잡히지 않도록
            if (!list.some(p => p.pnu && p.pnu === r.parcel!.pnu)) {
              list = [...list, r.parcel];
            }
          } else {
            misses = [...misses, { lat: a, lng: o,
                                   reason: 'NO_PARCEL',
                                   detail: r.detail || '해당 좌표에 필지가 없습니다.' }];
          }
        } catch (e) {
          if (!alive) return;
          misses = [...misses, { lat: a, lng: o, reason: 'FETCH',
                                 detail: e instanceof Error ? e.message : '조회 실패' }];
        }
        setParcelList(list); setParcelMisses(misses);
      }
      if (alive) setParcelBusy(false);

      // 시·도/시·군·구는 첫 필지 기준으로 채운다(조례 조회 기준값).
      const head = list[0];
      if (alive && head && !sigungu) {
        try {
          const g = await windsiteApi.geocode({ lat: head.lat, lng: head.lng });
          if (alive) { setSido(g.sido || ''); setSigungu(g.sigungu || ''); }
        } catch { /* 주소를 못 받아도 좌표 지정은 유효하다 */ }
      }
    })();
    return () => { alive = false; };
    // parcelList/parcelMisses를 의존성에 넣으면 갱신마다 다시 돌아 무한루프가 된다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pickMode, ring]);

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
    windsiteApi.laws(P.code).then(d => setLaws(d.results)).catch(() => setLaws([]));
    windsiteApi.config().then(d => setConfig(d.results)).catch(() => setConfig([]));
  }, [P.code]);

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
    windsiteApi.permits(capacity ? Number(capacity) : null, P.code)
      .then(d => setPermits(d.results))
      .catch(() => setPermits([]));
  }, [tab, capacity, P.code]);

  const notConfigured = config.filter(c => c.configured === false);

  return (
    <div className="ws-view">
      {/* ── 입력부 ── */}
      <div className="ws-top">
        <div className="ops-card ws-inputcard">
          <div className="ops-card-hd"><span className="tag">SITE</span> 사업지 지정</div>
          <div className="ops-card-bd">
            <div className="ws-modebar">
              {P.modes.map(m => (
                <button key={m} type="button"
                  className={pickMode === m ? 'on' : ''}
                  onClick={() => {
                    setPickMode(m); setRing([]); setAreaResult(null);
                    setTurbineAddrs([]); setParcelList([]); setParcelMisses([]);
                  }}>
                  {m === 'layout' ? '지점·배치선 검토'
                    : m === 'parcel' ? '필지 검토' : '구역 검토'}
                </button>
              ))}
              {(
                <span className="ws-modeacts">
                  <button type="button" disabled={!ring.length}
                    onClick={() => setRing(ring.slice(0, -1))}>되돌리기</button>
                  <button type="button" disabled={!ring.length}
                    onClick={() => {
                      setRing([]); setAreaResult(null); setTurbineAddrs([]);
                      setParcelList([]); setParcelMisses([]);
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
            {!!areaResult?.env_layers?.length && (
              <label className="ws-fld" style={{ marginBottom: 6 }}>
                <span>지도 보기</span>
                <select value={activeEnvLayer ?? ''}
                  onChange={e => setActiveEnvLayer(e.target.value || null)}>
                  <option value="">전체 제약도</option>
                  {areaResult.env_layers.some(l => l.kind === 'zoning') && (
                    <option value={ENV_ZONING_KEY}>용도지역 구성</option>
                  )}
                  {areaResult.env_layers.filter(l => l.kind === 'item').map(l => (
                    <option key={l.name} value={l.name}>{l.name}</option>
                  ))}
                </select>
              </label>
            )}
            <SitePicker
              lat={null} lng={null} radiusM={0}
              onPick={() => {}}
              mode={pickMode}
              ring={ring}
              onRingChange={setRing}
              turbineRadiusM={turbineR}
              corridorRadiusM={corridorR}
              parcels={parcelList}
              screening={!areaResult?.screening?.too_wide
                ? (areaResult?.screening?.parcels ?? []).filter(
                    p => !candidatesOnly
                      || (p.grade !== 'NOT_APPLICABLE' && p.grade !== 'IMPOSSIBLE'))
                : null}
              fitToken={fitToken}
              overlays={areaResult?.overlays ?? null}
              siteRings={areaResult?.site_rings ?? null}
              overlayLabels={areaResult?.overlay_labels}
              roadDetail={areaResult?.road_detail ?? null}
              onMapReady={(map) => { mapInstanceRef.current = map; }}
              envLayers={areaResult?.env_layers}
              activeEnvLayer={activeEnvLayer}
              focus={focus}
              adminBoundary={adminArea?.rings ?? null}
              adminLabel={adminArea?.full_name}
            />
            {areaResult?.screening && (
              <ScreenPanel r={areaResult.screening} busy={areaLoading}
                onlyCandidates={candidatesOnly}
                onToggleOnly={setCandidatesOnly} />
            )}
            {areaResult && (
              <AreaSummary r={areaResult} />
            )}
          </div>
        </div>

        <div className="ops-card ws-formcard">
          <div className="ops-card-hd"><span className="tag">INPUT</span> 검토 조건</div>
          <div className="ops-card-bd">
            {/* 주소로 지도를 옮긴다. 사업지를 찍기 **전에** 쓰는 것이라
                맨 위에 둔다 — 아래 입력은 찍고 난 뒤에 채워진다. */}
            <label className="ws-fld ws-search">
              <span>주소로 찾기 <em>(사업지 위치로 지도 이동)</em></span>
              <div className="ws-search-row">
                <input type="text" value={query}
                  placeholder="강원특별자치도 평창군 방림면"
                  onChange={e => setQuery(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); searchAddress(); } }} />
                <button type="button" className="ws-btn" onClick={searchAddress}
                  disabled={searching || !query.trim()}>
                  {searching ? '찾는 중…' : '검색'}
                </button>
              </div>
              {searchMsg && <p className="ws-search-msg">{searchMsg}</p>}
              {adminArea && (
                <button type="button" className="ws-linkbtn"
                  onClick={() => { setAdminArea(null); setSearchMsg(''); }}>
                  행정구역 경계 지우기
                </button>
              )}
            </label>
            <label className="ws-fld">
              <span>
                사업지 주소{' '}
                <em>
                  {pickMode === 'layout'
                    ? `(지점 ${ring.length}곳 · 클릭 시 자동 입력)`
                    : pickMode === 'parcel'
                      ? `(필지 ${parcelList.length}개${parcelBusy ? ' · 조회 중…' : ''})`
                      : '(구역 검토 — 지도에서 꼭짓점을 찍으십시오)'}
                </em>
              </span>
              {pickMode === 'parcel' ? (
                <ParcelPanel list={parcelList} misses={parcelMisses}
                             busy={parcelBusy} />
              ) : pickMode === 'layout' ? (
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
              disabled={areaLoading || parcelBusy
                || (pickMode === 'parcel'
                    ? parcelList.length < 1
                    : ring.length < (pickMode === 'layout' ? 1 : 3))}>
              {areaLoading ? '검토 중…'
                : pickMode === 'layout' ? '입지타당성 검토 실행'
                : pickMode === 'parcel' ? '필지 검토 실행' : '구역 검토 실행'}
            </button>
            {/* 사업명은 보고서 **파일명과 표지**에 그대로 들어간다.
                종전에는 「배치안 저장」 패널 안에만 있어, 접혀 있는 동안에는
                보고서를 받는 사람 눈에 보이지 않았다. 같은 상태를 쓰므로
                어느 쪽에서 채워도 다른 쪽에 그대로 반영된다. */}
            {areaResult && !areaReporting && (
              <label className="ws-fld ws-report-name">
                <span>사업명 <em>(보고서 파일명·표지에 들어갑니다)</em></span>
                <input value={saveForm.project} maxLength={120} list="ws-projects"
                  placeholder="예) 장흥 염해농지 태양광"
                  onChange={e => setSaveForm(f => ({ ...f, project: e.target.value }))} />
              </label>
            )}
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
                  <button className="ws-btn2"
                    onClick={downloadAreaReport}
                    disabled={!areaResult || areaLoading}>
                    보고서 내려받기 (docx)
                  </button>
                  {P.allowPlans && pickMode === 'layout' && (
                    <button className="ws-btn2" disabled={!ring.length || areaLoading}
                      onClick={() => { setSaveOpen(true); setError(''); }}>
                      배치안 저장
                    </button>
                  )}
                  {P.hasCandidates && pickMode === 'area' && (
                    <button className="ws-btn2"
                      disabled={ring.length < 3 || areaLoading}
                      onClick={() => { setSaveOpen(true); setError(''); }}>
                      후보로 담기
                    </button>
                  )}
                  {P.allowCompare && pickMode === 'layout' && ring.length === 1 && (
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
                    : pickMode === 'parcel'
                      ? '지도를 확대해 필지를 클릭하십시오. 인접 필지를 여러 개 고르면 하나의 사업지로 묶어 검토합니다.'
                      : '지도를 클릭해 사업구역 꼭짓점을 3개 이상 찍으십시오.')
                : !areaResult
                  ? '검토를 실행하면 규제 항목과 면적 분포, 제약도가 표시됩니다.'
                  : '보고서에는 종합판정·규제 62개 항목·제약도·'
                    + (P.hasWindResource ? '풍황·' : '')
                    + '계통·조례 경과규정이 포함됩니다. 지점마다 규제를 조회하므로 '
                    + '같은 부지의 첫 생성은 5~10분 걸리고, 이후 재생성은 10초 내로 '
                    + '끝납니다. 파일명에는 위 「사업명」이 들어가므로 먼저 채워 '
                    + '두십시오.'}
            </p>

            {savedMsg && <p className="ws-ok">{savedMsg}</p>}
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
          ...(P.allowPlans ? [['saved', '저장한 배치안']] : []),
          ...(P.hasCandidates ? [['cands', '후보 필지']] : []),
          ...(P.allowCompare
            ? [['compare', `후보지 비교${candidates.length ? ` (${candidates.length})` : ''}`]]
            : []),
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
                        {/* '최악 N호기'는 붙이지 않는다. 여기 적히는 호기들은
                            모두 같은 판정을 받은 것이라, 그중 하나를 최악으로
                            지목하면 나머지가 더 나은 것처럼 읽힌다. */}
                        {m.status !== 'POSSIBLE' && m.hits > 0 && (
                          <span className="hit">{m.hit_label}</span>
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

      {/* ── 저장한 배치안 ── */}
      {tab === 'saved' && (
        <SavedPlans onLoad={loadPlan} refreshKey={savedKey} />
      )}

      {/* ── 후보지 비교 ── */}
      {tab === 'cands' && (
        <SolarCandidates refreshKey={savedKey} onLoad={(x) => {
          // 좌표와 조건만 되돌린다. 저장된 요약은 그때의 규제 기준이므로
          // 결과 칸에 남겨 두면 지금 판정으로 오인한다.
          //
          // ⚠️ 모드를 **저장된 그대로** 되돌려야 한다. 종전에는 무조건
          //    'parcel'로 두어, 구역으로 담은 후보를 불러오면 **폴리곤
          //    꼭짓점이 필지 클릭 좌표로 해석**됐다. 꼭짓점마다 필지가
          //    하나씩 잡혀 22개 구역이 22필지로 둔갑했다.
          const mode: PickMode = P.modes.includes(x.mode as PickMode)
            ? (x.mode as PickMode) : P.modes[0];
          setPickMode(mode);
          // 모드가 달라지면 좌표의 뜻도 달라진다(꼭짓점 vs 필지 클릭점).
          // 그대로 밀어 넣으면 22개 꼭짓점이 22필지로 읽힌다.
          setRing(mode === x.mode ? x.turbines : []);
          setParcelList([]); setParcelMisses([]);
          setCapacity(x.capacity_mw != null ? String(x.capacity_mw) : '');
          setPermitDate(x.permit_date || '');
          setSido(x.sido); setSigungu(x.sigungu);
          setAreaResult(null);
          setTab('result');
          // 좌표를 되살렸으면 그 사업지가 보이도록 지도를 맞춘다 —
          // 전국 시점에 점만 찍혀 있으면 매번 손으로 찾아 들어가야 한다.
          if (mode === x.mode && x.turbines.length) setFitToken(t => t + 1);
          // 지금 화면에 없는 모드로 담아 둔 옛 후보는 좌표의 뜻이 달라
          // 그대로 되살릴 수 없다. 조용히 다른 모드로 밀어 넣지 않고 알린다.
          setSavedMsg(mode === x.mode
            ? `${x.project_name} · ${x.name} 불러옴 — 검토를 다시 실행하십시오.`
            : `${x.project_name} · ${x.name}은(는) 지금은 쓰지 않는 '${x.mode}' 방식으로 `
              + '담긴 후보라 좌표를 그대로 되살릴 수 없습니다. 지도에서 구역을 다시 '
              + '지정해 주십시오.');
          window.setTimeout(() => setSavedMsg(''), 8000);
        }} />
      )}

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
          <p className="ops-empty">
            {P.hasPermitSeed
              ? '인허가 로드맵을 불러오는 중입니다.'
              : `${P.label} 인허가 절차는 아직 등록되지 않았습니다. 절차가 없다는 뜻이 `
                + '아니라 데이터가 없다는 뜻이며, 풍력 절차를 대신 보여 주지 '
                + '않습니다(발전사업허가 소관·용량 경계가 다릅니다).'}
          </p>
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
          {laws.length === 0 && (
            <p className="ops-empty">
              {P.label} 관련 법령이 등록되지 않았습니다. 시드 등록 전까지는
              목록을 비워 둡니다 — 다른 에너지원의 법령을 대신 싣지 않습니다.
            </p>
          )}
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

      {/* ── 배치안 저장 ── */}
      {saveOpen && (
        <div className="ws-modal-bg" onClick={() => setSaveOpen(false)}>
          <div className="ws-modal" onClick={e => e.stopPropagation()}>
            <b>{P.hasCandidates && pickMode === 'area' ? '후보로 담기' : '배치안 저장'}</b>
            {P.hasCandidates && pickMode === 'area' ? (
              <p className="ws-modal-note">
                구역 꼭짓점 {ring.length}개와 검토 조건을 남깁니다.
                {areaResult?.screening
                  && ` 구역 안 필지 ${areaResult.screening.counts.POSSIBLE ?? 0}개가 '가능'입니다.`}
                {areaResult
                  ? ' 지금 검토 결과의 면적·발전시간 요약도 산출 시점과 함께 저장됩니다.'
                  : ' 아직 검토를 실행하지 않아 좌표만 저장됩니다.'}
                {' '}판정 전문은 담지 않습니다 — 규제·조례는 개정되므로 불러올 때
                그 시점 기준으로 다시 검토합니다.
              </p>
            ) : (
            <p className="ws-modal-note">
              지점 {ring.length}곳과 검토 조건(반경 {turbineR}/{corridorR}m
              {permitDate && ` · 허가일 ${permitDate}`})을 남깁니다.
              {areaResult
                ? ' 지금 검토 결과의 요약도 산출 시점과 함께 저장됩니다.'
                : ' 아직 검토를 실행하지 않아 좌표만 저장됩니다.'}
            </p>
            )}
            <label className="ws-fld">
              <span>사업명 <em>(없으면 새로 만듭니다)</em></span>
              <input value={saveForm.project} maxLength={120} list="ws-projects"
                placeholder="예) 삼척 천봉풍력"
                onChange={e => setSaveForm(f => ({ ...f, project: e.target.value }))} />
              <datalist id="ws-projects">
                {projectNames.map(n => <option key={n} value={n} />)}
              </datalist>
            </label>
            <label className="ws-fld">
              <span>{P.hasCandidates ? '후보명' : '배치안명'}{' '}
                <em>(비우면 {P.hasCandidates ? '후보' : '배치안'} 1, 2… 로 붙습니다)</em></span>
              <input value={saveForm.name} maxLength={120} placeholder="배치안 1"
                onChange={e => setSaveForm(f => ({ ...f, name: e.target.value }))} />
            </label>
            <label className="ws-fld">
              <span>검토자 <em>(담당자명 — 이력에 남습니다)</em></span>
              <input value={saveForm.reviewer} maxLength={60} placeholder="예) 홍길동"
                onChange={e => setSaveForm(f => ({ ...f, reviewer: e.target.value }))} />
            </label>
            <label className="ws-fld">
              <span>메모 <em>(무엇을 바꿨는지 적어두면 나중에 알아봅니다)</em></span>
              <textarea rows={3} value={saveForm.note}
                placeholder="예) 3호기를 능선 남쪽으로 300m 이설 — 주거 이격 회피"
                onChange={e => setSaveForm(f => ({ ...f, note: e.target.value }))} />
            </label>
            {error && <p className="ws-error">{error}</p>}
            <div className="ws-modal-acts">
              <button className="ws-btn2" onClick={() => setSaveOpen(false)}>취소</button>
              <button className="btn-primary" onClick={() => void savePlan()}
                disabled={saving || !saveForm.project.trim()}>
                {saving ? '저장 중…' : '저장'}
              </button>
            </div>
          </div>
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
 * 부지로 쓸 수 없는 공공용지 성격 지목.
 * 백엔드 `providers/cadastral.py`의 EXCLUDED와 같은 목록이다.
 */
const PUBLIC_JIMOK = new Set(['도로', '하천', '구거', '제방', '철도용지', '수도용지']);

/**
 * 스크리닝 현황 — 범례·집계·고지.
 *
 * 기획서 R-09가 요구하는 **예비 스크리닝 고지를 상시 노출**한다. 채색된
 * 지도는 그럴듯해서 그것만으로 판단하기 쉬운데, 이 채색과 필지 클릭
 * 정밀판정은 신뢰도가 다른 두 층이다. UI가 그 차이를 숨기면 안 된다(§3.4).
 */
function ScreenPanel({ r, busy, onlyCandidates, onToggleOnly }: {
  r: ScreenResult | null; busy: boolean;
  onlyCandidates: boolean; onToggleOnly: (v: boolean) => void;
}) {
  if (!r) {
    return <p className="ws-screen-notice">{busy ? '필지를 조회하는 중입니다…'
      : '지도를 움직이면 화면 범위의 필지를 등급별로 칠합니다.'}</p>;
  }
  if (r.too_wide) {
    // 오류가 아니라 '더 확대하라'는 상태다. 붉게 띄우지 않는다.
    return <p className="ws-screen-notice">{r.detail}</p>;
  }

  const total = Object.values(r.counts).reduce((a, b) => a + b, 0);
  const na = r.counts.NOT_APPLICABLE ?? 0;
  return (
    <>
      <div className="ws-legend">
        {(['POSSIBLE', 'CONDITIONAL', 'IMPOSSIBLE', 'UNKNOWN',
           'NOT_APPLICABLE'] as const).map(g => (
          <span key={g}>
            <i className={`g-${g}`} />
            {SCREEN_GRADE_LABEL[g]} <b>{(r.counts[g] ?? 0).toLocaleString()}</b>
          </span>
        ))}
        {/* 도로 이격은 채움이 아니라 선·파선이라 네모 범례로는 알 수 없다.
            선 모양 그대로 보여 준다 — 지도의 무엇을 가리키는지가 바로 보인다. */}
        <span className="ws-legend-road" title="조례가 정한 도로 이격 — 선은 기준 도로, 파선은 이격 범위">
          <i className="road-blocked" />배제 도로
          <i className="road-uncertain" />조건부 도로
        </span>
        <label className="ws-screen-toggle" title="대상 아님·배제 필지를 지도에서 감춥니다">
          <input type="checkbox" checked={onlyCandidates}
            onChange={e => onToggleOnly(e.target.checked)} />
          {' '}후보만 보기
        </label>
        <span className="ws-legend-sum">
          화면 {r.area_km2} km² · 필지 {total.toLocaleString()}개{busy && ' · 갱신 중…'}
        </span>
      </div>

      {na > 0 && (
        <p className="ws-screen-notice">
          <b>대상 아님 {na.toLocaleString()}필지</b>를 후보에서 뺐습니다 —
          면적 {r.min_area_m2.toLocaleString()}㎡ 미만, 발전부지가 될 수 없는
          지목(도로·하천·학교·묘지 등), 그리고 <b>건축물이 필지를 덮고 있는
          경우</b>(마을·축사·공장, 건축물 {r.building_count.toLocaleString()}동 대조)
          입니다. <b>규제 때문에 배제된 것이 아니라 애초에 후보가 아닌</b>
          땅이며, '후보만 보기'를 끄면 경계선으로 확인할 수 있습니다.
        </p>
      )}

      <p className="ws-screen-notice">
        <b>본 채색은 1차 스크리닝이며 인허가 판단을 대신하지 않습니다.</b>
        {' '}<b>미확인(빗금)</b>은 제약이 없다는 뜻이 아니라 <b>조회하지 못했다</b>는
        뜻입니다. 조건부는 색이 하나이므로, <b>필지에 커서를 올리면 무엇 때문에
        조건부인지</b>(조례 이격·농업진흥지역 등) 확인할 수 있습니다.
        {' '}필지를 눌러 그 필지만 따로 정밀판정할 수도 있습니다.
      </p>

      {r.unverified.length > 0 && (
        <p className="ws-warn">
          {r.unverified.join(' · ')} 은(는) <b>이격거리 조례를 확인하지 못했습니다.</b>
          {' '}해당 관할 필지는 '가능'으로 칠하지 않고 미확인으로 남겼습니다.
        </p>
      )}
      {r.truncated && (
        <p className="ws-warn">
          필지 수가 상한에 걸려 <b>일부가 표시되지 않았습니다.</b> 이 화면에 보이는
          것이 전부가 아니므로, 더 확대해 나눠 보십시오.
        </p>
      )}
      {r.fetch_failures.length > 0 && (
        <p className="ws-warn">
          조회하지 못한 레이어 {r.fetch_failures.length}건 —{' '}
          {r.fetch_failures.slice(0, 2).join(' / ')}
          {r.fetch_failures.length > 2 && ' 외'}. <b>보지 못한 제약이 있을 수
          있어</b> 이 화면의 필지를 미확인으로 처리했습니다.
        </p>
      )}
    </>
  );
}

/**
 * 선택한 필지 목록.
 *
 * 지번·지목·면적을 함께 보여준다. 태양광은 **면적이 곧 사업 규모**라
 * 지번만 나열하면 무엇을 고른 것인지 알 수 없다.
 *
 * 못 찾은 좌표도 함께 싣는다. 조용히 빼면 필지 하나가 빠진 채 면적이 나오고,
 * 그 숫자로 부지 계약과 설계가 진행된다. 그리고 그 둘 — '여기엔 필지가
 * 없다'와 '조회하지 못했다' — 을 구분해 적는다. 앞은 확정 정보이고
 * 뒤는 재시도로 풀릴 수 있는 문제다.
 */
function ParcelPanel({ list, misses, busy }: {
  list: ParcelInfo[]; misses: ParcelMiss[]; busy: boolean;
}) {
  if (!list.length && !misses.length) {
    return (
      <p className="ws-addr-empty">
        지도를 확대해 필지를 클릭하면 지번·지목·면적이 표시됩니다.
        {busy && ' 조회 중…'}
      </p>
    );
  }

  const total = list.reduce((s, p) => s + p.area_m2, 0);
  const inexact = list.filter(p => !p.exact);
  // 공공용지 성격 지목은 부지로 쓸 수 없다. 지도만 보고 클릭하면 구거·하천을
  // 밭으로 착각하기 쉬운데(폭이 좁고 초지로 보인다), 그대로 넣으면 면적이
  // 부풀려진 채 사업 규모가 잡힌다. 빼지는 않는다 — 고른 것은 사용자다.
  const publicUse = list.filter(p => PUBLIC_JIMOK.has(p.jimok));

  return (
    <>
      <ol className="ws-addrlist">
        {list.map((p, i) => (
          <li key={p.pnu || i}>
            <span className="no">{i + 1}</span>
            <span className="addr">
              {p.addr || p.jibun || p.pnu}
              <em>
                {' '}· {p.jimok} · {p.area_m2.toLocaleString()}㎡
                ({(p.area_m2 / 3.305785).toFixed(0)}평)
              </em>
            </span>
            {!p.exact && <span className="ws-badge na" title="경계 근처를 클릭해 인접 필지를 골랐습니다">확인 필요</span>}
          </li>
        ))}
      </ol>

      <p className="ws-hint">
        선택 {list.length}필지 · 합계 {(total / 10_000).toFixed(2)} ha
        ({total.toLocaleString()}㎡ · {(total / 3.305785).toFixed(0)}평)
        {busy && ' · 조회 중…'}
      </p>

      {inexact.length > 0 && (
        <p className="ws-warn">
          {inexact.length}필지는 클릭 지점이 어느 필지에도 들지 않아 <b>가장 가까운
          필지</b>를 골랐습니다. 의도한 필지가 맞는지 지번을 확인하십시오.
        </p>
      )}

      {publicUse.length > 0 && (
        <p className="ws-warn">
          <b>{publicUse.map(p => p.jimok).filter((v, i, a) => a.indexOf(v) === i).join('·')}</b>
          {' '}지목이 포함돼 있습니다({publicUse.length}필지 ·{' '}
          {(publicUse.reduce((s, p) => s + p.area_m2, 0) / 10_000).toFixed(2)} ha).
          공공용지 성격이라 발전부지로 쓸 수 없는 것이 일반적이며, 합계 면적에
          그대로 들어가 있으니 사업 규모를 잡을 때 빼고 보십시오.
        </p>
      )}

      {misses.length > 0 && (
        <p className="ws-warn">
          {misses.filter(m => m.reason === 'NO_PARCEL').length > 0 && (
            <>클릭한 지점 {misses.filter(m => m.reason === 'NO_PARCEL').length}곳은
              연속지적에 <b>필지가 없습니다</b>(도로·하천 등). 검토에서 제외됩니다. </>
          )}
          {misses.filter(m => m.reason === 'FETCH').length > 0 && (
            <>{misses.filter(m => m.reason === 'FETCH').length}곳은 <b>조회에
              실패</b>했습니다 — 필지가 없는 것이 아니라 물어보지 못한 것입니다.
              해당 지점을 다시 클릭해 주십시오.</>
          )}
        </p>
      )}
    </>
  );
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
          {/* 판정을 어떻게 셌는지 그 자리에서 밝힌다. 면적 표만 보면
              조례 이격이 왜 조건부인지 알 수 없고, 지도 색도 설명되지 않는다. */}
          {r.ordinance_grandfathered && (
            <p className="scenario">
              이 검토에서는 <b>조례 이격을 배제가 아니라 조건부로 집계</b>했습니다 —
              허가일이 조례 시행일보다 앞서 부칙 경과조치가 적용될 수 있기 때문입니다.
              <em> 면제가 확정된 것은 아니며, 적용 여부는 관할 지자체가 판단합니다.</em>
            </p>
          )}
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
