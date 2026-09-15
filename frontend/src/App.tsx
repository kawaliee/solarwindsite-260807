import { useState } from 'react'
import Sidebar from './components/Sidebar'
import Topbar from './components/Topbar'
import OpsView from './features/ops/OpsView'
import WindSiteView from './features/windsite/WindSiteView'
import ViewBoundary from './components/ViewBoundary'
import Login from './components/Login'

type ViewType = 'windsite' | 'solarsite' | 'ops';

interface UserProfile {
  id?: string;
  name: string;
  email: string;
  department: string;
  role: string;
}

const VIEW_META: Record<ViewType, { title: string; sub: string }> = {
  windsite: { title: '풍력 입지타당성 검토', sub: '입지 규제 자동 스크리닝 · 인허가 로드맵 · 관련 법령' },
  solarsite: { title: '태양광 입지타당성 검토', sub: '필지 단위 정밀판정 · 태양광 이격거리 조례 · 가용면적 산출' },
  ops: { title: '운영관리 Dashboard', sub: '전국 사업장 분포 · 발전 자산 통합 모니터링' },
};

export default function App() {
  const [user, setUser] = useState<UserProfile | null>(() => {
    const saved = localStorage.getItem('user');
    return saved ? JSON.parse(saved) : null;
  });

  const [activeView, setActiveView] = useState<ViewType>('windsite');

  const handleLoginSuccess = (userData: UserProfile) => {
    localStorage.setItem('user', JSON.stringify(userData));
    setUser(userData);
  };

  const handleLogout = () => {
    localStorage.removeItem('user');
    setUser(null);
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
        user={user}
        onLogout={handleLogout}
      />
      <main className="main-content">
        <Topbar title={meta.title} subtitle={meta.sub} view={activeView} />

        {/* 화면 하나가 터져도 앱 전체가 흰 화면이 되지 않도록 가둔다.
            key를 화면 이름으로 줘 메뉴를 옮기면 경계가 새로 만들어진다. */}
        <ViewBoundary key={activeView} name={meta.title}>
        {/* 두 카테고리는 완전히 분리된 화면이다. ViewBoundary의 key가
            화면 이름이라 메뉴를 옮기면 상태가 초기화되고, 한쪽에서 찍은
            좌표·검토 조건이 다른 쪽으로 새지 않는다. */}
        {activeView === 'windsite' && <WindSiteView energy="WIND" />}

        {activeView === 'solarsite' && <WindSiteView energy="SOLAR" />}

        {activeView === 'ops' && <OpsView />}
        </ViewBoundary>
      </main>
    </>
  );
}
