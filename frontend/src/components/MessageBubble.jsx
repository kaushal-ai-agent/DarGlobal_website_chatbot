import React from 'react';
import PropertyCard from './PropertyCard';

export default function MessageBubble({ message }) {
  const isUser = message.role === 'user';

  return (
    <div className={`flex flex-col ${isUser ? 'items-end' : 'items-start'} mb-4 w-full`}>
      <div className="flex items-center space-x-2 mb-1">
        <span className="text-[10px] font-mono text-neutral-500 uppercase">
          {isUser ? 'CLIENT' : 'AI ADVISOR'}
        </span>
        {message.timestamp && (
          <span className="text-[10px] font-mono text-neutral-400">
            {new Date(message.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </span>
        )}
      </div>

      <div
        className={`max-w-2xl p-4 text-sm leading-relaxed border ${
          isUser
            ? 'bg-[#0F172A] text-white border-[#0F172A]'
            : 'bg-white text-[#0F172A] border-[#E0E0E0]'
        }`}
      >
        <p className="whitespace-pre-wrap">{message.content}</p>
      </div>

      {message.matched_properties && message.matched_properties.length > 0 && (
        <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-3 w-full max-w-2xl">
          {message.matched_properties.map((prop, idx) => (
            <PropertyCard key={prop.id || idx} property={prop} />
          ))}
        </div>
      )}
    </div>
  );
}
