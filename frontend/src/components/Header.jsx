import React from 'react';

export default function Header() {
  return (
    <header className="w-full bg-[#0F172A] text-white border-b border-[#E0E0E0] px-6 py-4 flex items-center justify-between">
      <div className="flex items-center space-x-3">
        <div className="w-3 h-3 bg-[#2563EB]"></div>
        <h1 className="text-lg font-bold tracking-tight uppercase">
          DarGlobal <span className="text-[#2563EB]">&amp;</span> Wasalt AI
        </h1>
        <span className="text-xs text-neutral-400 font-mono border border-neutral-700 px-2 py-0.5 ml-2">
          v0.1.0-alpha
        </span>
      </div>
      <div className="flex items-center space-x-4 text-xs font-mono text-neutral-300">
        <span className="flex items-center">
          <span className="w-2 h-2 bg-emerald-500 mr-2"></span>
          SYS: OPERATIONAL
        </span>
        <span className="text-neutral-500">|</span>
        <span>LATENCY: &lt;120ms</span>
      </div>
    </header>
  );
}
