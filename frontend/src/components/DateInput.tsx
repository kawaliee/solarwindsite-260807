/**
 * 연·월·일 분리 입력
 * ---------------------------------------------------------------
 * 브라우저 기본 <input type="date">를 대신한다.
 *
 * 기본 위젯의 연도 칸은 자릿수가 고정되어 있지 않아 '201666' 같은 값이
 * 그대로 들어가고, 4자리를 채워도 월로 넘어가지 않는다. 발전사업허가일은
 * 조례 시행일과 비교해 경과조치 해당 여부를 가르는 값이라, 연도가 한 자리
 * 잘못 들어가면 판정이 통째로 뒤집힌다.
 *
 * 그래서 칸을 셋으로 나눈다.
 *   · 연 4자리를 채우면 월로, 월이 정해지면 일로 커서가 저절로 간다
 *   · 칸이 나뉘어 있으므로 Tab/Shift+Tab이 그대로 칸 이동이 된다
 *   · Backspace로 빈 칸을 지우면 앞 칸으로 돌아간다
 *
 * 월은 2~9를 누른 순간 확정된다(10월 이상이 될 수 없다). 0이나 1은 뒤에
 * 숫자가 더 올 수 있으므로 기다린다. 일도 같은 이유로 4~9에서 확정한다.
 */
import { useEffect, useRef, useState } from 'react'

type Props = {
  /** 'YYYY-MM-DD' 또는 '' */
  value: string
  /** 완성되고 유효할 때만 'YYYY-MM-DD', 그 외에는 '' */
  onChange: (v: string) => void
  id?: string
}

type Seg = { y: string; m: string; d: string }

const EMPTY: Seg = { y: '', m: '', d: '' }

function parse(v: string): Seg {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v || '')
  return m ? { y: m[1], m: m[2], d: m[3] } : EMPTY
}

/** 그 달의 마지막 날. 연도를 모르면 윤년을 인정해 29일까지 둔다. */
function lastDay(y: string, m: string): number {
  const mi = Number(m)
  if (!mi || mi < 1 || mi > 12) return 31
  const yi = Number(y)
  if (!yi) return [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mi - 1]
  return new Date(yi, mi, 0).getDate()
}

function pad(v: string): string {
  return v.length === 1 ? `0${v}` : v
}

export default function DateInput({ value, onChange, id }: Props) {
  const [seg, setSeg] = useState<Seg>(() => parse(value))
  const wrapRef = useRef<HTMLSpanElement>(null)
  const yRef = useRef<HTMLInputElement>(null)
  const mRef = useRef<HTMLInputElement>(null)
  const dRef = useRef<HTMLInputElement>(null)

  // 커서를 옮기면 blur가 곧바로 뜨는데, 그 시점에는 아직 setSeg가 반영되기
  // 전이라 seg가 한 박자 늦다. 그 값으로 보정하면 방금 친 숫자를 덮어쓴다
  // (월에 '0','8'을 잇달아 치면 08이 아니라 01이 되던 문제). 최신 값을
  // ref에 같이 들고 다니며 보정은 이쪽만 본다.
  const segRef = useRef<Seg>(seg)

  //: 우리가 마지막으로 부모에게 보낸 값.
  //
  // 이게 없으면 아직 다 치지 않은 날짜가 통째로 지워진다. 2월에 '2'까지
  // 치면 2월 2일이 되어 값이 나가고, 이어서 '9'를 쳐 29일이 되면 그 해가
  // 평년일 때 없는 날이라 값이 ''로 돌아간다. 그 ''를 바깥에서 온 초기화로
  // 오해하면 연·월까지 싹 비워 버린다(실측). 내가 보낸 값이면 무시한다.
  const sentRef = useRef<string>(value)

  // 바깥에서 값이 바뀐 경우(초기화 등)만 따라간다. 타이핑 중에는 화면의
  // 조각이 진실이므로, 같은 날짜를 가리키면 건드리지 않는다.
  useEffect(() => {
    if (value === sentRef.current) return
    sentRef.current = value
    const next = parse(value)
    setSeg(prev => {
      if (prev.y === next.y && prev.m === next.m && prev.d === next.d) return prev
      segRef.current = next
      return next
    })
  }, [value])

  /** 세 칸이 다 차고 실제로 있는 날짜일 때만 부모에게 값을 준다. */
  const emit = (s: Seg) => {
    const { y, m, d } = s
    let next = ''
    if (y.length === 4 && m && d) {
      const mm = Number(m); const dd = Number(d)
      if (mm >= 1 && mm <= 12 && dd >= 1 && dd <= lastDay(y, pad(m))) {
        next = `${y}-${pad(m)}-${pad(d)}`
      }
    }
    if (next === sentRef.current) return
    sentRef.current = next
    onChange(next)
  }

  const put = (next: Seg, focus?: 'm' | 'd') => {
    segRef.current = next
    setSeg(next)
    emit(next)
    if (focus === 'm') mRef.current?.select()
    if (focus === 'd') dRef.current?.select()
  }

  const digits = (v: string) => v.replace(/\D/g, '')

  /**
   * 칸이 다 찬 뒤에 들어온 숫자는 버리지 않고 다음 칸으로 넘긴다.
   *
   * 커서를 옮기는 것만으로는 부족하다. 빠르게 연달아 치면 네 번째 글자의
   * 처리가 끝나기 전에 다섯 번째 글자가 이미 연도 칸에 들어와 있어, 자릿수
   * 제한에 걸려 조용히 사라진다(실측). 넘쳐난 글자를 다음 칸에 밀어 넣으면
   * '20160830'을 쉬지 않고 쳐도 그대로 채워진다.
   */
  const onYear = (raw: string) => {
    const all = digits(raw).slice(0, 6)
    const y = all.slice(0, 4)
    const rest = all.slice(4)
    if (y.length < 4) { put({ ...seg, y }); return }
    if (rest) { applyMonth(rest, { ...seg, y }); return }
    put({ ...seg, y }, 'm')
  }

  const applyMonth = (raw: string, base: Seg) => {
    const all = digits(raw).slice(0, 4)
    if (!all) { put({ ...base, m: '' }); return }
    let m = all.slice(0, 2)
    const rest = all.slice(2)
    if (all.length === 1) {
      // 0이나 1 뒤에는 숫자가 더 올 수 있다(01·10·11·12). 2~9는 그럴 수 없다.
      if (Number(all) < 2) { put({ ...base, m: all }); return }
      m = pad(all)
    }
    if (rest) { applyDay(rest, { ...base, m }); return }
    put({ ...base, m }, 'd')
  }

  const applyDay = (raw: string, base: Seg) => {
    const all = digits(raw).slice(0, 2)
    // 4~9로 시작하면 두 자리가 될 수 없다(40일은 없다).
    if (all.length === 1 && Number(all) >= 4) { put({ ...base, d: pad(all) }); return }
    put({ ...base, d: all })
  }

  const onMonth = (raw: string) => applyMonth(raw, seg)
  const onDay = (raw: string) => applyDay(raw, seg)

  /** 빈 칸에서 Backspace, 또는 칸 끝에서 방향키를 누르면 이웃 칸으로 */
  const nav = (e: React.KeyboardEvent<HTMLInputElement>,
               prev: HTMLInputElement | null, next: HTMLInputElement | null) => {
    const el = e.currentTarget
    const at = el.selectionStart ?? 0
    if (e.key === 'Backspace' && !el.value && prev) {
      e.preventDefault(); prev.focus()
      prev.setSelectionRange(prev.value.length, prev.value.length)
    } else if (e.key === 'ArrowLeft' && at === 0 && prev) {
      e.preventDefault(); prev.focus()
      prev.setSelectionRange(prev.value.length, prev.value.length)
    } else if (e.key === 'ArrowRight' && at === el.value.length && next) {
      e.preventDefault(); next.focus(); next.setSelectionRange(0, 0)
    }
  }

  /** '2016-08-30' / '20160830' 을 어느 칸에 붙여넣어도 통째로 받는다 */
  const onPaste = (e: React.ClipboardEvent) => {
    const t = e.clipboardData.getData('text').replace(/\D/g, '')
    if (t.length < 8) return
    e.preventDefault()
    put({ y: t.slice(0, 4), m: t.slice(4, 6), d: t.slice(6, 8) })
  }

  /**
   * 칸을 다 빠져나갔을 때만 보정한다.
   *
   * 칸 사이를 오갈 때도 blur는 뜨지만, 그때 보정하면 아직 다 치지 않은 숫자를
   * 멋대로 완성해 버린다. relatedTarget이 이 안에 있으면 내부 이동이다.
   */
  const onBlur = (e: React.FocusEvent) => {
    if (wrapRef.current?.contains(e.relatedTarget as Node | null)) return
    normalize()
  }

  /** 한 자리로 남은 값을 두 자리로 맞추고, 없는 날짜는 그 달 마지막 날로 */
  const normalize = () => {
    const cur = segRef.current
    const y = cur.y
    let m = cur.m ? pad(cur.m) : ''
    let d = cur.d ? pad(cur.d) : ''
    // '0'만 남은 칸은 완성하지 않고 비운다. 01월로 지레짐작하면 허가일이
    // 조용히 틀어지고, 경과조치 판정이 통째로 뒤집힌다.
    if (m === '00') m = ''
    if (m && Number(m) > 12) m = '12'
    if (d === '00') d = ''
    if (d && m) {
      const max = lastDay(y, m)
      if (Number(d) > max) d = String(max)
    }
    if (m !== cur.m || d !== cur.d) put({ y, m, d })
  }

  const clear = () => { put(EMPTY) }

  const has = !!(seg.y || seg.m || seg.d)

  return (
    <span className="dateseg" ref={wrapRef} onBlur={onBlur} onPaste={onPaste}>
      <input ref={yRef} id={id} value={seg.y} onChange={e => onYear(e.target.value)}
        onKeyDown={e => nav(e, null, mRef.current)}
        onFocus={e => e.currentTarget.select()}
        inputMode="numeric" maxLength={6} placeholder="YYYY"
        aria-label="년" className="dateseg-y" />
      <b>-</b>
      <input ref={mRef} value={seg.m} onChange={e => onMonth(e.target.value)}
        onKeyDown={e => nav(e, yRef.current, dRef.current)}
        onFocus={e => e.currentTarget.select()}
        inputMode="numeric" maxLength={4} placeholder="MM"
        aria-label="월" className="dateseg-s" />
      <b>-</b>
      <input ref={dRef} value={seg.d} onChange={e => onDay(e.target.value)}
        onKeyDown={e => nav(e, mRef.current, null)}
        onFocus={e => e.currentTarget.select()}
        inputMode="numeric" maxLength={2} placeholder="DD"
        aria-label="일" className="dateseg-s" />
      {has && (
        <button type="button" className="dateseg-x" onClick={clear}
          title="지우기" aria-label="날짜 지우기">×</button>
      )}
    </span>
  )
}
