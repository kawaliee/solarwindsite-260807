import { useEffect, useMemo, useState } from 'react'
import SitePicker from './SitePicker'
import { windsiteApi } from './api'
import {
  CONFIDENCE_LABEL,
  DIFFICULTY_LABEL,
  STATUS_LABEL,
  type AnalysisItem,
  type EvaluationResult,
  type LawRef,
  type ProviderConfigRow,
} from './types'

type Tab = 'result' | 'permits' | 'laws' | 'config';

export default function WindSiteView() {
  const [lat, setLat] = useState<number | null>(null);
  const [lng, setLng] = useState<number | null>(null);
  const [radiusM, setRadiusM] = useState(500);
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
              onPick={(a, o) => { setLat(a); setLng(o); }}
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
              <span>사업지 주소 <em>(표시용)</em></span>
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
                <input type="number" min={50} max={20000} step={50} value={radiusM}
                  onChange={e => setRadiusM(Number(e.target.value) || 500)} />
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
                      ? <span className="ws-badge na">공개 API 없음</span>
                      : c.configured
                        ? <span className="ws-badge ok">연동됨</span>
                        : <span className="ws-badge no">키 미설정</span>}
                  </td>
                  <td className="ws-mono">{c.missing.join(', ') || (c.required_settings.join(', ') || '—')}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="ws-footnote">
            군사·비행안전, 전력계통 여유도, KIER 풍력자원지도는 좌표 기반 공개 API가 제공되지 않아
            인증키를 등록해도 자동 판정되지 않습니다. 해당 항목은 관할부대·한전 등과의 협의 절차로 처리하십시오.
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
