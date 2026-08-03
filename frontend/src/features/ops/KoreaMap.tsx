import { useMemo, useState } from 'react'
import { KR_PROVINCES, KR_VIEWBOX, normalizeProvince } from './koreaMap.data'
import { positionSites, STATUS_LABEL, type OpsSite, type PositionedSite } from './sites'

interface KoreaMapProps {
  sites: OpsSite[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}

interface TipState {
  x: number;
  y: number;
  site: PositionedSite;
}

/** 상태별 마커 색 */
const MARKER_FILL: Record<string, string> = {
  operating: '#39e08a',
  construction: '#ff9f43',
  development: '#39d3e6',
};

export default function KoreaMap({ sites, selectedId, onSelect }: KoreaMapProps) {
  const [tip, setTip] = useState<TipState | null>(null);

  const positioned = useMemo(() => positionSites(sites), [sites]);

  // 시·도별 사업장 수 집계 (하이라이트 강도용)
  const provinceCount = useMemo(() => {
    const m = new Map<string, number>();
    for (const s of positioned) {
      const p = normalizeProvince(s.province);
      if (p) m.set(p, (m.get(p) ?? 0) + 1);
    }
    return m;
  }, [positioned]);

  // 같은 좌표(동일 시군구)에 여러 사업장이 있으면 살짝 흩뿌린다
  const spread = useMemo(() => {
    const groups = new Map<string, PositionedSite[]>();
    for (const s of positioned) {
      const key = `${s.x.toFixed(1)},${s.y.toFixed(1)}`;
      const arr = groups.get(key) ?? [];
      arr.push(s);
      groups.set(key, arr);
    }
    const out: PositionedSite[] = [];
    for (const arr of groups.values()) {
      if (arr.length === 1) {
        out.push(arr[0]);
        continue;
      }
      const r = 7;
      arr.forEach((s, i) => {
        const angle = (Math.PI * 2 * i) / arr.length - Math.PI / 2;
        out.push({ ...s, x: s.x + Math.cos(angle) * r, y: s.y + Math.sin(angle) * r });
      });
    }
    return out;
  }, [positioned]);

  return (
    <div className="krmap-wrap">
      <svg
        className="krmap"
        viewBox={`0 0 ${KR_VIEWBOX.w} ${KR_VIEWBOX.h}`}
        role="img"
        aria-label="전국 운영사업장 지도"
      >
        {/* 시·도 경계 */}
        <g>
          {KR_PROVINCES.map(p => {
            const n = provinceCount.get(p.name) ?? 0;
            return (
              <path
                key={p.name}
                d={p.d}
                className={`prov${n > 0 ? ' hot' : ''}`}
              >
                <title>{p.name}{n > 0 ? ` · 사업장 ${n}개` : ''}</title>
              </path>
            );
          })}
        </g>

        {/* 사업장이 있는 시·도 라벨 */}
        <g className="prov-labels">
          {KR_PROVINCES.filter(p => (provinceCount.get(p.name) ?? 0) > 0).map(p => (
            <text key={p.name} x={p.cx} y={p.cy} textAnchor="middle">
              {p.name.replace(/(특별자치도|특별자치시|특별시|광역시|도)$/, '')}
            </text>
          ))}
        </g>

        {/* 사업장 마커 */}
        <g>
          {spread.map(s => {
            const on = selectedId === s.id;
            const fill = MARKER_FILL[s.status] ?? '#39d3e6';
            return (
              <g
                key={s.id}
                className={`site-mk${on ? ' on' : ''}`}
                onMouseEnter={e => setTip({ x: e.clientX, y: e.clientY, site: s })}
                onMouseMove={e => setTip({ x: e.clientX, y: e.clientY, site: s })}
                onMouseLeave={() => setTip(null)}
                onClick={() => onSelect(on ? null : s.id)}
              >
                {on && <circle cx={s.x} cy={s.y} r={13} fill={fill} opacity={0.18} />}
                <circle cx={s.x} cy={s.y} r={on ? 8 : 6} fill={fill} stroke="#04121c" strokeWidth={1.6} />
                <circle cx={s.x} cy={s.y} r={2.2} fill="#04121c" />
              </g>
            );
          })}
        </g>
      </svg>

      <div className="krmap-legend">
        <span><i className="dot" style={{ background: MARKER_FILL.operating }} />운영중</span>
        <span><i className="dot" style={{ background: MARKER_FILL.construction }} />건설중</span>
        <span><i className="dot" style={{ background: MARKER_FILL.development }} />개발중</span>
        <span className="legend-note">마커를 클릭하면 상세가 표시됩니다</span>
      </div>

      {tip && (
        <div
          className="krmap-tip"
          style={{ left: Math.min(tip.x + 14, window.innerWidth - 250), top: tip.y + 14 }}
        >
          <b>{tip.site.name}</b>
          <div>{tip.site.project}</div>
          <div>
            {tip.site.province} {tip.site.sigungu}
            {tip.site.coordSource === 'approx' && <span className="approx">위치 근사</span>}
          </div>
          <div>
            {STATUS_LABEL[tip.site.status]} ·{' '}
            {tip.site.capacityMw != null ? `${tip.site.capacityMw} MW` : '용량 미상'}
          </div>
        </div>
      )}
    </div>
  );
}
