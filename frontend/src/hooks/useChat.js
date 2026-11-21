import { useState, useCallback, useRef, useEffect } from 'react';
import { createSession, streamChatMessage, sendChatMessage } from '../services/api';

const SESSION_STORAGE_KEY = 'darglobal_adk_session_id';

const INITIAL_GREETING = {
  role: 'assistant',
  content: 'Welcome to the DarGlobal & Wasalt Real Estate AI Advisor. How can I assist your luxury property search today?',
  matched_properties: [],
  activity: null,
  isStreaming: false,
  timestamp: new Date().toISOString(),
};

export function useChat() {
  const [messages, setMessages] = useState([INITIAL_GREETING]);
  const [sessionId, setSessionId] = useState(() => {
    return localStorage.getItem(SESSION_STORAGE_KEY) || null;
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const abortControllerRef = useRef(null);

  // Initialize session on mount if absent
  useEffect(() => {
    if (!sessionId) {
      createSession()
        .then((data) => {
          if (data && data.session_id) {
            setSessionId(data.session_id);
            localStorage.setItem(SESSION_STORAGE_KEY, data.session_id);
          }
        })
        .catch((err) => {
          console.warn('Initial session creation fallback:', err);
        });
    }
  }, [sessionId]);

  /**
   * Resets the conversation and establishes a fresh, isolated ADK session.
   */
  const createNewSession = useCallback(async () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }

    setLoading(true);
    setError(null);

    try {
      const data = await createSession();
      const newSid = data.session_id;
      setSessionId(newSid);
      localStorage.setItem(SESSION_STORAGE_KEY, newSid);
      setMessages([
        {
          ...INITIAL_GREETING,
          timestamp: new Date().toISOString(),
        },
      ]);
    } catch (err) {
      console.error('Failed to create new ADK session:', err);
      // Fallback: generate local UUID session
      const fallbackId = `session_${Date.now()}`;
      setSessionId(fallbackId);
      localStorage.setItem(SESSION_STORAGE_KEY, fallbackId);
      setMessages([
        {
          ...INITIAL_GREETING,
          timestamp: new Date().toISOString(),
        },
      ]);
    } finally {
      setLoading(false);
    }
  }, []);

  /**
   * Sends a user prompt and streams the live ADK execution events and response.
   */
  const sendMessage = useCallback(
    async (content) => {
      const trimmed = content.trim();
      if (!trimmed || loading) return;

      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      const controller = new AbortController();
      abortControllerRef.current = controller;

      const userMessage = {
        role: 'user',
        content: trimmed,
        timestamp: new Date().toISOString(),
      };

      // Optimistically push user message and placeholder assistant message
      setMessages((prev) => [
        ...prev,
        userMessage,
        {
          role: 'assistant',
          content: '',
          isStreaming: true,
          activity: {
            currentAgent: 'real_estate_orchestrator',
            steps: [],
            totalDurationMs: 0,
            completed: false,
          },
          matched_properties: [],
          timestamp: new Date().toISOString(),
        },
      ]);

      setLoading(true);
      setError(null);

      let currentSessionId = sessionId;

      try {
        await streamChatMessage(
          trimmed,
          currentSessionId,
          (event) => {
            if (!event || !event.type) return;

            setMessages((prev) => {
              if (prev.length === 0) return prev;
              const next = [...prev];
              const lastIdx = next.length - 1;
              const lastMsg = { ...next[lastIdx] };

              if (lastMsg.role !== 'assistant') return prev;

              const activity = { ...(lastMsg.activity || { currentAgent: 'real_estate_orchestrator', steps: [] }) };
              const steps = [...(activity.steps || [])];

              switch (event.type) {
                case 'session':
                  if (event.session_id && event.session_id !== currentSessionId) {
                    currentSessionId = event.session_id;
                    setSessionId(event.session_id);
                    localStorage.setItem(SESSION_STORAGE_KEY, event.session_id);
                  }
                  break;

                case 'agent_start':
                  activity.currentAgent = event.author || 'real_estate_orchestrator';
                  if (steps.length === 0 || steps[steps.length - 1].type !== 'agent_start') {
                    steps.push({
                      type: 'agent_start',
                      author: event.author || 'real_estate_orchestrator',
                      isRunning: true,
                      timestamp: event.timestamp || Date.now(),
                    });
                  }
                  break;

                case 'agent_change':
                  // Mark prior agent_start step finished
                  steps.forEach((s) => {
                    if (s.type === 'agent_start') s.isRunning = false;
                  });
                  activity.currentAgent = event.to_agent;
                  steps.push({
                    type: 'agent_change',
                    from_agent: event.from_agent,
                    to_agent: event.to_agent,
                    timestamp: event.timestamp || Date.now(),
                  });
                  break;

                case 'thinking':
                  if (steps.length > 0 && steps[steps.length - 1].type === 'thinking' && event.partial) {
                    steps[steps.length - 1] = {
                      ...steps[steps.length - 1],
                      text: steps[steps.length - 1].text + event.text,
                    };
                  } else {
                    steps.push({
                      type: 'thinking',
                      author: event.author,
                      text: event.text,
                      timestamp: event.timestamp || Date.now(),
                    });
                  }
                  break;

                case 'tool_call':
                  steps.push({
                    type: 'tool_call',
                    id: event.id,
                    author: event.author,
                    name: event.name,
                    args: event.args,
                    isRunning: true,
                    timestamp: event.timestamp || Date.now(),
                  });
                  break;

                case 'tool_result': {
                  const matchIdx = steps.findLastIndex(
                    (s) => s.type === 'tool_call' && (s.id === event.id || s.name === event.name)
                  );
                  if (matchIdx !== -1) {
                    steps[matchIdx] = {
                      ...steps[matchIdx],
                      isRunning: false,
                      duration_ms: event.duration_ms,
                      response_preview: event.response_preview,
                    };
                  } else {
                    steps.push({
                      type: 'tool_result',
                      id: event.id,
                      author: event.author,
                      name: event.name,
                      duration_ms: event.duration_ms,
                      response_preview: event.response_preview,
                      isRunning: false,
                    });
                  }
                  break;
                }

                case 'text_delta':
                  steps.forEach((s) => {
                    if (s.type === 'agent_start') s.isRunning = false;
                  });
                  lastMsg.content = (lastMsg.content || '') + event.text;
                  break;

                case 'final_response':
                  steps.forEach((s) => {
                    if (s.type === 'agent_start' || s.type === 'tool_call') s.isRunning = false;
                  });
                  lastMsg.content = event.text;
                  activity.totalDurationMs = event.total_duration_ms;
                  break;

                case 'error':
                  setError(event.error_message || 'An error occurred during agent execution.');
                  lastMsg.content =
                    lastMsg.content ||
                    'I encountered an error while processing your request. Please try again or create a new session.';
                  break;

                case 'done':
                  lastMsg.isStreaming = false;
                  activity.completed = true;
                  break;

                default:
                  break;
              }

              activity.steps = steps;
              lastMsg.activity = activity;
              next[lastIdx] = lastMsg;
              return next;
            });
          },
          controller.signal
        );
      } catch (err) {
        if (err.name === 'AbortError') {
          console.log('Chat stream aborted by user.');
        } else {
          console.warn('Stream failed, attempting non-streaming fallback:', err);
          // Automatic seamless fallback to non-streaming endpoint
          try {
            const fallbackRes = await sendChatMessage(trimmed, currentSessionId);
            setMessages((prev) => {
              const next = [...prev];
              const lastIdx = next.length - 1;
              if (lastIdx >= 0 && next[lastIdx].role === 'assistant') {
                next[lastIdx] = {
                  ...next[lastIdx],
                  isStreaming: false,
                  content: fallbackRes.reply || 'Information retrieved.',
                  matched_properties: fallbackRes.matched_properties || [],
                  activity: {
                    currentAgent: 'real_estate_orchestrator',
                    steps: [],
                    totalDurationMs: 500,
                    completed: true,
                  },
                };
              }
              return next;
            });
          } catch (fallbackErr) {
            console.error('Fallback chat error:', fallbackErr);
            setError(fallbackErr.message || 'Connection interrupted.');
            setMessages((prev) => {
              const next = [...prev];
              const lastIdx = next.length - 1;
              if (lastIdx >= 0 && next[lastIdx].role === 'assistant') {
                next[lastIdx] = {
                  ...next[lastIdx],
                  isStreaming: false,
                  content:
                    next[lastIdx].content ||
                    'I apologize, but I encountered an error communicating with the agent. Please try again.',
                };
              }
              return next;
            });
          }
        }
      } finally {
        setLoading(false);
        abortControllerRef.current = null;
        setMessages((prev) => {
          const next = [...prev];
          const lastIdx = next.length - 1;
          if (lastIdx >= 0 && next[lastIdx].role === 'assistant') {
            next[lastIdx] = { ...next[lastIdx], isStreaming: false };
          }
          return next;
        });
      }
    },
    [sessionId, loading]
  );

  return {
    messages,
    loading,
    error,
    sessionId,
    sendMessage,
    createNewSession,
  };
}
