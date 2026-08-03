import { useEffect, useMemo, useState } from 'react'
import KoreaMap from './KoreaMap'
import {
  fetchOpsSites,
  summarize,
  STATUS_LABEL,
  type OpsSite,
  type SiteStatus,
} from './sites'

type StatusFilter = 'all' | SiteStatus;

export default function OpsView() {
  const [sites, setSites] = useState<OpsSite[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [provinceFilter, setProvinceFilter] = useState<string>('all');

  useEffect(() => {
    let alive = true;
    fetchOpsSites()
      .then(data => { if (alive) { setSites(data); setLoading(false); } })
      .catch(() => { if (alive) { setSites([]); setLoading(false); } });
    return () => { alive = false; };
  }, []);

  const provinces = useMemo(
    () => Array.from(new Set(sites.map(s => s.province))).sort(),
    [sites],
  );

  const filtered = useMemo(
    () => sites.filter(s =>
      (statusFilter === 'all' || s.status === statusFilter) &&
      (provinceFilter === 'all' || s.province === provinceFilter)
    ),
    [sites, statusFilter, provinceFilter],
  );

  const kpi = useMemo(() => summarize(filtered), [filtered]);
  const selected = filtered.find(s => s.id === selectedId) ?? null;

  if (loading) {
    return <div className="ops-view"><p className="ops-empty">사업장 정보를 불러오는 중…</p></div>;
  }

  return (
    <div className="ops-view">
      {/* KPI */}
      <div className="ops-kpis">
        <div className="ops-kpi cyan">
          <div className="n">{kpi.total}</div>
          <div className="l">총 사업장</div>
        </div>
        <div className="ops-kpi green">
          <div className="n">{kpi.operating}</div>
          <div className="l">운영중</div>
        </div>
        <div className="ops-kpi orange">
          <div className="n">{kpi.construction}</div>
          <div className="l">건설중</div>
        </div>
        <div className="ops-kpi cyan">
          <div className="n">{kpi.development}</div>
          <div className="l">개발중</div>
        </div>
        <div className="ops-kpi amber">
          <div className="n">
            {kpi.knownCapacityMw.toLocaleString(undefined, { maximumFractionDigits: 1 })}
            <small> MW</small>
          </div>
          <div className="l">
            확인된 설비용량
            {kpi.unknownCapacityCount > 0 && (
              <span className="ops-uncertain">미상 {kpi.unknownCapacityCount}건 제외</span>
            )}
          </div>
        </div>
        <div className="ops-kpi cyan">
          <div className="n">{kpi.provinceCount}</div>
          <div className="l">사업 지역(시·도)</div>
        </div>
      </div>

      {/* 필터 */}
      <div className="ops-filters">
        <span className="ops-section-t">전국 운영사업장 현황</span>
        <span className="ops-mini">{filtered.length}개 표시 중</span>
        <span style={{ marginLeft: 'auto' }} />
        <label className="ops-mini">단계</label>
        <select value={statusFilter} onChange={e => setStatusFilter(e.target.value as StatusFilter)}>
          <option value="all">전체</option>
          <option value="operating">운영중</option>
          <option value="construction">건설중</option>
          <option value="development">개발중</option>
        </select>
        <label className="ops-mini">지역</label>
        <select value={provinceFilter} onChange={e => setProvinceFilter(e.target.value)}>
          <option value="all">전체</option>
          {provinces.map(p => <option key={p} value={p}>{p}</option>)}
        </select>
      </div>

      {/* 지도 + 목록 */}
      <div className="ops-grid">
        <div className="ops-card">
          <div className="ops-card-hd"><span className="tag">MAP</span> 사업장 분포</div>
          <div className="ops-card-bd">
            <KoreaMap sites={filtered} selectedId={selectedId} onSelect={setSelectedId} />
          </div>
        </div>

        <div className="ops-side">
          <div className="ops-card">
            <div className="ops-card-hd"><span className="tag">SITES</span> 사업장 목록</div>
            <div className="ops-card-bd ops-list">
              {filtered.length === 0 && <p className="ops-empty">조건에 맞는 사업장이 없습니다.</p>}
              {filtered.map(s => (
                <button
                  key={s.id}
                  className={`ops-site-row${selectedId === s.id ? ' on' : ''}`}
                  onClick={() => setSelectedId(selectedId === s.id ? null : s.id)}
                >
                  <span className={`ops-dot ${s.status}`} />
                  <span className="nm">
                    <b>{s.name}</b>
                    <small>{s.province} {s.sigungu} · {s.project}</small>
                  </span>
                  <span className="cap">
                    {s.capacityMw != null ? `${s.capacityMw} MW` : '미상'}
                  </span>
                </button>
              ))}
            </div>
          </div>

          {selected && (
            <div className="ops-card">
              <div className="ops-card-hd"><span className="tag">DETAIL</span> {selected.name}</div>
              <div className="ops-card-bd">
                <div className="ops-kv"><span className="k">PJT</span><span>{selected.project}</span></div>
                <div className="ops-kv"><span className="k">유형</span><span>{selected.energy}</span></div>
                <div className="ops-kv"><span className="k">위치</span><span>{selected.province} {selected.sigungu}</span></div>
                <div className="ops-kv">
                  <span className="k">설비용량</span>
                  <span>{selected.capacityMw != null ? `${selected.capacityMw} MW` : <em className="ops-unknown">미상 (DB 연동 시 표시)</em>}</span>
                </div>
                <div className="ops-kv"><span className="k">단계</span><span>{STATUS_LABEL[selected.status]}</span></div>
                <div className="ops-kv">
                  <span className="k">COD</span>
                  <span>{selected.cod ?? <em className="ops-unknown">미상</em>}</span>
                </div>
                {selected.note && (
                  <div className="ops-kv"><span className="k">비고</span><span>{selected.note}</span></div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      <p className="ops-footnote">
        ※ 현재 표시되는 사업장은 임시 데이터입니다. 운영사업장 DB 연동 시 자동으로 대체되며,
        설비용량·COD 등 미확인 항목도 함께 채워집니다. 마커 위치는 시·군·구 중심 근사 좌표를 사용하므로
        정확한 부지 위치는 사업장 데이터에 위·경도를 입력하면 반영됩니다.
      </p>
    </div>
  );
}
