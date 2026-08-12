import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'

export type PickMode = 'point' | 'area' | 'layout';

interface SitePickerProps {
  lat: number | null;
  lng: number | null;
  radiusM: number;
  onPick: (lat: number, lng: number) => void;
  /**
   * 'area'  클릭이 사업구역 꼭짓점을 찍는다
   * 'layout' 클릭이 발전기 위치를 순서대로 찍는다 (1호기부터)
   */
  mode?: PickMode;
  /** 구역 꼭짓점 또는 발전기 위치 [[lat, lng], …] */
  ring?: [number, number][];
  onRingChange?: (ring: [number, number][]) => void;
  /** 배치선 모드 — 발전기 검토반경 / 연결선 검토반경 (m) */
  turbineRadiusM?: number;
  corridorRadiusM?: number;
  /** 검토 결과를 지도에 겹쳐 그릴 영역 */
  overlays?: { blocked: [number, number][][]; conditional: [number, number][][];
               free: [number, number][][] } | null;
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

/** 제약 등급별 표시색 — 배경이 위성영상이라 채도를 높이고 투명도를 낮춘다 */
const OVERLAY_STYLE: Record<string, L.PathOptions> = {
  blocked: { color: '#ff4d4f', weight: 1, fillColor: '#ff4d4f', fillOpacity: 0.42 },
  conditional: { color: '#faad14', weight: 1, fillColor: '#faad14', fillOpacity: 0.3 },
  free: { color: '#52c41a', weight: 1, fillColor: '#52c41a', fillOpacity: 0.28 },
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

export default function SitePicker({
  lat, lng, radiusM, onPick,
  mode = 'point', ring, onRingChange, overlays,
  turbineRadiusM = 500, corridorRadiusM = 100,
}: SitePickerProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const markerRef = useRef<L.Marker | null>(null);
  const circleRef = useRef<L.Circle | null>(null);
  const drawRef = useRef<L.LayerGroup | null>(null);
  const overlayRef = useRef<L.LayerGroup | null>(null);
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

    mapRef.current = map;

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
    };
    // 최초 1회만 생성한다. onPick은 setState만 쓰므로 클로저가 낡아도 안전하다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── 사업구역 꼭짓점·미리보기 ─────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    drawRef.current?.remove();
    drawRef.current = null;
    if (mode === 'point' || !ring?.length) return;

    const g = L.layerGroup().addTo(map);

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
        g.addLayer(L.marker([a, o], {
          icon: L.divIcon({
            className: 'ws-turbine',
            html: `<span>${i + 1}</span>`,
            iconSize: [22, 22], iconAnchor: [11, 11],
          }),
          keyboard: false,
        }));
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
      g.addLayer(L.circleMarker([a, o], {
        radius: 4, color: '#39d3e6', weight: 2,
        fillColor: i === ring.length - 1 ? '#fff' : '#39d3e6', fillOpacity: 1,
      }));
    });
    drawRef.current = g;
  }, [mode, ring, turbineRadiusM, corridorRadiusM]);

  // ── 검토 결과 겹쳐 그리기 ───────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    overlayRef.current?.remove();
    overlayRef.current = null;
    if (!overlays) return;

    const g = L.layerGroup().addTo(map);
    // 배제 → 조건부 → 제약없음 순으로 겹친다. 강한 제약이 위로 올라와야
    // 겹치는 지점에서 더 엄한 쪽이 보인다.
    (['free', 'conditional', 'blocked'] as const).forEach((k) => {
      (overlays[k] || []).forEach((r) => {
        if (r.length >= 4) g.addLayer(L.polygon(r, OVERLAY_STYLE[k]));
      });
    });
    overlayRef.current = g;
  }, [overlays]);

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
      <div ref={hostRef} className="ws-leaflet" />
      <div className="ws-pickfoot">
        <span>
          {mode === 'layout' ? (
            <>
              지도를 클릭해 <b>발전기 위치</b>를 1호기부터 순서대로 찍으세요
              (현재 {n}기 · 반경 {turbineRadiusM.toLocaleString()}m)
            </>
          ) : mode === 'area' ? (
            <>
              지도를 클릭해 <b>사업구역 꼭짓점</b>을 찍으세요 (현재 {n}개
              {n > 0 && n < 3 ? ' · 3개 이상 필요' : ''})
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
