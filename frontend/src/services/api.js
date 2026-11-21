/**
 * API client for interacting with the DarGlobal & Wasalt backend and Google ADK services.
 */

const BASE_URL = import.meta.env.VITE_API_URL || '';

/**
 * Health check endpoint.
 */
export async function checkHealth() {
  const response = await fetch(`${BASE_URL}/api/health`);
  if (!response.ok) {
    throw new Error(`Health check failed with status: ${response.status}`);
  }
  return response.json();
}

/**
 * Creates a fresh, isolated Google ADK conversation session.
 */
export async function createSession(userId = 'default_user') {
  const response = await fetch(`${BASE_URL}/api/sessions?user_id=${encodeURIComponent(userId)}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
  });
  if (!response.ok) {
    throw new Error(`Failed to create ADK session: ${response.status}`);
  }
  return response.json();
}

/**
 * Retrieves session metadata and event turn count.
 */
export async function getSession(sessionId, userId = 'default_user') {
  const response = await fetch(`${BASE_URL}/api/sessions/${encodeURIComponent(sessionId)}?user_id=${encodeURIComponent(userId)}`);
  if (!response.ok) {
    throw new Error(`Failed to inspect session ${sessionId}: ${response.status}`);
  }
  return response.json();
}

/**
 * Deletes an active session and purges in-memory event state.
 */
export async function deleteSession(sessionId, userId = 'default_user') {
  const response = await fetch(`${BASE_URL}/api/sessions/${encodeURIComponent(sessionId)}?user_id=${encodeURIComponent(userId)}`, {
    method: 'DELETE',
  });
  if (!response.ok) {
    throw new Error(`Failed to delete session: ${response.status}`);
  }
  return response.json();
}

/**
 * Streams real-time ADK events, thinking tokens, and tool executions over Server-Sent Events (SSE).
 * Uses fetch() + ReadableStream for compatibility with POST payloads.
 *
 * @param {string} message - User query prompt
 * @param {string|null} sessionId - Active ADK session identifier
 * @param {function} onEvent - Callback invoked on each normalized SSE event { type, ... }
 * @param {AbortSignal} signal - AbortController signal for cancellation
 */
export async function streamChatMessage(message, sessionId = null, onEvent, signal = null) {
  // Enforce a 30-second connection timeout guard to prevent indefinite hanging
  const timeoutController = new AbortController();
  const timeoutId = setTimeout(() => {
    timeoutController.abort(new Error('Agent request timed out after 30 seconds.'));
  }, 30000);

  // Link external abort signal if provided
  if (signal) {
    signal.addEventListener('abort', () => timeoutController.abort(signal.reason), { once: true });
  }

  let reader = null;
  try {
    const response = await fetch(`${BASE_URL}/api/chat/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        message,
        session_id: sessionId,
        user_id: 'default_user',
        streaming: true,
      }),
      signal: timeoutController.signal,
    });

    if (!response.ok) {
      throw new Error(`Chat stream failed with status: ${response.status}`);
    }

    reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const blocks = buffer.split('\n\n');
      buffer = blocks.pop() || ''; // Keep incomplete trailing chunk

      for (const block of blocks) {
        const trimmed = block.trim();
        // Skip empty lines and SSE keepalive comments (e.g. : keepalive)
        if (!trimmed || trimmed.startsWith(':')) continue;

        if (trimmed.startsWith('data: ')) {
          const jsonStr = trimmed.slice(6);
          try {
            const event = JSON.parse(jsonStr);
            onEvent(event);
          } catch (parseErr) {
            console.error('Failed to parse SSE payload:', parseErr, jsonStr);
          }
        }
      }
    }

    // Process any residual data remaining in buffer after stream closes
    if (buffer && buffer.trim()) {
      const trimmed = buffer.trim();
      if (trimmed.startsWith('data: ')) {
        try {
          const event = JSON.parse(trimmed.slice(6));
          onEvent(event);
        } catch (parseErr) {
          console.error('Failed to parse trailing SSE payload:', parseErr, trimmed);
        }
      }
    }
  } finally {
    clearTimeout(timeoutId);
    if (reader) {
      try {
        reader.releaseLock();
      } catch (e) {
        // ignore
      }
    }
  }
}

/**
 * Non-streaming chat endpoint (fallback & automated testing).
 */
export async function sendChatMessage(message, conversationId = null, history = []) {
  const response = await fetch(`${BASE_URL}/api/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      message,
      conversation_id: conversationId,
      history,
    }),
  });

  if (!response.ok) {
    throw new Error(`Chat request failed with status: ${response.status}`);
  }

  return response.json();
}
