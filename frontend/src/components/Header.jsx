import React from 'react';
import { Plus, RotateCcw } from 'lucide-react';

export default function Header({ sessionId, onNewChat }) {
  const shortSession = sessionId ? `${sessionId.slice(0, 8)}...` : 'INITIALIZING';

  return (
    <header className="w-full bg-[#0F172A] text-white border-b border-[#E0E0E0] px-6 py-3 flex items-center justify-between">
      {/* Brand Title */}
      <div className="flex items-center space-x-3">
        <div className="w-3 h-3 bg-[#2563EB]"></div>
        <h1 className="text-base font-bold tracking-tight uppercase">
          DarGlobal <span className="text-[#2563EB]">&amp;</span> Wasalt AI
        </h1>
        <span className="text-[11px] text-neutral-400 font-mono border border-neutral-700 px-2 py-0.5 ml-2 hidden sm:inline">
          ADK ROOT ORCHESTRATOR
        </span>
      </div>

      {/* Session Controls & Status Indicators */}
      <div className="flex items-center space-x-3 text-xs font-mono">
        {/* Session ID Pill */}
        <span className="hidden md:inline-flex items-center text-neutral-400 border border-neutral-800 bg-neutral-900 px-2.5 py-1 text-[11px]">
          <span className="text-neutral-500 mr-1.5">SESSION:</span>
          <span className="text-neutral-200">{shortSession}</span>
        </span>

        {/* New Session Button */}
        <button
          onClick={onNewChat}
          className="flex items-center space-x-1.5 bg-[#2563EB] text-white px-3 py-1.5 text-xs font-mono font-bold uppercase tracking-wider hover:bg-[#1D4ED8] transition-colors border border-[#2563EB]"
          title="Start fresh isolated ADK session"
        >
          <Plus className="w-3.5 h-3.5" />
          <span>New Chat</span>
        </button>

        {/* Operational Status */}
        <span className="hidden lg:flex items-center text-neutral-300 text-[11px] pl-2 border-l border-neutral-800">
          <span className="w-2 h-2 bg-emerald-500 mr-1.5"></span>
          SYS: OPERATIONAL
        </span>
      </div>
    </header>
  );
}
