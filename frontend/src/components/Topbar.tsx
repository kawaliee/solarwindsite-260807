interface TopbarProps {
  title: string;
  subtitle: string;
  view: string;
}

const VIEW_ICONS: Record<string, JSX.Element> = {
  ops: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>,
  windsite: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 22V13"/><circle cx="12" cy="10" r="2.2"/><path d="M12 8V2.5"/><path d="M13.9 11l4.8 2.8"/><path d="M10.1 11l-4.8 2.8"/></svg>,
  solarsite: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3.6"/><path d="M12 2.5v2.6M12 18.9v2.6M2.5 12h2.6M18.9 12h2.6"/><path d="M5.2 5.2l1.9 1.9M16.9 16.9l1.9 1.9M18.8 5.2l-1.9 1.9M7.1 16.9l-1.9 1.9"/></svg>,
};

export default function Topbar({ title, subtitle, view }: TopbarProps) {
  return (
    <div className="topbar">
      <div className="t-title">
        <div className="t-ic">{VIEW_ICONS[view]}</div>
        <div>
          <h1>{title}</h1>
          <div className="t-sub">{subtitle}</div>
        </div>
      </div>
      <div className="admin-badge"><span className="dot"></span>관리자 모드</div>
    </div>
  );
}
