interface PlaceholderViewProps {
  icon: 'chart' | 'dashboard';
  title: string;
  description: string;
}

export default function PlaceholderView({ icon, title, description }: PlaceholderViewProps) {
  const icons = {
    chart: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M3 3v18h18"/><path d="M7 14l3-3 3 3 5-6"/></svg>,
    dashboard: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>,
  };

  return (
    <div className="placeholder-view">
      <div className="ph-icon">{icons[icon]}</div>
      <h2>{title}</h2>
      <p>{description}</p>
      <div style={{ marginTop: 16, fontSize: 12, color: 'var(--ink-faint)' }}>
        메뉴 자리가 마련되어 있으며, 추후 구조 변경 없이 기능이 추가됩니다.
      </div>
    </div>
  );
}
