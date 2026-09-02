import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import type { EnvLayer, LatLng, RoadDetail } from './types'

export type PickMode = 'point' | 'area' | 'layout' | 'parcel';

interface SitePickerProps {
  lat: number | null;
  lng: number | null;
  radiusM: number;
  onPick: (lat: number, lng: number) => void;
  /**
   * 'area'   클릭이 사업구역 꼭짓점을 찍는다
   * 'layout' 클릭이 발전기 위치를 순서대로 찍는다 (1호기부터)
   * 'parcel' 클릭이 그 자리의 **필지**를 고른다 (태양광)
   */
  mode?: PickMode;
  /** 구역 꼭짓점 또는 발전기 위치 [[lat, lng], …] */
  ring?: [number, number][];
  onRingChange?: (ring: [number, number][]) => void;
  /** 배치선 모드 — 발전기 검토반경 / 연결선 검토반경 (m) */
  turbineRadiusM?: number;
  corridorRadiusM?: number;
  /**
   * parcel 모드에서 확정된 필지들. ring(클릭 좌표)과 별개로 받는다 —
   * 조회가 비동기라, 클릭은 이미 찍혔는데 경계는 아직 없는 순간이 있다.
   */
  parcels?: { pnu: string; jibun: string; rings: [number, number][][];
              exact: boolean }[];
  /**
   * 스크리닝 채색 결과. 켜져 있을 때만 채워 준다.
   * 필지를 누르면 onScreenPick으로 대표좌표가 올라간다 → 정밀판정으로 잇는다.
   */
  screening?: { pnu: string; jibun: string; jimok: string; area_m2: number;
                grade: string; reasons: string[]; rings: [number, number][][];
                lat: number | null; lng: number | null }[] | null;
  onScreenPick?: (lat: number, lng: number) => void;
  /** 지도를 움직였을 때 현재 범위를 알린다 (스크리닝 갱신용) */
  onBoundsChange?: (b: [number, number, number, number], zoom: number) => void;
  /**
   * 이 값이 바뀌면 ring 전체가 보이도록 지도를 맞춘다.
   *
   * 저장한 사업지를 불러오면 좌표만 복원되고 지도는 전국 시점에 머물러,
   * 사용자가 매번 손으로 찾아 들어가야 했다. 값이 바뀔 때만 움직이므로
   * 지도를 옮기는 중에 시점이 되돌아가지 않는다.
   */
  fitToken?: number;
  /**
   * 주소 검색으로 옮겨 갈 자리. token이 바뀔 때만 움직인다.
   *
   * fitToken은 **찍어 둔 지점(ring)**에 맞추는 것이라 아직 아무것도 찍지
   * 않은 상태에서는 쓸 수 없다. 주소만 알고 지도에서 그 자리를 찾아가는
   * 것이 검색의 목적이므로 별도 손잡이를 둔다.
   */
  focus?: { lat: number; lng: number; token: number } | null;
  /**
   * 사업지가 속한 읍·면·동 경계. 검색 결과와 함께 받아 면으로 그린다.
   *
   * 주소를 점으로만 찍어 주면 부지가 행정구역 어디에 걸치는지 알 수 없다.
   * 경계를 깔아 두면 그 안에서 꼭짓점을 찍게 되어 사업지 지정이 정확해진다.
   */
  adminBoundary?: [number, number][][] | null;
  /** 경계 라벨 — '강원특별자치도 평창군 방림면' */
  adminLabel?: string;
  /** 검토 결과를 지도에 겹쳐 그릴 영역 */
  overlays?: { blocked: [number, number][][]; conditional: [number, number][][];
               free: [number, number][][];
               /** 조례 이격 범위 — 배제·조건부 면에 이미 포함. 윤곽만 덧그린다 */
               ordinance_house?: [number, number][][];
               ordinance_road?: [number, number][][];
               ordinance_road_uncertain?: [number, number][][] } | null;
  /**
   * 조례 이격을 배제가 아니라 **조건부**로 셌는가(경과규정 대상).
   *
   * 발전사업허가일이 조례 시행일보다 앞서면 부칙으로 종전 기준이 적용될 수
   * 있어 무조건 불가가 아니다. 색이 이미 조건부로 나오므로 지도에서는
   * 이격 윤곽의 안내 문구만 달라진다.
   */
  ordinanceGrandfathered?: boolean;
  /**
   * 겹침 면에 커서를 올렸을 때 보여 줄 문구. {overlay 열쇠: 한 줄}
   *
   * 붉은 면만 보면 **왜 배제인지** 알 수 없어 다음 행동이 서지 않는다.
   * 조례 이격이면 어느 도로에서 몇 m인지까지 적어 준다.
   */
  overlayLabels?: Record<string, string>;
  /**
   * 도로별 선과 이격 범위. **어느 도로로부터 얼마만큼 침범되는가**를
   * 지도에서 보기 위한 것이다 — 합친 붉은 면 하나로는 알 수 없다.
   */
  roadDetail?: RoadDetail[] | null;
  /**
   * 지도(L.Map)가 만들어지면 그 인스턴스를 올려준다.
   *
   * 보고서 캡처가 이 지도를 그대로 이미지로 떠야 화면과 문서가 어긋나지
   * 않는다. 캡처는 부모(WindSiteView)가 버튼을 눌렀을 때 하므로, 그 시점에
   * 지도 컨테이너에 접근할 손잡이가 필요하다.
   */
  onMapReady?: (map: L.Map) => void;
  /**
   * **사업구역 경계**(필지 기반, 검토 실행 후 서버가 확정한 도형).
   *
   * 그린 폴리곤(ring)과 다르다 — 그린 구역 안 도로·구거 같은 '대상 아님'
   * 필지를 뺀, 판정·면적·보고서 지도가 실제로 쓰는 도형이다. 화면이 이
   * 경계를 그려야 캡처(제약도)와 서버 렌더 지도의 사업구역이 같은 도형이
   * 된다. 검토 전에는 없다.
   */
  siteRings?: [number, number][][] | null;
  /** 환경성 평가 항목별 지도 미리보기 목록 (용도지역 구성·농업진흥지역도 등) */
  envLayers?: EnvLayer[];
  /**
   * 지금 화면에 낼 것 — null이면 평소대로 제약도(overlays)·필지 채색을
   * 그린다. 'zoning'이면 용도지역 구성 전체를, 그 밖의 값이면 그 이름과
   * 같은 envLayers 항목 하나만 단독으로 그린다.
   *
   * 보고서가 화면과 같은 그림을 캡처하려면, 화면이 "지금 이 항목만" 보여줄
   * 방법이 있어야 한다 — 평소의 제약도 위에 겹쳐 그리면 그 항목의 경계가
   * 다른 색에 묻혀 협의 자료로 못 쓴다.
   */
  activeEnvLayer?: string | null;
}

/**
 * 배치선을 radiusM만큼 부풀린 띠의 외곽선을 만든다 (실제 미터 기준).
 *
 * 발전기 반경은 L.circle이 미터로 그려주지만 연결선은 그런 게 없다. 폴리라인
 * 굵기는 화면 픽셀이라 확대·축소하면 실제 폭과 무관해진다 — 검토 폭이
 * 100m인지 1km인지 눈으로 가늠할 수 없다. 그래서 직접 만든다.
 *
 * 위경도는 각도라 그대로 오프셋할 수 없으므로, 선 중앙을 원점으로 하는
 * 국소 평면(미터)으로 옮겨 계산하고 되돌린다. 수 km 범위에서는 오차가
 * 1m 아래라 시각화 목적에 충분하다. 실제 판정용 버퍼는 서버가 EPSG:5179로
 * 따로 계산하므로 이 값은 화면 표시에만 쓴다.
 */
//: 호를 몇 도 단위로 쪼갤지. 각도가 작을수록 원에 가깝다.
//: 11.25°(8조각/반바퀴)면 실측 오차가 1.9%로 눈에 띄었다. L.circle과 비슷한
//: 매끄러움을 내려면 이 정도가 필요하다.
const ARC_STEP_RAD = Math.PI / 32;   // 5.625° → 이론 오차 0.12%

export function corridorRing(pts: [number, number][], radiusM: number): [number, number][] {
  if (pts.length < 2 || radiusM <= 0) return [];
  const lat0 = pts.reduce((s, p) => s + p[0], 0) / pts.length;
  const lng0 = pts.reduce((s, p) => s + p[1], 0) / pts.length;
  const mLat = 111_320;
  const mLng = 111_320 * Math.cos((lat0 * Math.PI) / 180);
  const toXY = ([a, o]: [number, number]) => [(o - lng0) * mLng, (a - lat0) * mLat];
  const toLL = ([x, y]: number[]): [number, number] =>
    [lat0 + y / mLat, lng0 + x / mLng];

  const P = pts.map(toXY);
  // 각 구간의 단위 법선. 길이 0인 구간(같은 자리 두 번 클릭)은 건너뛴다.
  const seg: { a: number[]; b: number[]; n: number[] }[] = [];
  for (let i = 0; i < P.length - 1; i++) {
    const dx = P[i + 1][0] - P[i][0];
    const dy = P[i + 1][1] - P[i][1];
    const len = Math.hypot(dx, dy);
    if (len < 1e-6) continue;
    seg.push({ a: P[i], b: P[i + 1], n: [-dy / len, dx / len] });
  }
  if (!seg.length) return [];

  // 호는 **부호 있는 스윕**으로 그린다. 목표 각도만 주고 최단 방향으로 돌게
  // 하면 정확히 반바퀴(π)일 때 방향이 정해지지 않는다. 끝단 캡이 그 경우인데,
  // 반대로 돌면 캡이 배치선을 가로질러 띠가 중심선까지 파고든다.
  const arcBy = (c: number[], from: number, sweep: number, out: number[][]) => {
    // 스윕 크기에 비례해 조각 수를 정한다. 고정 개수로 하면 반바퀴 캡이
    // 작은 꼭짓점 호와 같은 수로 쪼개져 눈에 띄게 각져 보인다.
    const steps = Math.max(2, Math.ceil(Math.abs(sweep) / ARC_STEP_RAD));
    for (let k = 1; k < steps; k++) {
      const t = from + (sweep * k) / steps;
      out.push([c[0] + radiusM * Math.cos(t), c[1] + radiusM * Math.sin(t)]);
    }
  };
  const ang = (v: number[]) => Math.atan2(v[1], v[0]);
  const turn = (from: number, to: number) => {
    let d = to - from;
    while (d > Math.PI) d -= 2 * Math.PI;
    while (d < -Math.PI) d += 2 * Math.PI;
    return d;
  };
  const off = (p: number[], n: number[], s: number) =>
    [p[0] + n[0] * radiusM * s, p[1] + n[1] * radiusM * s];

  // 왼쪽 offset을 정방향으로, 오른쪽을 역방향으로 이어 하나의 닫힌 고리를 만든다.
  // 꼭짓점에서는 양쪽 모두 회전각만큼 호를 넣는다. 안쪽에서 살짝 겹치지만
  // fillRule='nonzero'면 구멍이 생기지 않는다.
  const left: number[][] = [];
  const right: number[][] = [];
  seg.forEach((s, i) => {
    left.push(off(s.a, s.n, 1), off(s.b, s.n, 1));
    right.push(off(s.a, s.n, -1), off(s.b, s.n, -1));
    const nx = seg[i + 1];
    if (nx) {
      const d = turn(ang(s.n), ang(nx.n));
      arcBy(s.b, ang(s.n), d, left);
      arcBy(s.b, ang(s.n) + Math.PI, d, right);
    }
  });

  // 법선 n은 진행방향 u를 반시계로 90° 돌린 것이다. 따라서 캡이 진행방향
  // 바깥으로 부풀려면 두 캡 모두 시계방향(-π)으로 돌아야 한다.
  const last = seg[seg.length - 1];
  const first = seg[0];
  const ring: number[][] = [...left];
  arcBy(last.b, ang(last.n), -Math.PI, ring);                    // 끝 캡
  ring.push(...right.reverse());
  arcBy(first.a, ang(first.n) + Math.PI, -Math.PI, ring);        // 시작 캡
  return ring.map(toLL);
}

/**
 * 스크리닝 채색 — 판정 4단계를 그대로 따른다.
 * 배제는 **붉게** 칠한다. 종전에는 검은 음영으로 '걷어 낸다'는 뜻을 주려
 * 했는데, 같은 지도 위 제약도 겹침(blocked)이 붉은색이라 **같은 '배제'가 두
 * 색으로 보였다.** 범례의 네모도 검정이어서 지도의 빨간 면이 무엇인지 알 수
 * 없었다. 뜻이 같으면 색도 같아야 한다.
 */
const SCREEN_STYLE: Record<string, L.PathOptions> = {
  POSSIBLE: { color: '#52c41a', weight: 1, fillColor: '#52c41a', fillOpacity: 0.45 },
  CONDITIONAL: { color: '#faad14', weight: 1, fillColor: '#faad14', fillOpacity: 0.4 },
  IMPOSSIBLE: { color: '#ff4d4f', weight: 1, fillColor: '#ff4d4f', fillOpacity: 0.45 },
  UNKNOWN: { color: '#40a9ff', weight: 1, fillColor: '#40a9ff', fillOpacity: 0.3 },
  // 대상 아님 — 후보가 아니므로 경계만 희미하게 남긴다. 지우지는 않는다.
  NOT_APPLICABLE: { color: '#8c8c8c', weight: 0.5, fill: false, opacity: 0.35 },
};

/**
 * 겹침 면 기본 설명. 조례 이격처럼 서버가 사유를 만들어 주는 것은
 * `overlayLabels`가 덮어쓴다.
 */
const OVERLAY_TIP: Record<string, string> = {
  blocked: '배제 — 불가 판정 레이어 또는 조례 이격 범위',
  conditional: '조건부 — 협의·저감 조건 하에 진행 가능',
  free: '제약 없음 — 조회된 규제 레이어에 걸리지 않음',
  ordinance_house: '조례 주거 이격',
  ordinance_road: '조례 도로 이격 (국도·지방도)',
  ordinance_road_uncertain: '조례 도로 이격 (시·군도 — 군도 여부 확인 필요)',
};

/**
 * 도로 이격 표시 색.
 *
 * ⚠️ **채움 색과 겹치면 안 된다.** 지도의 면은 초록(제약없음)·주황(조건부)·
 *    빨강(배제)이 이미 차지하고 있다. 종전에 도로를 호박색(#ffd166)으로
 *    그렸더니 주황 조건부 면과 붙어 무엇이 도로인지 구분되지 않았다.
 *    그래서 **면에 쓰지 않는 찬 색**으로 가른다.
 *
 *      배제 도로(국도·지방도)   시안   — 거리를 바꿀 수 없어 부지를 옮겨야 한다
 *      조건부 도로(시·군도)     자홍   — 군도 노선 여부부터 확인해야 한다
 *
 * 선은 굵게 실선, 이격 범위는 같은 색 파선으로 둬 **원인과 결과가 한 색으로
 * 묶이게** 한다. 배제는 촘촘한 파선, 조건부는 성긴 점선으로 또 한 번 가른다.
 */
const ROAD_COLOR = { blocked: '#00e5ff', uncertain: '#ff5cf0' } as const;
//: 지도에 이름표를 다는 도로 수. 다 달면 글자가 겹쳐 아무것도 안 읽힌다.
const ROAD_LABEL_MAX = 4;

//: "지도 보기"에서 용도지역 구성을 고르면 쓰는 고정 키. 서버(area_report.py
//: ENV_ZONING_KEY)와 같은 값이어야 캡처 이미지가 짝을 찾는다.
export const ENV_ZONING_KEY = 'zoning';
//: 용도지역 4분류 색 — 서버(maps.py ZONING_COLORS)와 같은 값.
const ZONING_COLORS: Record<string, string> = {
  '도시지역': '#4a6fa5', '관리지역': '#c9a227',
  '농림지역': '#4caf50', '자연환경보전지역': '#2e7d32',
};
const ZONING_DEFAULT = '#8a8f98';
//: 개별 항목 색 — 서버(area_report.py _ENV_MAP_COLOR)와 같은 값.
const ENV_ITEM_COLOR: Record<string, string> = {
  IMPOSSIBLE: '#d9363e', CONDITIONAL: '#e8a33d',
};

const ROAD_LINE: Record<string, L.PathOptions> = {
  blocked: { color: ROAD_COLOR.blocked, weight: 3.5, opacity: 1 },
  uncertain: { color: ROAD_COLOR.uncertain, weight: 3, opacity: 0.95,
               dashArray: '10 5' },
};
//: 선이 배경에 묻히지 않도록 어두운 후광을 깐다.
const ROAD_LINE_HALO: L.PathOptions = {
  color: '#08121c', weight: 7, opacity: 0.55, interactive: false,
};
/** 도로별 이격 범위 — 면은 이미 배제·조건부가 칠했으므로 **윤곽만** 얹는다. */
const ROAD_ZONE: Record<string, L.PathOptions> = {
  blocked: { color: ROAD_COLOR.blocked, weight: 1.8, dashArray: '8 5',
             fill: false, opacity: 0.9 },
  uncertain: { color: ROAD_COLOR.uncertain, weight: 1.6, dashArray: '2 6',
               fill: false, opacity: 0.85 },
};

/**
 * 서버가 준 한 줄을 툴팁 HTML로 바꾼다.
 *
 *   `배제 — 국도 1,000m … 조례⏎(장흥대로, …)`
 *   → `<b>배제</b><br>국도 1,000m … 조례<br>(장흥대로, …)`
 *
 * 등급을 굵게 떼고 줄바꿈을 살린다. 줄바꿈 문자를 그대로 두면 HTML에서
 * 공백이 되어 한 줄로 흘러 읽히지 않는다.
 */
function fmtTip(text: string): string {
  const br = (t: string) => t.split('\n').join('<br>');
  const [head, ...rest] = text.split(' — ');
  return rest.length
    ? `<b>${head}</b><br>${br(rest.join(' — '))}`
    : br(text);
}

/**
 * 도로 이름표를 놓을 자리 — 가장 긴 선분의 중간점.
 *
 * 선이 없는 도로(구간이 짧아 line이 비는 경우)는 이격 범위 윤곽의 첫 점으로
 * 대신한다 — 위치가 완벽하지 않아도 "이 부근"이라는 뜻은 전달된다.
 */
function roadLabelPoint(d: RoadDetail): LatLng | null {
  const longest = [...d.line].sort((a, b) => b.length - a.length)[0];
  if (longest && longest.length) return longest[Math.floor(longest.length / 2)];
  const ring = d.rings[0];
  return ring && ring.length ? ring[0] : null;
}

const SCREEN_LABEL: Record<string, string> = {
  POSSIBLE: '가능', CONDITIONAL: '조건부', IMPOSSIBLE: '배제', UNKNOWN: '미확인',
  NOT_APPLICABLE: '대상 아님',
};

/** 제약 등급별 표시색 — 배경이 위성영상이라 채도를 높이고 투명도를 낮춘다 */
const OVERLAY_STYLE: Record<string, L.PathOptions> = {
  blocked: { color: '#ff4d4f', weight: 1, fillColor: '#ff4d4f', fillOpacity: 0.42 },
  conditional: { color: '#faad14', weight: 1, fillColor: '#faad14', fillOpacity: 0.3 },
  free: { color: '#52c41a', weight: 1, fillColor: '#52c41a', fillOpacity: 0.28 },
  // 조례 주거 이격 — **면은 칠하지 않는다.** 배제(붉은 면)에 이미 들어 있어
  // 겹쳐 칠하면 색만 진해질 뿐 무엇 때문에 불가인지는 여전히 알 수 없다.
  // 파선 윤곽으로 짚어 주면 '규제 레이어 때문'과 '조례 이격 때문'이 갈린다 —
  // 앞은 부지를 옮겨야 하고 뒤는 이격을 확보하면 되므로 다음 행동이 다르다.
  ordinance_house: { color: '#9b51e0', weight: 2, dashArray: '6 4',
                     fill: false, interactive: false },
  // ⚠️ 도로 이격은 **합집합 윤곽을 그리지 않는다.** 도로별(roadDetail)로
  //    같은 경계를 이미 그리고 있어 두 번 겹쳐 그려지고, 그쪽은 도로 이름과
  //    침범 면적까지 말해 주므로 합집합은 정보를 더하지 않고 지도만 어지럽힌다.
};

/**
 * 실제 배경지도 위에서 사업지를 클릭으로 지정한다.
 *
 * 종전에는 시·도 경계만 그린 전국 SVG라 클릭 정밀도가 km 단위였고 지형을 볼 수 없었다.
 * 풍력 입지는 능선·표고·사면향이 곧 사업성이라 등고선이 보이지 않으면 지도의 의미가 없다.
 * 그래서 슬리피 맵(Leaflet)으로 바꾸고 지형도를 기본 배경으로 둔다.
 *
 * 배경지도 (목록 순서대로)
 *   · 위성영상     V-World Satellite — 기본 배경
 *   · 일반지도     V-World Base (국토지리정보원 수치지도, 한글 지명)
 *   · 일반지도     OSM
 *   · 지형·등고선  OpenTopoMap (등고선 + 음영기복)
 *   · 지명·경계    V-World Hybrid — 배경 위에 겹치는 오버레이, 기본 켜짐
 *
 * 위성영상만으로는 지명을 읽을 수 없어 사업지를 찾기 어렵다. 그래서 지명·경계
 * 오버레이를 기본으로 켜 둔다. 어느 배경 위에서도 투명 배경으로 겹쳐진다.
 *
 * V-World 타일은 VITE_VWORLD_KEY가 있을 때만 등록한다. 키가 없어도
 * OpenTopoMap·OSM으로 지도는 그대로 뜬다(판정 기능과 무관).
 */

const VWORLD_KEY: string = import.meta.env.VITE_VWORLD_KEY || '';
const vw = (layer: string, ext: string) =>
  `https://api.vworld.kr/req/wmts/1.0.0/${VWORLD_KEY}/${layer}/{z}/{y}/{x}.${ext}`;

/** 전국이 한눈에 들어오는 초기 시점 */
const KR_CENTER: L.LatLngTuple = [36.35, 127.9];
const KR_ZOOM = 7;
/** 지점을 찍었을 때 들어가는 배율 — 능선이 판별되는 수준 */
const SITE_ZOOM = 14;

/**
 * 저장한 사업지로 날아가는 시간(초).
 *
 * 짧으면 순간이동처럼 보여 어디로 갔는지 놓치고, 길면 기다리게 된다.
 * 1.6초면 전국 시점에서 읍면 단위까지 경로가 읽히면서 답답하지 않다.
 */
const FLY_SECONDS = 1.6;

export default function SitePicker({
  lat, lng, radiusM, onPick,
  mode = 'point', ring, onRingChange, overlays, overlayLabels, roadDetail,
  siteRings,
  turbineRadiusM = 500, corridorRadiusM = 100, parcels,
  screening, onScreenPick, onBoundsChange, fitToken, onMapReady,
  envLayers, activeEnvLayer, focus, adminBoundary, adminLabel,
}: SitePickerProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const markerRef = useRef<L.Marker | null>(null);
  const circleRef = useRef<L.Circle | null>(null);
  const drawRef = useRef<L.LayerGroup | null>(null);
  const overlayRef = useRef<L.LayerGroup | null>(null);
  const screenRef = useRef<L.LayerGroup | null>(null);
  const envLayerRef = useRef<L.LayerGroup | null>(null);
  const adminRef = useRef<L.LayerGroup | null>(null);
  const onBoundsRef = useRef(onBoundsChange);
  const onScreenPickRef = useRef(onScreenPick);
  const onMapReadyRef = useRef(onMapReady);
  onBoundsRef.current = onBoundsChange;
  onScreenPickRef.current = onScreenPick;
  onMapReadyRef.current = onMapReady;
  /** 지도 클릭으로 방금 바꾼 좌표 — 외부 입력과 구분해 불필요한 화면 이동을 막는다 */
  const selfSetRef = useRef<string>('');
  /**
   * 클릭 핸들러는 지도 생성 시 한 번만 등록된다. 그 시점의 mode·ring을
   * 클로저에 가두면 모드를 바꿔도 계속 옛 값을 본다. ref로 최신값을 읽는다.
   */
  const modeRef = useRef(mode);
  const ringRef = useRef<[number, number][]>(ring ?? []);
  const onRingChangeRef = useRef(onRingChange);
  modeRef.current = mode;
  ringRef.current = ring ?? [];
  onRingChangeRef.current = onRingChange;

  const [hover, setHover] = useState<{ lat: number; lng: number } | null>(null);
  const [zoom, setZoom] = useState(KR_ZOOM);

  // ── 지도 생성 (최초 1회) ────────────────────────────────────────────
  useEffect(() => {
    if (!hostRef.current || mapRef.current) return;

    const topo = L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
      subdomains: 'abc', maxZoom: 19, maxNativeZoom: 17,
      attribution: '© OpenTopoMap (CC-BY-SA) · © OpenStreetMap contributors',
    });
    const osm = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, attribution: '© OpenStreetMap contributors',
    });
    // 목록에 넣는 순서가 곧 화면에 보이는 순서다 — 위성영상을 맨 위, 지형을 맨 아래에 둔다.
    const bases: Record<string, L.TileLayer> = {};
    const tileOverlays: Record<string, L.TileLayer> = {};
    let satellite: L.TileLayer | null = null;
    let hybrid: L.TileLayer | null = null;

    if (VWORLD_KEY) {
      satellite = L.tileLayer(vw('Satellite', 'jpeg'), {
        maxZoom: 19, maxNativeZoom: 18, attribution: '© 국토교통부 V-World',
      });
      bases['위성영상 (V-World)'] = satellite;
      bases['일반지도 (V-World)'] = L.tileLayer(vw('Base', 'png'), {
        maxZoom: 19, attribution: '© 국토교통부 V-World',
      });
    }
    bases['일반지도 (OSM)'] = osm;
    bases['지형·등고선'] = topo;

    if (VWORLD_KEY) {
      hybrid = L.tileLayer(vw('Hybrid', 'png'), {
        maxZoom: 19, opacity: 0.9, attribution: '© 국토교통부 V-World',
      });
      tileOverlays['지명·경계 표기'] = hybrid;
    }

    // 키가 없으면 위성 타일 자체가 없다. 그때는 지형도로 내려앉아야 지도가 빈 화면이 되지 않는다.
    const initialBase = satellite ?? topo;

    const map = L.map(hostRef.current, {
      center: lat != null && lng != null ? [lat, lng] : KR_CENTER,
      zoom: lat != null && lng != null ? SITE_ZOOM : KR_ZOOM,
      layers: hybrid ? [initialBase, hybrid] : [initialBase],
      zoomControl: true,
      minZoom: 6,
      maxZoom: 19,
      // 스크리닝은 폴리곤을 수천 개 그린다. Leaflet 기본(SVG)은 도형마다
      // DOM 노드를 만들어 수천 개에서 이미 버벅이고 수만 개면 멈춘다.
      // canvas는 한 장에 그려 그 한계가 없다. 툴팁·클릭은 그대로 동작한다.
      preferCanvas: true,
    });

    L.control.layers(bases, tileOverlays, { position: 'topright', collapsed: false }).addTo(map);
    L.control.scale({ metric: true, imperial: false, position: 'bottomleft' }).addTo(map);

    map.on('click', (e: L.LeafletMouseEvent) => {
      const a = Number(e.latlng.lat.toFixed(5));
      const o = Number(e.latlng.lng.toFixed(5));
      if (modeRef.current !== 'point') {
        onRingChangeRef.current?.([...ringRef.current, [a, o]]);
        return;
      }
      selfSetRef.current = `${a},${o}`;
      onPick(a, o);
    });
    map.on('mousemove', (e: L.LeafletMouseEvent) =>
      setHover({ lat: e.latlng.lat, lng: e.latlng.lng }));
    map.on('mouseout', () => setHover(null));
    map.on('zoomend', () => setZoom(map.getZoom()));
    // 스크리닝은 '보이는 범위'가 곧 조회 범위다. 이동·확대가 끝난 뒤에만
    // 알린다 — 드래그 중에 매 프레임 알리면 조회가 폭주한다.
    const notify = () => {
      const b = map.getBounds();
      onBoundsRef.current?.(
        [b.getSouth(), b.getWest(), b.getNorth(), b.getEast()], map.getZoom());
    };
    map.on('moveend', notify);
    map.on('zoomend', notify);

    mapRef.current = map;
    onMapReadyRef.current?.(map);

    // 카드 폭이 확정된 뒤에야 타일 크기가 맞는다 — 컨테이너 크기 변화를 따라간다
    const ro = new ResizeObserver(() => map.invalidateSize());
    ro.observe(hostRef.current);

    return () => {
      ro.disconnect();
      map.remove();
      mapRef.current = null;
      // 지도와 함께 사라진 레이어의 참조를 남기면 안 된다.
      // StrictMode는 개발 중 effect를 두 번 돌리는데, 참조가 남아 있으면
      // 두 번째 실행에서 '이미 있다'고 판단해 새 지도에 마커를 다시 붙이지 않는다.
      markerRef.current = null;
      circleRef.current = null;
      drawRef.current = null;
      overlayRef.current = null;
      screenRef.current = null;
    };
    // 최초 1회만 생성한다. onPick은 setState만 쓰므로 클로저가 낡아도 안전하다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── 키보드로 마지막 지점 취소 ───────────────────────────────────────
  useEffect(() => {
    if (mode === 'point') return;
    const onKey = (e: KeyboardEvent) => {
      // 주소·반경 입력란에서 Esc를 눌렀을 때까지 가로채면 안 된다
      const el = e.target as HTMLElement | null;
      const tag = el?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || el?.isContentEditable) return;
      const undo = e.key === 'Escape'
        || ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z');
      if (!undo) return;
      const cur = ringRef.current;
      if (!cur.length) return;
      e.preventDefault();
      onRingChangeRef.current?.(cur.slice(0, -1));
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [mode]);

  // ── 사업구역 꼭짓점·미리보기 ─────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    drawRef.current?.remove();
    drawRef.current = null;
    if (mode === 'point' || !ring?.length) return;

    const g = L.layerGroup().addTo(map);

    if (mode === 'parcel') {
      // 확정된 필지는 경계째 칠한다. 경계 근처를 눌러 인접 필지로 대신
      // 고른 건(exact=false)은 점선으로 달리 그려, 확정과 구분되게 한다.
      (parcels ?? []).forEach(p => {
        p.rings.forEach(r => {
          if (r.length < 4) return;
          g.addLayer(L.polygon(r, {
            color: p.exact ? '#39d3e6' : '#faad14',
            weight: 2,
            dashArray: p.exact ? undefined : '5 4',
            fillColor: p.exact ? '#39d3e6' : '#faad14',
            fillOpacity: 0.18,
          }).bindTooltip(
            `${p.jibun || p.pnu}${p.exact ? '' : ' (경계 인접 — 확인 필요)'}`,
            { direction: 'top' }));
        });
      });
      // 클릭 지점은 항상 보여준다. 조회 중이거나 필지를 못 찾은 자리도
      // 표시돼야 어디를 눌렀는지 알고 되돌릴 수 있다.
      ring.forEach(([a, o], i) => {
        const v = L.circleMarker([a, o], {
          radius: 5, color: '#39d3e6', weight: 2,
          fillColor: '#fff', fillOpacity: 1, bubblingMouseEvents: false,
        });
        v.bindTooltip(`필지 ${i + 1} — 클릭하면 제외`, { direction: 'top' });
        v.on('click', (ev) => {
          L.DomEvent.stopPropagation(ev);
          const cur = ringRef.current;
          onRingChangeRef.current?.(cur.filter((_, k) => k !== i));
        });
        g.addLayer(v);
      });
      drawRef.current = g;
      return;
    }

    if (mode === 'layout') {
      // 발전기를 찍은 순서대로 잇는다. 그 선이 집전선로·진입도로 경로가 된다.
      if (ring.length >= 2) {
        // 검토 폭을 실제 미터로 그린다. 발전기 반경과 같은 기준이라야
        // 둘의 크기를 눈으로 비교할 수 있다.
        const band = corridorRing(ring, corridorRadiusM);
        if (band.length >= 4) {
          g.addLayer(L.polygon(band, {
            color: '#39d3e6', weight: 1, opacity: 0.7,
            fillColor: '#39d3e6', fillOpacity: 0.12,
            // 꼭짓점 라운드 조인이 안쪽에서 겹치므로 nonzero여야 구멍이 없다
            fillRule: 'nonzero',
          }));
        }
        g.addLayer(L.polyline(ring, { color: '#39d3e6', weight: 2 }));
      }
      ring.forEach(([a, o], i) => {
        // 발전기 검토반경은 화면 배율과 무관하게 '실제 미터'로 그린다
        g.addLayer(L.circle([a, o], {
          radius: turbineRadiusM, color: '#39d3e6', weight: 1,
          fillColor: '#39d3e6', fillOpacity: 0.1,
        }));
        const mk = L.marker([a, o], {
          icon: L.divIcon({
            className: 'ws-turbine',
            html: `<span>${i + 1}</span>`,
            iconSize: [22, 22], iconAnchor: [11, 11],
          }),
          keyboard: false,
          title: `${i + 1}호기 — 클릭하면 삭제`,
        });
        // 잘못 찍은 지점을 지우려고 전부 되돌릴 필요가 없게 한다.
        // stopPropagation을 하지 않으면 지도 클릭도 함께 발생해 방금 지운
        // 자리에 새 지점이 다시 찍힌다.
        mk.on('click', (ev) => {
          L.DomEvent.stopPropagation(ev);
          const cur = ringRef.current;
          onRingChangeRef.current?.(cur.filter((_, k) => k !== i));
        });
        g.addLayer(mk);
      });
      drawRef.current = g;
      return;
    }

    // 3점 미만이면 아직 면이 아니므로 선으로 보여준다.
    if (ring.length >= 3) {
      g.addLayer(L.polygon(ring, {
        color: '#39d3e6', weight: 2, dashArray: '6 4',
        fillColor: '#39d3e6', fillOpacity: 0.1,
      }));
    } else {
      g.addLayer(L.polyline(ring, { color: '#39d3e6', weight: 2, dashArray: '6 4' }));
    }
    // 꼭짓점을 보이게 해야 어디를 찍었는지 알고 되돌릴 수 있다.
    ring.forEach(([a, o], i) => {
      const v = L.circleMarker([a, o], {
        radius: 5, color: '#39d3e6', weight: 2,
        fillColor: i === ring.length - 1 ? '#fff' : '#39d3e6', fillOpacity: 1,
        // 반경 5px은 누르기 좁다. 클릭 판정만 넓혀 준다.
        bubblingMouseEvents: false,
      });
      v.bindTooltip(`꼭짓점 ${i + 1} — 클릭하면 삭제`, { direction: 'top' });
      v.on('click', (ev) => {
        L.DomEvent.stopPropagation(ev);
        const cur = ringRef.current;
        onRingChangeRef.current?.(cur.filter((_, k) => k !== i));
      });
      g.addLayer(v);
    });
    drawRef.current = g;
  }, [mode, ring, turbineRadiusM, corridorRadiusM, parcels]);

  // ── 불러온 사업지로 날아가기 ────────────────────────────────────────
  //
  // 시점을 툭 바꾸지 않고 **이동하면서 확대**한다. 전국 시점에서 필지 하나로
  // 순간이동하면 지금 보는 곳이 어디인지 놓치는데, 날아가는 동안 경로가
  // 보이면 사업지가 국토 어디쯤인지가 함께 읽힌다.
  //
  // Leaflet의 flyTo는 먼 거리를 한 번 줌아웃했다가 다시 들어가는 곡선을
  // 그린다. 거리가 멀수록 그 호가 커져, 전국 → 읍면 이동이 자연스럽다.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !fitToken || !ring?.length) return;

    // 동작 줄이기를 켠 사용자에게는 움직이지 않고 바로 맞춘다.
    // 애니메이션이 어지럼을 유발할 수 있어 OS 설정을 따른다.
    const still = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

    if (ring.length === 1) {
      const z = Math.max(map.getZoom(), SITE_ZOOM);
      if (still) map.setView(ring[0], z);
      else map.flyTo(ring[0], z, { duration: FLY_SECONDS });
      return;
    }
    // 여백을 둬 경계가 화면 끝에 붙지 않게 한다. maxZoom을 걸어 두는 이유는
    // 아주 작은 구역에서 최대 배율까지 파고들면 주변이 안 보이기 때문이다.
    const opts = { padding: [40, 40] as [number, number], maxZoom: 17 };
    const b = L.latLngBounds(ring);
    if (still) map.fitBounds(b, opts);
    else map.flyToBounds(b, { ...opts, duration: FLY_SECONDS });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitToken]);

  // ── 주소 검색 → 그 자리로 이동 ──────────────────────────────────────
  // 경계를 함께 받았으면 경계 전체가 보이도록 맞추고, 아니면 좌표로 날아간다.
  // 경계에 맞추는 쪽이 낫다 — 사업지가 그 안 어디쯤인지 가늠하려면 면 전체가
  // 보여야 하고, 좌표만 확대하면 어느 면인지 알 수 없다.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !focus) return;
    const still = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const ring0 = adminBoundary?.[0];
    if (ring0 && ring0.length > 2) {
      const opts = { padding: [30, 30] as [number, number], maxZoom: 15 };
      const b = L.latLngBounds(ring0);
      if (still) map.fitBounds(b, opts);
      else map.flyToBounds(b, { ...opts, duration: FLY_SECONDS });
      return;
    }
    const z = Math.max(map.getZoom(), SITE_ZOOM);
    if (still) map.setView([focus.lat, focus.lng], z);
    else map.flyTo([focus.lat, focus.lng], z, { duration: FLY_SECONDS });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus?.token]);

  // ── 행정구역(읍·면·동) 경계 ─────────────────────────────────────────
  // 채움을 아주 옅게만 준다. 이 면은 **판정 결과가 아니라 길잡이**라,
  // 제약도 색(초록·주황·빨강)과 경쟁하면 안 된다.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    adminRef.current?.remove();
    adminRef.current = null;
    if (!adminBoundary?.length) return;

    const g = L.layerGroup().addTo(map);
    adminBoundary.forEach(r => {
      if (r.length < 4) return;
      g.addLayer(L.polygon(r, {
        color: '#2f80ed', weight: 2, dashArray: '8 4',
        fillColor: '#2f80ed', fillOpacity: 0.06, interactive: false,
      }));
    });
    if (adminLabel) {
      const b = L.latLngBounds(adminBoundary.flat());
      g.addLayer(L.marker(b.getCenter(), {
        icon: L.divIcon({ className: 'ws-admin-label',
                          html: `<span>${adminLabel}</span>` }),
        interactive: false, keyboard: false,
      }));
    }
    adminRef.current = g;
  }, [adminBoundary, adminLabel]);

  // ── 스크리닝 채색 ───────────────────────────────────────────────────
  //
  // 미확인은 색만 다르게 하지 않고 **빗금**을 넣는다. 색은 범례를 봐야 뜻이
  // 통하는데, 빗금은 보자마자 '이건 다른 것'으로 읽힌다. 조회 안 된 필지가
  // 가능으로 오독되는 것을 막는 마지막 장치다(기획서 §3.4).
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    screenRef.current?.remove();
    screenRef.current = null;
    // 환경성 항목 지도(용도지역 등)를 보는 중에는 필지 채색을 감춘다 —
    // 다른 항목 색이 섞이면 그 항목 하나만 짚어 보여주는 지도가 안 된다.
    if (!screening?.length || activeEnvLayer) return;

    const g = L.layerGroup().addTo(map);
    // 배제 → 조건부 → 가능 순으로 깔아 엄한 쪽이 위로 오게 한다.
    const order: Record<string, number> = {
      NOT_APPLICABLE: 0, POSSIBLE: 1, CONDITIONAL: 2, UNKNOWN: 3, IMPOSSIBLE: 4 };
    [...screening].sort((a, b) => (order[a.grade] ?? 0) - (order[b.grade] ?? 0))
      .forEach(p => {
        p.rings.forEach(r => {
          if (r.length < 4) return;
          const poly = L.polygon(r, {
            ...SCREEN_STYLE[p.grade],
            className: p.grade === 'UNKNOWN' ? 'ws-screen-unknown' : undefined,
          });
          // 사유를 한 줄로 붙이지 않고 목록으로 편다. 조건부는 색이 하나라
          // **무엇 때문인지**가 여기서만 갈린다 — 조례 이격만인지,
          // 농업진흥지역까지인지에 따라 다음 행동이 다르다.
          // 등급을 **맨 앞**에 굵게 둔다. 지번부터 읽으면 결론이 셋째 줄에
          // 묻힌다. 사유는 줄로 나눠 하나씩 세운다.
          poly.bindTooltip(
            `<b>${SCREEN_LABEL[p.grade] ?? p.grade}</b>`
            + ` <span style="opacity:.75">${p.jibun || p.pnu} · ${p.jimok}`
            + ` · ${Math.round(p.area_m2).toLocaleString()}㎡</span>`
            + (p.reasons.length
                ? '<br>· ' + p.reasons.join('<br>· ')
                : '<br><span style="opacity:.7">걸리는 제약 없음</span>'),
            { direction: 'top' });
          // 후보가 아닌 필지는 눌러도 검토 대상으로 담지 않는다.
          // 사유는 툴팁으로 볼 수 있으므로 정보가 사라지지는 않는다.
          if (p.grade !== 'NOT_APPLICABLE') {
            poly.on('click', (ev) => {
              L.DomEvent.stopPropagation(ev);
              if (p.lat != null && p.lng != null) onScreenPickRef.current?.(p.lat, p.lng);
            });
          }
          g.addLayer(poly);
        });
      });
    // ⚠️ 도로 이격(overlays 이펙트)의 fill:false 폴리곤도 내부 전체가
    // 클릭·호버를 가로챈다(위 ROAD_ZONE 주석 참고). 두 이펙트는 갱신 시점이
    // 서로 달라 어느 쪽이 나중에 그려질지(=위로 올지) 실행 순서에 좌우된다
    // — 그 결과 필지가 실제로는 농업진흥지역 때문에 조건부인데도, 마침
    // 그 자리가 도로 이격 버퍼 안이면 도로 사유만 뜨고 진짜 사유(농업진흥
    // 지역 등)는 안 보이는 문제가 있었다(실측). 필지 채색이 **모든** 사유를
    // 모아 보여주는 더 정확한 답이므로, 그리는 순서와 무관하게 항상 맨
    // 위로 끌어올려 도로 이격 레이어가 필지를 가리지 못하게 한다.
    g.eachLayer((l) => { if (l instanceof L.Polygon) l.bringToFront(); });
    screenRef.current = g;
  }, [screening, activeEnvLayer]);

  // ── 검토 결과 겹쳐 그리기 ───────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    overlayRef.current?.remove();
    overlayRef.current = null;
    // 환경성 항목 지도를 보는 중에는 평소 제약도(배제·조건부·도로 등)를
    // 감춘다 — 같이 그리면 그 항목 하나만 짚어 보여주는 지도가 안 된다.
    if (!overlays || activeEnvLayer) return;

    const g = L.layerGroup().addTo(map);
    // 배제 → 조건부 → 제약없음 순으로 겹친다. 강한 제약이 위로 올라와야
    // 겹치는 지점에서 더 엄한 쪽이 보인다.
    (['free', 'conditional', 'blocked', 'ordinance_house'] as const).forEach((k) => {
      const tip = overlayLabels?.[k] || OVERLAY_TIP[k];
      (overlays[k] || []).forEach((r) => {
        if (r.length < 4) return;
        const poly = L.polygon(r, OVERLAY_STYLE[k]);
        // 이격 윤곽(fill:false)은 커서가 닿지 않으므로 면에만 붙는다.
        // 등급('배제'/'조건부')과 사유를 줄로 나눈다 — 한 줄로 흐르면
        // 무엇이 결론이고 무엇이 근거인지 구분되지 않는다.
        if (tip) poly.bindTooltip(fmtTip(tip), { direction: 'top', sticky: true });
        g.addLayer(poly);
      });
    });
    // ── 도로별 이격 — 어느 도로로부터 얼마만큼 ────────────────────
    // 배제 면 위에 **기준이 된 도로 선**과 그 도로의 이격 범위 윤곽을 얹는다.
    // 도로를 옮길 수는 없으니, 어느 도로가 원인인지 알아야 배치를 어디로
    // 물릴지 정해진다.
    (roadDetail || []).forEach((d) => {
      // 툴팁 제목도 선과 같은 색으로 둔다 — 어느 선을 가리키는지가 색으로
      // 바로 이어져야 지도와 글이 따로 놀지 않는다.
      const c = d.blocked ? ROAD_COLOR.blocked : ROAD_COLOR.uncertain;
      const tip = `<b style="color:${c}">${d.name}</b> · ${d.rank}<br>`
        + `이격 ${d.distance_m.toLocaleString()}m`
        + ` — ${d.blocked ? '배제' : '조건부'}`
        + ` <b>${(d.area_m2 / 1e4).toFixed(1)} ha</b> 침범`
        + (d.blocked ? '' : '<br>※ 군도 노선 여부 확인 필요');
      const kind = d.blocked ? 'blocked' : 'uncertain';
      d.rings.forEach((r) => {
        if (r.length < 4) return;
        // ⚠️ 툴팁을 **반드시** 붙인다. Leaflet은 `fill:false` 폴리곤도
        //    _containsPoint가 내부 전체를 참으로 보아 **이벤트를 가로챈다.**
        //    윤곽만 그린다고 그냥 두면 이 도형이 아래 배제 면을 덮어,
        //    붉은 면에 커서를 올려도 아무 문구가 뜨지 않는다(실측).
        //    여기가 오히려 더 정확한 답이다 — 어느 도로의 이격인지까지 말한다.
        g.addLayer(L.polygon(r, ROAD_ZONE[kind])
          .bindTooltip(tip, { direction: 'top', sticky: true }));
      });
      d.line.forEach((ln) => {
        if (ln.length < 2) return;
        g.addLayer(L.polyline(ln, ROAD_LINE_HALO));
        g.addLayer(L.polyline(ln, ROAD_LINE[kind])
          .bindTooltip(tip, { direction: 'top', sticky: true }));
      });
    });
    // ── 도로 이름표 — 항상 보이게 ────────────────────────────────────
    // 툴팁은 마우스를 올려야 뜬다. 보고서 캡처는 화면을 정지 이미지로
    // 뜨므로, 호버 없이도 "무슨 도로가 몇 m 이격인가"가 찍혀 있어야
    // 지도만 보고도 원인을 알 수 있다. 다 달면 글자가 겹치므로 침범
    // 면적이 큰 도로부터 몇 개만 단다.
    [...(roadDetail || [])]
      .sort((a, b) => b.area_m2 - a.area_m2)
      .slice(0, ROAD_LABEL_MAX)
      .forEach((d) => {
        const pt = roadLabelPoint(d);
        if (!pt) return;
        const c = d.blocked ? ROAD_COLOR.blocked : ROAD_COLOR.uncertain;
        g.addLayer(L.marker(pt, {
          icon: L.divIcon({
            className: 'ws-road-label',
            html: `<span style="border-color:${c};color:${c}">`
                + `${d.name} 이격 ${d.distance_m.toLocaleString()}m</span>`,
          }),
          interactive: false,
          keyboard: false,
        }));
      });

    // ── 사업구역 경계(필지 기반) — 맨 위에 ─────────────────────────
    // 판정·보고서가 실제로 쓰는 도형이다. 그린 폴리곤과 어디가 다른지
    // (도로·구거가 빠진 자리) 화면에서 보여야, 보고서의 190.5ha와 화면의
    // 216.9ha가 서로 다른 문서처럼 읽히지 않는다.
    (siteRings || []).forEach((r) => {
      if (r.length < 4) return;
      g.addLayer(L.polygon(r, {
        color: '#d0021b', weight: 2, dashArray: '6 4',
        fill: false, interactive: false,
      }));
    });

    // 이 이펙트가 필지 채색(screening) 이펙트보다 나중에 실행되면 도로 이격
    // 등 이 레이어들이 다시 위로 올라와 필지의 종합 사유를 가릴 수 있다.
    // 실행 순서와 무관하게 필지 채색이 항상 위에 오도록 다시 끌어올린다.
    screenRef.current?.eachLayer((l) => { if (l instanceof L.Polygon) l.bringToFront(); });
    overlayRef.current = g;
  }, [overlays, roadDetail, activeEnvLayer, siteRings]);

  // ── 환경성 항목 지도(용도지역 구성·개별 항목) ────────────────────────
  // 평소 제약도 대신 **그 항목 하나만** 단독으로 그린다. 보고서의
  // 항목별 지도가 이 화면을 그대로 캡처한 것이라, 여기서 다른 색이
  // 섞이면 협의 자료로 못 쓴다.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    envLayerRef.current?.remove();
    envLayerRef.current = null;
    if (!activeEnvLayer || !envLayers?.length) return;

    const g = L.layerGroup().addTo(map);
    const draw = (name: string, rings: LatLng[][], color: string, ha: number) => {
      rings.forEach((r) => {
        if (r.length < 4) return;
        g.addLayer(L.polygon(r, { color, weight: 1.5, fillColor: color, fillOpacity: 0.5 })
          .bindTooltip(`<b>${name}</b> · ${ha.toLocaleString()} ha`,
            { direction: 'top', sticky: true }));
      });
    };
    if (activeEnvLayer === ENV_ZONING_KEY) {
      envLayers.filter(e => e.kind === 'zoning')
        .forEach(e => draw(e.name, e.rings, ZONING_COLORS[e.name] || ZONING_DEFAULT, e.ha));
    } else {
      const item = envLayers.find(e => e.kind === 'item' && e.name === activeEnvLayer);
      if (item) draw(item.name, item.rings, ENV_ITEM_COLOR[item.status ?? ''] || '#e8a33d', item.ha);
    }
    envLayerRef.current = g;
  }, [envLayers, activeEnvLayer]);

  // ── 마커·반경원 갱신 ────────────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    // 구역 모드에서는 지점 마커·반경원을 숨긴다. 둘이 같이 떠 있으면
    // 무엇이 검토 대상인지 헷갈린다.
    if (lat == null || lng == null || mode !== 'point') {
      markerRef.current?.remove();
      circleRef.current?.remove();
      markerRef.current = null;
      circleRef.current = null;
      return;
    }

    const pos: L.LatLngTuple = [lat, lng];
    if (!markerRef.current) {
      markerRef.current = L.marker(pos, {
        icon: L.divIcon({
          className: 'ws-pinicon',
          html: '<span class="ws-pindot"></span><span class="ws-pinring"></span>',
          iconSize: [26, 26],
          iconAnchor: [13, 13],
        }),
        keyboard: false,
      }).addTo(map);
    } else {
      markerRef.current.setLatLng(pos);
    }

    // 반경은 화면 배율과 무관하게 '실제 미터'로 그린다
    if (!circleRef.current) {
      circleRef.current = L.circle(pos, {
        radius: radiusM, className: 'ws-radius',
        color: '#39d3e6', weight: 1.5, fillColor: '#39d3e6', fillOpacity: 0.14,
      }).addTo(map);
    } else {
      circleRef.current.setLatLng(pos).setRadius(radiusM);
    }

    // 위·경도 입력란으로 직접 넣은 좌표면 그 지점으로 이동한다
    const key = `${lat},${lng}`;
    if (selfSetRef.current !== key) {
      map.setView(pos, Math.max(map.getZoom(), SITE_ZOOM));
    }
    selfSetRef.current = key;
  }, [lat, lng, radiusM, mode]);

  const n = ring?.length ?? 0;
  return (
    <div className="ws-picker">
      {/* 빗금 패턴 정의 — '미확인' 필지에 쓴다. 색만으로는 범례를 봐야 뜻이
          통하지만 빗금은 보자마자 다른 것으로 읽힌다(기획서 §3.4). */}
      <svg width="0" height="0" style={{ position: 'absolute' }} aria-hidden="true">
        <defs>
          <pattern id="ws-hatch" width="7" height="7" patternUnits="userSpaceOnUse"
            patternTransform="rotate(45)">
            <rect width="7" height="7" fill="rgba(64,169,255,.18)" />
            <line x1="0" y1="0" x2="0" y2="7" stroke="#1677d2" strokeWidth="2.4" />
          </pattern>
        </defs>
      </svg>
      <div ref={hostRef} className="ws-leaflet" />
      <div className="ws-pickfoot">
        <span>
          {mode === 'layout' ? (
            <>
              지도를 클릭해 <b>발전기 위치</b>를 1호기부터 순서대로 찍으세요
              (현재 {n}기 · 반경 {turbineRadiusM.toLocaleString()}m)
              {n > 0 && <> · <b>Esc</b> 마지막 취소 · 번호 클릭 시 해당 기 삭제</>}
            </>
          ) : mode === 'parcel' ? (
            <>
              지도를 클릭해 <b>필지</b>를 고르세요 (현재 {n}필지 · 인접 필지를
              여러 개 고르면 하나의 사업지로 묶입니다)
              {n > 0 && <> · <b>Esc</b> 마지막 취소 · 지점 클릭 시 제외</>}
            </>
          ) : mode === 'area' ? (
            <>
              지도를 클릭해 <b>사업구역 꼭짓점</b>을 찍으세요 (현재 {n}개
              {n > 0 && n < 3 ? ' · 3개 이상 필요' : ''})
              {n > 0 && <> · <b>Esc</b> 마지막 취소 · 꼭짓점 클릭 시 삭제</>}
            </>
          ) : (
            <>지도를 클릭해 사업지를 지정하세요 · 휠 또는 <b>＋ －</b> 로 확대·축소</>
          )}
        </span>
        <span className="ws-hovercoord">
          {hover ? `${hover.lat.toFixed(5)}, ${hover.lng.toFixed(5)}` : `z${zoom}`}
        </span>
      </div>
    </div>
  );
}
