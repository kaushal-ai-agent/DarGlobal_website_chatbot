import React from 'react';
import ReactMarkdown from 'react-markdown';
import PropertyCard from './PropertyCard';
import AgentActivityCard from './AgentActivityCard';

export default function MessageBubble({ message }) {
  const isUser = message.role === 'user';

  return (
    <div className={`flex flex-col ${isUser ? 'items-end' : 'items-start'} mb-5 w-full`}>
      {/* Sender Header */}
      <div className="flex items-center space-x-2 mb-1.5">
        <span className="text-[10px] font-mono text-neutral-500 uppercase tracking-wider font-semibold">
          {isUser ? 'CLIENT' : 'AI ADVISOR (ROOT ORCHESTRATOR)'}
        </span>
        {message.timestamp && (
          <span className="text-[10px] font-mono text-neutral-400">
            {new Date(message.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </span>
        )}
      </div>

      {/* Main Bubble Container */}
      <div className={`w-full max-w-3xl flex flex-col ${isUser ? 'items-end' : 'items-start'}`}>
        {/* ADK Execution Activity Timeline (Above Assistant Message) */}
        {!isUser && message.activity && (
          <AgentActivityCard
            activity={message.activity}
            isStreaming={message.isStreaming}
          />
        )}

        {/* Message Content Body */}
        {(message.content || message.isStreaming) && (
          <div
            className={`w-full p-4 text-sm leading-relaxed border ${
              isUser
                ? 'bg-[#0F172A] text-white border-[#0F172A]'
                : 'bg-white text-[#0F172A] border-[#E0E0E0]'
            }`}
          >
            {isUser ? (
              <p className="whitespace-pre-wrap">{message.content}</p>
            ) : (
              <div className="prose prose-sm max-w-none text-[#0F172A]">
                <ReactMarkdown
                  components={{
                    h1: ({ node, ...props }) => <h1 className="text-base font-bold uppercase tracking-tight mb-2 border-b border-[#E0E0E0] pb-1" {...props} />,
                    h2: ({ node, ...props }) => <h2 className="text-sm font-bold uppercase tracking-wide mt-3 mb-1.5" {...props} />,
                    h3: ({ node, ...props }) => <h3 className="text-sm font-semibold mt-2.5 mb-1" {...props} />,
                    p: ({ node, ...props }) => <p className="mb-2 leading-relaxed" {...props} />,
                    ul: ({ node, ...props }) => <ul className="list-disc pl-5 mb-2.5 space-y-1" {...props} />,
                    ol: ({ node, ...props }) => <ol className="list-decimal pl-5 mb-2.5 space-y-1" {...props} />,
                    li: ({ node, ...props }) => <li className="leading-relaxed" {...props} />,
                    a: ({ node, ...props }) => (
                      <a
                        className="text-[#2563EB] hover:underline font-mono text-xs font-semibold"
                        target="_blank"
                        rel="noopener noreferrer"
                        {...props}
                      />
                    ),
                    table: ({ node, ...props }) => (
                      <div className="overflow-x-auto my-3 border border-[#E0E0E0]">
                        <table className="w-full text-xs font-mono border-collapse" {...props} />
                      </div>
                    ),
                    th: ({ node, ...props }) => (
                      <th className="border border-[#E0E0E0] bg-neutral-100 p-2 text-left font-bold text-[#0F172A]" {...props} />
                    ),
                    td: ({ node, ...props }) => (
                      <td className="border border-[#E0E0E0] p-2 text-neutral-800" {...props} />
                    ),
                    code: ({ node, inline, ...props }) => (
                      <code
                        className={`font-mono text-xs ${
                          inline
                            ? 'bg-neutral-100 text-[#0F172A] px-1 py-0.5 border border-[#E0E0E0]'
                            : 'block bg-neutral-900 text-neutral-100 p-3 border border-[#0F172A] my-2 overflow-x-auto'
                        }`}
                        {...props}
                      />
                    ),
                  }}
                >
                  {message.content}
                </ReactMarkdown>

                {message.isStreaming && !message.content && (
                  <span className="inline-block w-2 h-4 bg-[#2563EB] animate-pulse"></span>
                )}
              </div>
            )}
          </div>
        )}

        {/* Matched Property Cards Grid */}
        {message.matched_properties && message.matched_properties.length > 0 && (
          <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-3 w-full">
            {message.matched_properties.map((prop, idx) => (
              <PropertyCard key={prop.id || idx} property={prop} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
