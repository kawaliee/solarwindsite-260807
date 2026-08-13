/**
 * 화면 단위 오류 경계
 * ---------------------------------------------------------------
 * 렌더 도중 예외가 나면 React는 트리 전체를 걷어낸다. 경계가 없으면
 * 화면 한 곳의 실수가 앱 전체를 흰 화면으로 만들고, 사용자에게는 아무
 * 단서도 남지 않는다(실제로 입지 검토 결과에서 그렇게 됐다).
 *
 * 여기서 막으면 사이드바와 다른 메뉴는 살아 있고, 무엇이 터졌는지도
 * 눈에 보인다. 오류를 삼켜 감추는 것이 아니라 번지지 않게 가둔다.
 *
 * key로 화면 이름을 주면 메뉴를 옮길 때 경계가 새로 만들어져,
 * 한 번 터진 화면이 영영 막혀 있지 않는다.
 */
import { Component, type ErrorInfo, type ReactNode } from 'react'

type Props = { name: string; children: ReactNode }
type State = { error: Error | null }

export default class ViewBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // 콘솔에는 원문을 그대로 남긴다 — 재현 없이 원인을 좁힐 단서가 된다.
    console.error(`[${this.props.name}] 렌더 중 오류`, error, info.componentStack)
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <div className="view-error">
        <b>이 화면을 그리는 중 오류가 발생했습니다.</b>
        <p>
          다른 메뉴는 그대로 쓸 수 있습니다. 아래 내용을 알려주시면 원인을
          좁히는 데 도움이 됩니다.
        </p>
        <code>{error.message || String(error)}</code>
        <button type="button" className="ws-btn2"
          onClick={() => this.setState({ error: null })}>다시 시도</button>
      </div>
    )
  }
}
