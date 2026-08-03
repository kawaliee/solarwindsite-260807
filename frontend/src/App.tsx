import { useState } from 'react'
import Sidebar from './components/Sidebar'
import Topbar from './components/Topbar'
import ChatView from './features/chat/ChatView'
import ContractView from './features/contracts/ContractView'
import OpsView from './features/ops/OpsView'
import WindSiteView from './features/windsite/WindSiteView'
import PlaceholderView from './components/PlaceholderView'
import Login from './components/Login'

type ViewType = 'chat' | 'contract' | 'finance' | 'ops' | 'windsite';

interface UserProfile {
  id?: string;
  name: string;
  email: string;
  department: string;
  role: string;
}

const VIEW_META: Record<ViewType, { title: string; sub: string }> = {
  chat: { title: '대화', sub: '사내 자료 기반 질의응답' },
  contract: { title: '계약', sub: '계약서 생성 및 검토' },
  finance: { title: '재무모델', sub: '재무모델 생성 및 검토 (준비 중)' },
  ops: { title: '운영관리 Dashboard', sub: '전국 사업장 분포 · 발전 자산 통합 모니터링' },
  windsite: { title: '풍력 입지타당성 검토', sub: '입지 규제 자동 스크리닝 · 인허가 로드맵 · 관련 법령' },
};

export default function App() {
  const [user, setUser] = useState<UserProfile | null>(() => {
    const saved = localStorage.getItem('user');
    return saved ? JSON.parse(saved) : null;
  });

  const [activeView, setActiveView] = useState<ViewType>('chat');
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [conversationRefreshKey, setConversationRefreshKey] = useState(0);

  const handleLoginSuccess = (userData: UserProfile) => {
    localStorage.setItem('user', JSON.stringify(userData));
    setUser(userData);
  };

  const handleLogout = () => {
    localStorage.removeItem('user');
    setUser(null);
  };

  const handleNewChat = () => {
    setActiveConversationId(null);
    setConversationRefreshKey(k => k + 1);
  };

  const meta = VIEW_META[activeView];

  if (!user) {
    return <Login onLoginSuccess={handleLoginSuccess} />;
  }

  return (
    <>
      <Sidebar
        activeView={activeView}
        onNavigate={(view) => setActiveView(view as ViewType)}
        activeConversationId={activeConversationId}
        onSelectConversation={setActiveConversationId}
        onNewChat={handleNewChat}
        refreshKey={conversationRefreshKey}
        user={user}
        onLogout={handleLogout}
      />
      <main className="main-content">
        <Topbar title={meta.title} subtitle={meta.sub} view={activeView} />

        {activeView === 'chat' && (
          <ChatView
            conversationId={activeConversationId}
            onConversationCreated={(id) => {
              setActiveConversationId(id);
              setConversationRefreshKey(k => k + 1);
            }}
          />
        )}

        {activeView === 'contract' && <ContractView />}

        {activeView === 'finance' && (
          <PlaceholderView
            icon="chart"
            title="재무모델"
            description="재무모델 생성 및 검토 기능은 현재 준비 중입니다. 추후 동일한 패턴으로 확장될 예정입니다."
          />
        )}

        {activeView === 'ops' && <OpsView />}

        {activeView === 'windsite' && <WindSiteView />}
      </main>
    </>
  );
}
