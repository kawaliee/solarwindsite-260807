import React, { useState } from 'react';
import { authApi } from '../api/client';

interface LoginProps {
  onLoginSuccess: (user: { name: string; email: string; department: string; role: string }) => void;
}

export default function Login({ onLoginSuccess }: LoginProps) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email || !password) {
      setError('이메일과 비밀번호를 모두 입력해 주세요.');
      return;
    }

    setIsLoading(true);
    setError('');

    try {
      const response = await authApi.login({ email, password });
      setIsLoading(false);
      onLoginSuccess(response.user);
    } catch (err: any) {
      setIsLoading(false);
      setError(err.message || '로그인에 실패했습니다. 아이디 또는 비밀번호를 확인하세요.');
    }
  };

  return (
    <div style={styles.container}>
      <div style={styles.card} className="fade-in">
        <div style={styles.logoSection}>
          <div style={styles.mark}>
            <svg viewBox="0 0 24 24" fill="none" stroke="#03121a" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={styles.svg}>
              <path d="M12 2a7 7 0 0 0-7 7c0 3 2 5 4 7l3 6 3-6c2-2 4-4 4-7a7 7 0 0 0-7-7Z" />
              <path d="M12 9v0" />
              <path d="M9 9c1.5-1.5 4.5-1.5 6 0" />
            </svg>
          </div>
          <div style={styles.brandText}>
            <b style={styles.brandTitle}>재생E AI Agent</b>
            <span style={styles.brandSub}>RENEWABLE ENERGY SYSTEMS</span>
          </div>
        </div>

        <h2 style={styles.title}>로그인</h2>
        <p style={styles.subtitle}>재생에너지 사업관리 AI 어시스턴트 서비스</p>

        <form onSubmit={handleSubmit} style={styles.form}>
          <div style={styles.inputGroup}>
            <label style={styles.label}>이메일 주소</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="admin@example.com"
              disabled={isLoading}
              style={styles.input}
              required
            />
          </div>

          <div style={styles.inputGroup}>
            <label style={styles.label}>비밀번호</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              disabled={isLoading}
              style={styles.input}
              required
            />
          </div>

          {error && (
            <div style={styles.errorContainer}>
              <svg viewBox="0 0 24 24" fill="none" stroke="#BC4B38" strokeWidth="2" style={styles.errorIcon}>
                <circle cx="12" cy="12" r="10" />
                <line x1="12" y1="8" x2="12" y2="12" />
                <line x1="12" y1="16" x2="12.01" y2="16" />
              </svg>
              <span style={styles.errorText}>{error}</span>
            </div>
          )}

          <button
            type="submit"
            disabled={isLoading}
            style={{
              ...styles.button,
              opacity: isLoading ? 0.7 : 1,
              cursor: isLoading ? 'not-allowed' : 'pointer'
            }}
          >
            {isLoading ? '인증 확인 중...' : '로그인'}
          </button>
        </form>

        <div style={styles.footer}>
          <span style={styles.footerText}>테스트 계정 정보: admin@example.com / admin1234</span>
        </div>
      </div>
    </div>
  );
}

const styles = {
  container: {
    display: 'flex',
    justifyContent: 'center',
    alignItems: 'center',
    width: '100vw',
    height: '100vh',
    backgroundColor: 'var(--bg)',
  },
  card: {
    width: '420px',
    padding: '40px 36px',
    backgroundColor: 'var(--surface)',
    borderRadius: 'var(--radius)',
    boxShadow: 'var(--shadow)',
    border: '1px solid var(--line)',
  },
  logoSection: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '12px',
    marginBottom: '28px',
  },
  mark: {
    width: '40px',
    height: '40px',
    borderRadius: '11px',
    background: 'linear-gradient(135deg, var(--brand), #0a6ed1)',
    display: 'grid',
    placeItems: 'center',
    border: '1px solid rgba(57,211,230,.35)',
    boxShadow: '0 0 18px rgba(57,211,230,.35)',
  },
  svg: {
    width: '22px',
    height: '22px',
  },
  brandText: {
    textAlign: 'left' as const,
  },
  brandTitle: {
    display: 'block',
    fontSize: '16px',
    letterSpacing: '-.2px',
    color: 'var(--brand)',
    fontWeight: '700',
  },
  brandSub: {
    fontSize: '10px',
    color: 'var(--ink-soft)',
    letterSpacing: '.6px',
    fontWeight: '600',
  },
  title: {
    fontSize: '22px',
    fontWeight: '700',
    color: 'var(--ink)',
    letterSpacing: '-.5px',
    marginBottom: '6px',
    textAlign: 'center' as const,
  },
  subtitle: {
    fontSize: '13px',
    color: 'var(--ink-soft)',
    marginBottom: '32px',
    textAlign: 'center' as const,
  },
  form: {
    display: 'flex',
    flexDirection: 'column' as const,
    gap: '18px',
  },
  inputGroup: {
    display: 'flex',
    flexDirection: 'column' as const,
    gap: '6px',
  },
  label: {
    fontSize: '12.5px',
    fontWeight: '600',
    color: 'var(--ink-soft)',
  },
  input: {
    width: '100%',
    border: '1px solid var(--line)',
    backgroundColor: 'var(--surface-2)',
    borderRadius: '10px',
    padding: '11px 13px',
    fontSize: '14px',
    outline: 'none',
    transition: 'border-color .15s, box-shadow .15s',
  },
  button: {
    width: '100%',
    backgroundColor: 'var(--brand)',
    color: 'var(--on-brand)',
    padding: '12px',
    borderRadius: '11px',
    fontSize: '14px',
    fontWeight: '600',
    border: 'none',
    transition: 'background .15s, transform .05s',
    marginTop: '6px',
  },
  errorContainer: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    backgroundColor: 'var(--red-soft)',
    border: '1px solid rgba(188, 75, 56, 0.15)',
    borderRadius: '8px',
    padding: '10px 12px',
  },
  errorIcon: {
    width: '16px',
    height: '16px',
    flexShrink: 0,
  },
  errorText: {
    color: 'var(--red)',
    fontSize: '12.5px',
    fontWeight: '500',
  },
  footer: {
    marginTop: '24px',
    textAlign: 'center' as const,
    borderTop: '1px solid var(--line-soft)',
    paddingTop: '16px',
  },
  footerText: {
    fontSize: '11px',
    color: 'var(--ink-faint)',
    fontWeight: '500',
  }
};
