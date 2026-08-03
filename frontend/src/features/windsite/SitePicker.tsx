import { useMemo, useRef, useState } from 'react'
import { KR_PROVINCES, KR_VIEWBOX, project, unproject } from '../ops/koreaMap.data'

interface SitePickerProps {
  lat: number | null;
  lng: number | null;
  radiusM: number;
  onPick: (lat: number, lng: number) => void;
}

/**
 * 전국 지도에서 사업지를 클릭으로 선택한다.
 * 실측 시·도 경계(KoreaMap 데이터) 위에 클릭 지점을 표시하고 위경도로 역변환한다.
 *
 * ※ 전국 스케일 SVG라 클릭 정밀도는 약 1km 수준이다.
 *   정밀 좌표가 필요하면 좌하단 입력란에 위·경도를 직접 넣거나,
 *   V-World/카카오 지도 SDK를 붙여 배경지도를 교체하십시오.
 */
export default function SitePicker({ lat, lng, radiusM, onPick }: SitePickerProps) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [hover, setHover] = useState<{ lat: number; lng: number } | null>(null);

  const marker = useMemo(
    () => (lat != null && lng != null ? project(lng, lat) : null),
    [lat, lng],
  );

  // 반경을 SVG 단위로 환산 (경도 1도 ≈ kx SVG단위, 위도 1도 ≈ 88.9km 부근)
  const radiusUnits = useMemo(() => {
    const kmPerDegLat = 110.95;
    const unitsPerDegLat = KR_VIEWBOX.h / 5.5; // 대략치 — 시각 표현용
    return Math.max(2, (radiusM / 1000 / kmPerDegLat) * unitsPerDegLat);
  }, [radiusM]);

  const toGeo = (e: React.MouseEvent<SVGSVGElement>) => {
    const svg = svgRef.current;
    if (!svg) return null;
    const rect = svg.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * KR_VIEWBOX.w;
    const y = ((e.clientY - rect.top) / rect.height) * KR_VIEWBOX.h;
    return unproject(x, y);
  };

  return (
    <div className="ws-picker">
      <svg
        ref={svgRef}
        className="krmap ws-pickmap"
        viewBox={`0 0 ${KR_VIEWBOX.w} ${KR_VIEWBOX.h}`}
        onClick={e => {
          const g = toGeo(e);
          if (g) onPick(Number(g.lat.toFixed(5)), Number(g.lng.toFixed(5)));
        }}
        onMouseMove={e => setHover(toGeo(e))}
        onMouseLeave={() => setHover(null)}
        role="img"
        aria-label="사업지 선택 지도"
      >
        {KR_PROVINCES.map(p => (
          <path key={p.name} d={p.d} className="prov">
            <title>{p.name}</title>
          </path>
        ))}

        {marker && (
          <g className="ws-marker">
            <circle cx={marker.x} cy={marker.y} r={radiusUnits} className="ws-buffer" />
            <circle cx={marker.x} cy={marker.y} r={6} className="ws-pin" />
            <line x1={marker.x - 12} y1={marker.y} x2={marker.x + 12} y2={marker.y} className="ws-cross" />
            <line x1={marker.x} y1={marker.y - 12} x2={marker.x} y2={marker.y + 12} className="ws-cross" />
          </g>
        )}
      </svg>

      <div className="ws-pickfoot">
        <span>지도를 클릭해 사업지를 지정하세요</span>
        {hover && (
          <span className="ws-hovercoord">
            {hover.lat.toFixed(4)}, {hover.lng.toFixed(4)}
          </span>
        )}
      </div>
    </div>
  );
}
