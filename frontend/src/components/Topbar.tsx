interface TopbarProps {
  title: string;
  subtitle: string;
  view: string;
}

const VIEW_ICONS: Record<string, JSX.Element> = {
  chat: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5Z"/></svg>,
  contract: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>,
  finance: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M3 3v18h18"/><path d="M7 14l3-3 3 3 5-6"/></svg>,
  ops: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>,
  windsite: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 22V13"/><circle cx="12" cy="10" r="2.2"/><path d="M12 8V2.5"/><path d="M13.9 11l4.8 2.8"/><path d="M10.1 11l-4.8 2.8"/></svg>,
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
