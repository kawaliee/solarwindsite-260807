interface SidebarProps {
  activeView: string;
  onNavigate: (view: string) => void;
  user: { name: string; email: string; department: string; role: string } | null;
  onLogout: () => void;
}

export default function Sidebar({
  activeView, onNavigate, user, onLogout
}: SidebarProps) {
  const navItems = [
    { key: 'windsite', label: '풍력 입지검토', icon: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 22V13" /><circle cx="12" cy="10" r="2.2" /><path d="M12 8V2.5" /><path d="M13.9 11l4.8 2.8" /><path d="M10.1 11l-4.8 2.8" /></svg> },
    { key: 'solarsite', label: '태양광 입지검토', icon: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3.6" /><path d="M12 2.5v2.6M12 18.9v2.6M2.5 12h2.6M18.9 12h2.6" /><path d="M5.2 5.2l1.9 1.9M16.9 16.9l1.9 1.9M18.8 5.2l-1.9 1.9M7.1 16.9l-1.9 1.9" /></svg> },
    { key: 'ops', label: '운영관리 Dashboard', icon: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></svg> },
  ];

  return (
    <aside className="sidebar">
      {/* Brand */}
      <div className="brand">
        <img
          src="/logo.png"
          alt="재생E AI Agent 로고"
          style={{
            width: '38px',
            height: '38px',
            borderRadius: '15px',
            objectFit: 'cover',
            flexShrink: 0,
            display: 'block',
          }}
        />
        <div className="txt"><b>입지타당성 검토</b><span>RENEWABLE ENERGY</span></div>
      </div>

      {/* Navigation */}
      <div className="nav-label">메뉴</div>
      {navItems.map(item => (
        <button
          key={item.key}
          className={`nav-item ${activeView === item.key ? 'active' : ''}`}
          onClick={() => onNavigate(item.key)}
        >
          {item.icon}
          {item.label}
        </button>
      ))}

      {/* User */}
      <div className="side-foot" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div className="user-chip" style={{ flex: 1 }}>
          <div className="av">{user?.name ? user.name[0] : '관'}</div>
          <div className="u-txt">
            <b>{user?.name || 'AI Agent 관리자'}</b>
            <span>{user?.department || '재생E 사업개발실'}</span>
          </div>
        </div>
        <button
          onClick={onLogout}
          title="로그아웃"
          style={{
            marginLeft: '8px',
            opacity: 0.6,
            transition: 'opacity 0.15s',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '6px',
            borderRadius: '6px',
            color: '#AEC6BB'
          }}
          onMouseEnter={(e) => e.currentTarget.style.opacity = '1'}
          onMouseLeave={(e) => e.currentTarget.style.opacity = '0.6'}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: '16px', height: '16px' }}>
            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
            <polyline points="16 17 21 12 16 7" />
            <line x1="21" y1="12" x2="9" y2="12" />
          </svg>
        </button>
      </div>
    </aside>
  );
}
