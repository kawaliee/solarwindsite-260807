import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'

interface SitePickerProps {
  lat: number | null;
  lng: number | null;
  radiusM: number;
  onPick: (lat: number, lng: number) => void;
}

/**
 * 실제 배경지도 위에서 사업지를 클릭으로 지정한다.
 *
 * 종전에는 시·도 경계만 그린 전국 SVG라 클릭 정밀도가 km 단위였고 지형을 볼 수 없었다.
 * 풍력 입지는 능선·표고·사면향이 곧 사업성이라 등고선이 보이지 않으면 지도의 의미가 없다.
 * 그래서 슬리피 맵(Leaflet)으로 바꾸고 지형도를 기본 배경으로 둔다.
 *
 * 배경지도
 *   · 지형·등고선  OpenTopoMap (등고선 + 음영기복) — 기본값
 *   · 일반지도     V-World Base (국토지리정보원 수치지도, 한글 지명)
 *   · 위성영상     V-World Satellite
 *   · 지명·경계    V-World Hybrid — 위성/지형 위에 겹치는 오버레이
 *
 * V-World 타일은 VITE_VWORLD_KEY가 있을 때만 등록한다. 키가 없어도
 * OpenTopoMap·OSM·Esri로 지도는 그대로 뜬다(판정 기능과 무관).
 */

const VWORLD_KEY: string = import.meta.env.VITE_VWORLD_KEY || '';
const vw = (layer: string, ext: string) =>
  `https://api.vworld.kr/req/wmts/1.0.0/${VWORLD_KEY}/${layer}/{z}/{y}/{x}.${ext}`;

/** 전국이 한눈에 들어오는 초기 시점 */
const KR_CENTER: L.LatLngTuple = [36.35, 127.9];
const KR_ZOOM = 7;
/** 지점을 찍었을 때 들어가는 배율 — 능선이 판별되는 수준 */
const SITE_ZOOM = 14;

export default function SitePicker({ lat, lng, radiusM, onPick }: SitePickerProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const markerRef = useRef<L.Marker | null>(null);
  const circleRef = useRef<L.Circle | null>(null);
  /** 지도 클릭으로 방금 바꾼 좌표 — 외부 입력과 구분해 불필요한 화면 이동을 막는다 */
  const selfSetRef = useRef<string>('');

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
    const esriTerrain = L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',
      { maxZoom: 19, attribution: 'Esri, USGS, NOAA' });

    const bases: Record<string, L.TileLayer> = {
      '지형·등고선': topo,
      '일반지도 (OSM)': osm,
      '지형도 (Esri)': esriTerrain,
    };
    const overlays: Record<string, L.TileLayer> = {};

    if (VWORLD_KEY) {
      bases['일반지도 (V-World)'] = L.tileLayer(vw('Base', 'png'), {
        maxZoom: 19, attribution: '© 국토교통부 V-World',
      });
      bases['위성영상 (V-World)'] = L.tileLayer(vw('Satellite', 'jpeg'), {
        maxZoom: 19, maxNativeZoom: 18, attribution: '© 국토교통부 V-World',
      });
      overlays['지명·경계 표기'] = L.tileLayer(vw('Hybrid', 'png'), {
        maxZoom: 19, opacity: 0.9, attribution: '© 국토교통부 V-World',
      });
    }

    const map = L.map(hostRef.current, {
      center: lat != null && lng != null ? [lat, lng] : KR_CENTER,
      zoom: lat != null && lng != null ? SITE_ZOOM : KR_ZOOM,
      layers: [topo],
      zoomControl: true,
      minZoom: 6,
      maxZoom: 19,
    });

    L.control.layers(bases, overlays, { position: 'topright', collapsed: false }).addTo(map);
    L.control.scale({ metric: true, imperial: false, position: 'bottomleft' }).addTo(map);

    map.on('click', (e: L.LeafletMouseEvent) => {
      const a = Number(e.latlng.lat.toFixed(5));
      const o = Number(e.latlng.lng.toFixed(5));
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
    };
    // 최초 1회만 생성한다. onPick은 setState만 쓰므로 클로저가 낡아도 안전하다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── 마커·반경원 갱신 ────────────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    if (lat == null || lng == null) {
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
  }, [lat, lng, radiusM]);

  return (
    <div className="ws-picker">
      <div ref={hostRef} className="ws-leaflet" />
      <div className="ws-pickfoot">
        <span>
          지도를 클릭해 사업지를 지정하세요 · 휠 또는 <b>＋ －</b> 로 확대·축소
        </span>
        <span className="ws-hovercoord">
          {hover ? `${hover.lat.toFixed(5)}, ${hover.lng.toFixed(5)}` : `z${zoom}`}
        </span>
      </div>
    </div>
  );
}
