/**
 * 에너지원 프로파일 (화면)
 * ---------------------------------------------------------------
 * 입지검토 화면은 풍력·태양광이 거의 같다. 지도에서 대상을 지정하고, 규제를
 * 판정하고, 면적을 나누고, 보고서를 뽑는 흐름이 동일하기 때문이다.
 *
 * 실제로 갈리는 것은 좁다 — **입력 기하**(풍력은 호기 점, 태양광은 사업구역
 * 면), **자원 항목**(풍황은 풍력만), **아직 등록되지 않은 데이터**(태양광
 * 인허가·법령 시드). 그 차이를 화면 곳곳의 `if (energy === 'SOLAR')`로
 * 흩뿌리지 않고 이 표 한 곳에 모은다.
 *
 * 백엔드 `apps/windsite/energy.py` 와 짝이다. 한쪽만 고치면 화면 문구와
 * 보고서 표제가 어긋나므로 함께 본다.
 */
import type { PickMode } from './SitePicker'

export type EnergyType = 'WIND' | 'SOLAR';

export interface EnergyProfile {
  code: EnergyType;
  /** 사람이 읽는 이름 */
  label: string;
  /** 상단 제목 · 부제 (App.tsx VIEW_META와 같은 값을 쓴다) */
  title: string;
  subtitle: string;
  /** 쓸 수 있는 입력 모드. 순서가 곧 화면 버튼 순서다. */
  modes: PickMode[];
  /** 배치안 저장·불러오기 — 호기 좌표를 남기는 기능이라 배치선 모드 전용 */
  allowPlans: boolean;
  /** 후보지 비교 — 지점 1곳을 후보로 담는 기능 */
  allowCompare: boolean;
  /**
   * 인허가 절차·관련 법령이 DB에 등록돼 있는지.
   * 등록 전에는 빈 목록이 오는데, 그것을 '절차가 없다'로 읽으면 안 되므로
   * 화면이 이유를 명시한다.
   */
  hasPermitSeed: boolean;
  /** 보고서에 풍황 절이 실리는지 — 안내 문구에 쓴다 */
  hasWindResource: boolean;
  /**
   * 구역 검토 결과에 **필지별 채색**이 함께 오는지.
   *
   * 제약도는 규제 레이어를 면적으로 칠하므로 필지 경계와 무관하다.
   * '이 구역의 30%가 조건부'는 알려 주지만 '이 필지가 되는가'는 답하지
   * 못한다. 태양광은 필지가 사업 단위라 그 답이 있어야 후보를 고른다.
   */
  hasScreening: boolean;
  /**
   * 후보 필지 담기·비교를 제공하는지.
   * 태양광은 여러 필지를 견줘 고르는 것이 발굴의 마지막 단계다.
   * 풍력은 같은 자리를 '배치안'으로 저장·비교하므로 별도 탭이 없다.
   */
  hasCandidates: boolean;
}

const WIND: EnergyProfile = {
  code: 'WIND',
  label: '풍력',
  title: '풍력 입지타당성 검토',
  subtitle: '입지 규제 자동 스크리닝 · 인허가 로드맵 · 관련 법령',
  modes: ['layout', 'area'],
  allowPlans: true,
  allowCompare: true,
  hasPermitSeed: true,
  hasWindResource: true,
  hasScreening: false,
  hasCandidates: false,
};

const SOLAR: EnergyProfile = {
  code: 'SOLAR',
  label: '태양광',
  title: '태양광 입지타당성 검토',
  subtitle: '필지 단위 정밀판정 · 태양광 이격거리 조례 · 가용면적 산출',
  // 태양광은 **구역 하나**로 검토한다.
  //
  // 필지 모드를 따로 두었더니 인접 필지 열 개를 고르려면 열 번 눌러야 했고,
  // 화면 범위 스크리닝은 관심 밖 필지까지 절반 넘게 조회했다(실측 55%).
  // 구역을 그리면 그 안 필지가 한 번에 잡히고 조회량도 1/50로 준다.
  // 필지 하나만 볼 일은 채색된 필지를 눌러 정밀판정으로 잇는다.
  //
  // 호기 배치선은 태양광에 성립하지 않으므로 넣지 않는다.
  modes: ['area'],
  allowPlans: false,
  allowCompare: false,
  hasPermitSeed: false,
  hasWindResource: false,
  hasScreening: true,
  hasCandidates: true,
};

export const ENERGY_PROFILES: Record<EnergyType, EnergyProfile> = {
  WIND: WIND,
  SOLAR: SOLAR,
};

export function energyProfile(code: EnergyType = 'WIND'): EnergyProfile {
  return ENERGY_PROFILES[code] ?? WIND;
}
