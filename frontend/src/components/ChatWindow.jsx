import React, { useState, useRef, useEffect } from 'react';
import MessageBubble from './MessageBubble';

export default function ChatWindow({ messages, loading, onSendMessage }) {
  const [input, setInput] = useState('');
  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, loading]);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!input.trim() || loading) return;
    onSendMessage(input);
    setInput('');
  };

  return (
    <div className="flex flex-col h-[calc(100vh-65px)] w-full max-w-5xl mx-auto border-x border-[#E0E0E0] bg-[#FFFFFF]">
      {/* Messages Viewport */}
      <div className="flex-1 overflow-y-auto p-6 space-y-4 bg-[#F8FAFC]">
        {messages.map((msg, index) => (
          <MessageBubble key={index} message={msg} />
        ))}

        {loading && (
          <div className="flex items-center space-x-2 text-xs font-mono text-neutral-500 py-2">
            <span className="inline-block w-2 h-2 bg-[#2563EB] animate-pulse"></span>
            <span>SEARCHING CHROMADB &amp; REASONING...</span>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input Form Bar */}
      <form onSubmit={handleSubmit} className="border-t border-[#E0E0E0] bg-white p-4 flex gap-2">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about luxury villas, penthouses, prices, or amenities in Riyadh/Dubai..."
          disabled={loading}
          className="flex-1 px-4 py-3 border border-[#E0E0E0] bg-white text-sm text-[#0F172A] placeholder-neutral-400 focus:outline-none focus:border-[#2563EB] font-sans"
        />
        <button
          type="submit"
          disabled={loading || !input.trim()}
          className="px-6 py-3 bg-[#0F172A] text-white text-xs font-mono font-bold uppercase tracking-wider hover:bg-[#2563EB] disabled:bg-neutral-300 disabled:cursor-not-allowed border border-[#0F172A] transition-colors"
        >
          Send
        </button>
      </form>
    </div>
  );
}
