import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import ReactMarkdown from 'react-markdown';
import './styles.css';

const API_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '');
const newSessionId = () => `s-${crypto.randomUUID()}`;
const suggestions = [
  { icon: '✳', title: 'Explain a complex idea', prompt: 'Explain how a transformer model works in a way a curious high school student would understand.' },
  { icon: '⌘', title: 'Help me write', prompt: 'Help me write a concise, friendly note asking a teammate for a project update.' },
  { icon: '↗', title: 'Think through a problem', prompt: 'Give me a practical framework for prioritizing a busy week with competing deadlines.' },
];

function App() {
  const [sessionId] = useState(newSessionId);
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [showPrompt, setShowPrompt] = useState(false);
  const [simulateFailure, setSimulateFailure] = useState(false);
  const [sending, setSending] = useState(false);
  const [health, setHealth] = useState('checking');
  const [keysConfigured, setKeysConfigured] = useState(null);
  const feedRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    let active = true;
    const checkHealth = () => fetch(`${API_BASE}/health`).then((r) => r.json()).then((data) => {
      if (!active) return;
      setHealth(data.status === 'ok' ? 'online' : 'offline');
      setKeysConfigured(data.keys_configured);
    }).catch(() => { if (active) setHealth('offline'); });
    checkHealth();
    const timer = window.setInterval(checkHealth, 8000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  useEffect(() => {
    if (feedRef.current) feedRef.current.scrollTop = feedRef.current.scrollHeight;
  }, [messages, sending]);

  async function sendMessage(value = draft) {
    const message = value.trim();
    if (!message || sending) return;
    setDraft('');
    setSending(true);
    setMessages((current) => [...current, { role: 'user', content: message, id: crypto.randomUUID() }]);
    try {
      const headers = { 'Content-Type': 'application/json' };
      if (simulateFailure) headers['X-Simulate-Failure'] = 'key_1=429';
      const response = await fetch(`${API_BASE}/chat`, {
        method: 'POST', headers,
        body: JSON.stringify({ session_id: sessionId, message, ...(systemPrompt.trim() ? { system_prompt: systemPrompt.trim() } : {}) }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `The request could not be completed (${response.status}).`);
      setMessages((current) => [...current, { role: 'assistant', content: data.reply, metadata: data.metadata, id: crypto.randomUUID() }]);
      setHealth('online');
    } catch (error) {
      setMessages((current) => [...current, { role: 'error', content: error.message || 'Something went wrong. Please try again.', id: crypto.randomUUID() }]);
      if (String(error).includes('fetch')) setHealth('offline');
    } finally {
      setSending(false);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }

  function onSubmit(event) { event.preventDefault(); sendMessage(); }

  return (
    <div className="app-shell">
      <aside className="rail">
        <a className="brand-mark" href="#home" aria-label="Lyftr AI home"><span className="brand-glyph">L</span></a>
        <div className="rail-divider" />
        <button className="rail-button active" title="Chat" aria-label="Chat"><span className="chat-icon" /></button>
        <button className="rail-button muted" title="History" aria-label="History"><span className="history-icon">◷</span></button>
        <div className="rail-bottom"><span className="avatar">G</span></div>
      </aside>

      <main className="main-panel" id="home">
        <header className="topbar">
          <div className="crumb"><span className="crumb-label">WORKSPACE</span><span className="crumb-slash">/</span><span className="crumb-current">New conversation</span></div>
          <div className="topbar-right">
            <div className={`connection ${health}`}><span className="status-dot" />{health === 'online' ? 'Backend connected' : health === 'checking' ? 'Connecting…' : 'Backend offline'}</div>
            <button className="icon-button" title="New conversation" aria-label="New conversation" onClick={() => window.location.reload()}><span className="plus-icon">＋</span></button>
          </div>
        </header>

        <section className="workspace">
          <div className="conversation-head">
            <div className="eyebrow"><span className="eyebrow-dot" />LYFTR AI <span className="eyebrow-divider">·</span> CHAT</div>
            <h1>Good ideas start<br className="mobile-break" /> <span>somewhere.</span></h1>
            <p className="subtitle">A little context goes a long way. What’s on your mind?</p>
            <div className="session-chip"><span className="session-chip-icon">◉</span><span>SESSION</span><code>{sessionId.slice(0, 12)}…</code></div>
          </div>

          {showPrompt && <div className="system-prompt-wrap"><label htmlFor="system-prompt">SYSTEM PROMPT <span>· applied to this session</span></label><textarea id="system-prompt" data-testid="system-prompt-input" value={systemPrompt} onChange={(event) => setSystemPrompt(event.target.value)} placeholder="Set the assistant's role, tone, or constraints…" rows={2} /></div>}

          <div className={`chat-area ${messages.length ? 'has-messages' : ''}`} ref={feedRef} aria-live="polite">
            {messages.length === 0 ? (
              <div className="suggestions-grid">
                {suggestions.map((item, index) => <button className={`suggestion-card card-${index + 1}`} key={item.title} onClick={() => sendMessage(item.prompt)}><span className="suggestion-icon">{item.icon}</span><span className="suggestion-title">{item.title}</span><span className="suggestion-arrow">↗</span><span className="suggestion-prompt">{item.prompt}</span></button>)}
              </div>
            ) : (
              <div className="message-list">
                {messages.map((item) => item.role === 'user' ? (
                  <div className="user-row" key={item.id}><div className="user-bubble">{item.content}</div></div>
                ) : item.role === 'error' ? (
                  <div className="error-row" role="alert" key={item.id}><span className="error-mark">!</span><div><strong>Couldn’t get a response</strong><p>{item.content}</p></div></div>
                ) : (
                  <article className="assistant-message" data-testid="assistant-message" key={item.id}>
                    <div className="assistant-avatar"><span>L</span></div>
                    <div className="assistant-content"><div className="assistant-label">LYFTR <span>·</span> ASSISTANT</div><div className="reply-text"><ReactMarkdown>{item.content}</ReactMarkdown></div>
                      <div className="metadata-row">
                        <span className={`meta-pill source-${item.metadata?.resolution_source === 'LLM_CACHE' ? 'cache' : 'live'}`}><span className="meta-dot" /><span data-testid="meta-source">{item.metadata?.resolution_source}</span></span>
                        <span className="meta-pill model-pill"><span className="meta-label">MODEL</span><span data-testid="meta-model">{item.metadata?.model}</span></span>
                        <span className="meta-pill"><span className="meta-label">LATENCY</span><span data-testid="meta-latency">{item.metadata?.latency_ms} ms</span></span>
                        <span className="meta-pill token-pill"><span className="meta-label">TOKENS</span><span data-testid="meta-input-tokens">{item.metadata?.input_tokens}</span><span className="token-separator">→</span><span data-testid="meta-output-tokens">{item.metadata?.output_tokens}</span></span>
                        {item.metadata?.key_id && <span className="key-pill"><span className="key-icon">⌁</span>{item.metadata.key_id}{item.metadata.fallback_attempts > 0 ? ` · ${item.metadata.fallback_attempts} fallback` : ''}</span>}
                      </div>
                    </div>
                  </article>
                ))}
                {sending && <div className="assistant-message pending"><div className="assistant-avatar"><span>L</span></div><div className="typing"><span /><span /><span /></div><span className="thinking-label">THINKING</span></div>}
              </div>
            )}
          </div>

          <div className="composer-wrap">
            <form className="composer" onSubmit={onSubmit}>
              <textarea ref={inputRef} data-testid="chat-input" value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); sendMessage(); } }} placeholder="Ask anything…" rows={1} disabled={sending} aria-label="Your message" />
              <div className="composer-footer">
                <div className="composer-tools">
                  <button type="button" className={`tool-button ${showPrompt ? 'tool-active' : ''}`} onClick={() => setShowPrompt(!showPrompt)} title="Set system prompt"><span className="sparkle">✳</span><span>System prompt</span></button>
                  <span className="tool-separator" />
                  <button type="button" className={`tool-button test-tool ${simulateFailure ? 'tool-active' : ''}`} onClick={() => setSimulateFailure(!simulateFailure)} title="Simulate key_1 rate limit on next requests"><span className="pulse-icon">⌁</span><span>Test failover</span><span className={`switch ${simulateFailure ? 'switch-on' : ''}`}><i /></span></button>
                </div>
                <div className="send-area"><span className="enter-hint">↵ <span>send</span></span><button data-testid="send-button" className="send-button" type="submit" disabled={!draft.trim() || sending} aria-label="Send message"><span>↑</span></button></div>
              </div>
            </form>
            <div className="composer-caption"><span>AI can make mistakes. Check important information.</span><span>{keysConfigured === 2 ? <><i className="caption-check">✓</i> Resilient by design</> : 'MULTI-KEY FALLBACK'}</span></div>
          </div>
        </section>

        <footer className="footer"><span>LYFTR <span className="footer-slash">/</span> FULL STACK ASSIGNMENT</span><span>BUILT FOR BETTER CONVERSATIONS <span className="footer-sparkle">✳</span></span></footer>
      </main>
    </div>
  );
}

createRoot(document.getElementById('root')).render(<React.StrictMode><App /></React.StrictMode>);
