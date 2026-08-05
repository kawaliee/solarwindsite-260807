import { useEffect, useState } from 'react'
import { conversationsApi, type Conversation } from '../api/client'

interface SidebarProps {
  activeView: string;
  onNavigate: (view: string) => void;
  activeConversationId: string | null;
  onSelectConversation: (id: string) => void;
  onNewChat: () => void;
  refreshKey: number;
  user: { name: string; email: string; department: string; role: string } | null;
  onLogout: () => void;
}

export default function Sidebar({
  activeView, onNavigate, activeConversationId, onSelectConversation, onNewChat, refreshKey, user, onLogout
}: SidebarProps) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState('');

  // 편집 시작
  const handleStartEdit = (e: React.MouseEvent, id: string, currentTitle: string) => {
    e.stopPropagation();
    setEditingId(id);
    setEditTitle(currentTitle);
  };

  // 편집 저장
  const handleSaveEdit = async (e: React.SyntheticEvent, id: string) => {
    e.stopPropagation();
    const trimmed = editTitle.trim();
    if (!trimmed) {
      setEditingId(null);
      return;
    }

    // 반응성 속도 개선을 위해 로컬 상태 즉시 갱신
    setConversations(prev => prev.map(c => c.id === id ? { ...c, title: trimmed } : c));
    setEditingId(null);

    try {
      await conversationsApi.update(id, { title: trimmed });
    } catch (err) {
      console.error('Failed to update title:', err);
      // 실패 시 롤백용 원본 재조회
      conversationsApi.list()
        .then(data => setConversations(data.results || []));
    }
  };

  // 키 입력 분기
  const handleEditKeyDown = (e: React.KeyboardEvent, id: string) => {
    if (e.key === 'Enter') {
      handleSaveEdit(e, id);
    } else if (e.key === 'Escape') {
      e.stopPropagation();
      setEditingId(null);
    }
  };

  useEffect(() => {
    if (activeView === 'chat') {
      conversationsApi.list()
        .then(data => setConversations(data.results || []))
        .catch(() => setConversations([]));
    }
  }, [activeView, refreshKey]);

  const navItems = [
    { key: 'chat', label: '대화', icon: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5Z" /></svg> },
    { key: 'contract', label: '계약', icon: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /><path d="M9 13h6M9 17h4" /></svg> },
    { key: 'finance', label: '재무모델', icon: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M3 3v18h18" /><path d="M7 14l3-3 3 3 5-6" /></svg> },
    { key: 'ops', label: '운영관리 Dashboard', icon: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></svg> },
    { key: 'windsite', label: '풍력 입지검토', icon: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 22V13" /><circle cx="12" cy="10" r="2.2" /><path d="M12 8V2.5" /><path d="M13.9 11l4.8 2.8" /><path d="M10.1 11l-4.8 2.8" /></svg> },
    { key: 'factsheet', label: '사업 정보 입력', icon: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="4" y="3" width="16" height="18" rx="2" /><path d="M9 8h6M9 12h6M9 16h3" /></svg> },
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
        <div className="txt"><b>재생E AI Agent</b><span>RENEWABLE ENERGY</span></div>
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

      {/* Chat History */}
      {activeView === 'chat' && (
        <div className="history">
          <button className="new-chat" onClick={onNewChat}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M12 5v14M5 12h14" />
            </svg>
            새 대화
          </button>
          {conversations.length > 0 && (
            <>
              <div className="nav-label" style={{ paddingTop: 8 }}>대화 기록</div>
              {conversations.map(conv => (
                <div
                  key={conv.id}
                  className={`h-row ${activeConversationId === conv.id ? 'active' : ''} ${editingId === conv.id ? 'editing' : ''}`}
                  onClick={() => onSelectConversation(conv.id)}
                >
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                  </svg>
                  {editingId === conv.id ? (
                    <input
                      type="text"
                      className="edit-title-input"
                      value={editTitle}
                      onChange={e => setEditTitle(e.target.value)}
                      onBlur={e => handleSaveEdit(e, conv.id)}
                      onKeyDown={e => handleEditKeyDown(e, conv.id)}
                      onClick={e => e.stopPropagation()}
                      autoFocus
                    />
                  ) : (
                    <>
                      <span className="title-txt">{conv.title || '새 대화'}</span>
                      <button
                        className="edit-title-btn"
                        onClick={e => handleStartEdit(e, conv.id, conv.title || '새 대화')}
                        title="제목 수정"
                      >
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                          <path d="M18.5 2.5a2.121 2.121 0 1 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
                        </svg>
                      </button>
                    </>
                  )}
                </div>
              ))}
            </>
          )}
        </div>
      )}

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
