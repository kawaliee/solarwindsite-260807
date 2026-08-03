import { useState, useEffect, useRef } from 'react'
import { conversationsApi, type Message, type ConversationDetail } from '../../api/client'
import SourceViewer from './SourceViewer'

interface ChatViewProps {
  conversationId: string | null;
  onConversationCreated: (id: string) => void;
}

export default function ChatView({ conversationId, onConversationCreated }: ChatViewProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [useInternalDocs, setUseInternalDocs] = useState(true);
  const [isLoading, setIsLoading] = useState(false);
  const [currentConvId, setCurrentConvId] = useState<string | null>(conversationId);
  const [selectedSource, setSelectedSource] = useState<any>(null);
  const [isViewerOpen, setIsViewerOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // 대화 로드
  useEffect(() => {
    setCurrentConvId(conversationId);
    if (conversationId) {
      conversationsApi.get(conversationId)
        .then((data: ConversationDetail) => {
          setMessages(data.messages || []);
          setUseInternalDocs(data.use_internal_docs);
        })
        .catch(() => setMessages([]));
    } else {
      setMessages([]);
    }
  }, [conversationId]);

  // 스크롤 하단 유지
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  // textarea 자동 높이
  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    const ta = e.target;
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 120) + 'px';
  };

  // 메시지 전송
  const handleSend = async () => {
    const content = input.trim();
    if (!content || isLoading) return;

    setInput('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }

    // 사용자 메시지를 즉시 표시
    const tempUserMsg: Message = {
      id: 'temp-' + Date.now(),
      conversation: currentConvId || '',
      role: 'user',
      content,
      used_internal_docs: false,
      model: '',
      status: 'done',
      created_at: new Date().toISOString(),
      sources: [],
    };
    setMessages(prev => [...prev, tempUserMsg]);

    setIsLoading(true);

    try {
      let convId = currentConvId;

      // 새 대화 생성
      if (!convId) {
        const newConv = await conversationsApi.create({
          title: content.slice(0, 50),
        });
        convId = newConv.id;
        setCurrentConvId(convId);
      }

      // 임시 AI 메시지 생성
      const tempAiMsgId = 'ai-' + Date.now();
      const tempAiMsg: Message = {
        id: tempAiMsgId,
        conversation: convId!,
        role: 'assistant',
        content: '',
        used_internal_docs: false,
        model: '',
        status: 'done',
        created_at: new Date().toISOString(),
        sources: [],
      };
      
      setMessages(prev => [...prev.filter(m => m.id !== tempUserMsg.id), 
        { ...tempUserMsg, id: 'user-' + Date.now(), conversation: convId! },
        tempAiMsg
      ]);

      // 메시지 전송 → AI 응답 수신 (스트리밍)
      await conversationsApi.sendMessageStream(
        convId!,
        { content, use_internal_docs: useInternalDocs, stream: true },
        (chunk) => {
          if (chunk.type === 'sources') {
            setMessages(prev => prev.map(m => m.id === tempAiMsgId ? { ...m, sources: chunk.sources } : m));
          } else if (chunk.content) {
            setMessages(prev => prev.map(m => m.id === tempAiMsgId ? { ...m, content: m.content + chunk.content } : m));
          }
        }
      );

      // AI 답변 스트리밍 완료 후 부모 상태 업데이트 (사이드바 목록 갱신 및 대화 활성화)
      if (!currentConvId && convId) {
        onConversationCreated(convId);
      }
    } catch (error) {
      console.error('Send message error:', error);
      const errorMsg: Message = {
        id: 'error-' + Date.now(),
        conversation: currentConvId || '',
        role: 'assistant',
        content: '죄송합니다. 메시지 전송 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.',
        used_internal_docs: false,
        model: 'error',
        status: 'failed',
        created_at: new Date().toISOString(),
        sources: [],
      };
      setMessages(prev => [...prev, errorMsg]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="chat-wrap">
      {/* 메시지 영역 */}
      <div className="chat-scroll" ref={scrollRef}>
        <div className="chat-inner">
          {messages.length === 0 && !isLoading && (
            <div style={{ textAlign: 'center', padding: '80px 20px', color: 'var(--ink-faint)' }}>
              <div style={{ fontSize: 40, marginBottom: 16 }}>💬</div>
              <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--ink-soft)', marginBottom: 8 }}>
                재생E AI Agent에 질문하세요
              </div>
              <div style={{ fontSize: 13, lineHeight: 1.6, maxWidth: 400, margin: '0 auto' }}>
                사내 자료를 기반으로 질의응답이 가능합니다.<br/>
                예: "풍력 PPA 계약에서 우리가 관철했던 핵심 조건은?"
              </div>
            </div>
          )}

          {messages.map(msg => (
            <div key={msg.id} className={`msg ${msg.role === 'user' ? 'user' : 'ai'} fade-in`}>
              {msg.role === 'user' ? (
                <div className="ava">관</div>
              ) : (
                <div className="ava">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M12 2a7 7 0 0 0-7 7c0 3 2 5 4 7l3 6 3-6c2-2 4-4 4-7a7 7 0 0 0-7-7Z"/>
                  </svg>
                </div>
              )}
              <div className="body">
                <div className="who">
                  {msg.role === 'user' ? '관리자' : '재생E AI Agent'}
                  {msg.used_internal_docs && (
                    <span className="ref-tag">사내 문서 참조</span>
                  )}
                </div>
                <div className="text">
                  {msg.content.split('\n').map((line, i) => {
                    if (line.startsWith('- ') || line.startsWith('* ')) {
                      return <li key={i} style={{ marginLeft: 16, marginBottom: 4 }}>{line.slice(2)}</li>;
                    }
                    return line ? <p key={i}>{line}</p> : <br key={i} />;
                  })}
                </div>

                {/* 출처 칩 */}
                {msg.sources && msg.sources.length > 0 && (
                  <div className="sources">
                    <div className="s-lab">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                        <path d="M14 2v6h6"/>
                      </svg>
                      참조한 사내 자료 {msg.sources.length}건
                    </div>
                    <div className="chips">
                      {msg.sources.map(src => (
                        <span
                          key={src.id}
                          className="chip"
                          onClick={() => {
                            setSelectedSource(src);
                            setIsViewerOpen(true);
                          }}
                        >
                          <span className="dot"></span>
                          {src.short_label || src.display_title?.slice(0, 4) + '…'}
                          <span className="tip">
                            {src.display_title}
                            {src.location_label && (
                              <span className="tp-meta">{src.location_label}</span>
                            )}
                          </span>
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          ))}

          {/* 로딩 표시 */}
          {isLoading && (
            <div className="msg ai fade-in">
              <div className="ava">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 2a7 7 0 0 0-7 7c0 3 2 5 4 7l3 6 3-6c2-2 4-4 4-7a7 7 0 0 0-7-7Z"/>
                </svg>
              </div>
              <div className="body">
                <div className="who">재생E AI Agent</div>
                <div className="loading-dots">
                  <span></span><span></span><span></span>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 입력 영역 (Composer) */}
      <div className="composer">
        <div className="composer-inner">
          <div className="ref-toggle">
            <label className="switch">
              <input
                type="checkbox"
                checked={useInternalDocs}
                onChange={e => setUseInternalDocs(e.target.checked)}
              />
              <span className="track"></span>
              <span className="knob"></span>
            </label>
            <div>
              <span className="rt-txt">사내 문서 참조</span>
              <span className="rt-sub"> · 이 대화에서 부서 자료를 검색해 답변에 반영합니다</span>
            </div>
          </div>

          <div className="input-box">
            <textarea
              ref={textareaRef}
              rows={1}
              value={input}
              onChange={handleInputChange}
              onKeyDown={handleKeyDown}
              placeholder="질문을 입력하세요. 예: 풍력 PPA 계약에서 우리가 관철했던 핵심 조건은?"
              disabled={isLoading}
            />
            <button className="icon-btn" title="파일 첨부 (PDF · Word · Excel)">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
              </svg>
            </button>
            <button className="send-btn" title="전송" onClick={handleSend} disabled={isLoading || !input.trim()}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/>
              </svg>
            </button>
          </div>

          <div className="composer-foot">
            답변은 참조 자료에 근거하며, 중요한 의사결정 전 원본 확인을 권장합니다.
          </div>
        </div>
      </div>
      <SourceViewer
        isOpen={isViewerOpen}
        onClose={() => setIsViewerOpen(false)}
        source={selectedSource}
      />
    </div>
  );
}
