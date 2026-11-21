import React, { useState, useEffect } from 'react';
import { ChevronDown, ChevronRight, CheckCircle2, Loader2, ArrowRight, Brain, Cpu, Database, Wrench } from 'lucide-react';

export default function AgentActivityCard({ activity, isStreaming }) {
  // While streaming, keep open by default; when completed, collapse into a summary bar
  const [isExpanded, setIsExpanded] = useState(isStreaming);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [expandedDetails, setExpandedDetails] = useState({});

  useEffect(() => {
    setIsExpanded(isStreaming);
  }, [isStreaming]);

  // Live elapsed timer while streaming is active
  useEffect(() => {
    let interval = null;
    if (isStreaming) {
      const startTime = Date.now();
      interval = setInterval(() => {
        setElapsedSeconds(((Date.now() - startTime) / 1000).toFixed(1));
      }, 100);
    }
    return () => {
      if (interval) clearInterval(interval);
    };
  }, [isStreaming]);

  if (!activity || !activity.steps || activity.steps.length === 0) {
    if (!isStreaming) return null;
    return (
      <div className="mb-3 border border-[#E0E0E0] bg-[#FFFFFF] p-2.5 flex items-center justify-between text-xs font-mono text-[#0F172A]">
        <div className="flex items-center space-x-2">
          <Loader2 className="w-3.5 h-3.5 text-[#2563EB] animate-spin" />
          <span>CONNECTING TO ORCHESTRATOR... ({elapsedSeconds}s)</span>
        </div>
        {elapsedSeconds > 12 && (
          <span className="text-[11px] text-amber-600 font-sans">
            Connecting... if delayed, click New Chat
          </span>
        )}
      </div>
    );
  }

  const steps = activity.steps || [];
  const currentAgent = activity.currentAgent || 'real_estate_orchestrator';
  const runningStep = steps.find((s) => s.isRunning);
  const toolStepCount = steps.filter((s) => s.type === 'tool_call' || s.type === 'tool_result').length;
  const totalDuration = activity.totalDurationMs
    ? `${(activity.totalDurationMs / 1000).toFixed(2)}s`
    : `${elapsedSeconds}s`;

  const toggleDetail = (idx) => {
    setExpandedDetails((prev) => ({ ...prev, [idx]: !prev[idx] }));
  };

  return (
    <div className="mb-3 w-full border border-[#E0E0E0] bg-[#FFFFFF] text-[#0F172A]">
      {/* Summary Header / Toggle Bar */}
      <div
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex items-center justify-between p-2.5 cursor-pointer hover:bg-neutral-50 transition-colors select-none text-xs font-mono"
      >
        <div className="flex items-center space-x-2">
          {isStreaming ? (
            <span className="flex items-center space-x-1.5 text-[#2563EB] font-bold">
              <span className="w-2 h-2 bg-[#2563EB] animate-pulse"></span>
              <span>
                {currentAgent}
                {runningStep ? ` : ${runningStep.name}...` : ' : reasoning...'}
              </span>
            </span>
          ) : (
            <span className="flex items-center space-x-1.5 text-neutral-700">
              <Cpu className="w-3.5 h-3.5 text-[#0F172A]" />
              <span className="font-semibold">ADK Execution Activity</span>
              <span className="text-neutral-400">·</span>
              <span className="text-neutral-500">
                {toolStepCount > 0 ? `${toolStepCount} tool${toolStepCount > 1 ? 's' : ''}` : 'Direct reasoning'} ({currentAgent})
              </span>
            </span>
          )}
        </div>

        <div className="flex items-center space-x-3 text-neutral-500">
          <span className="font-mono text-[11px]">{totalDuration}</span>
          {isExpanded ? (
            <ChevronDown className="w-3.5 h-3.5 text-neutral-600" />
          ) : (
            <ChevronRight className="w-3.5 h-3.5 text-neutral-600" />
          )}
        </div>
      </div>

      {/* Expanded Activity Timeline */}
      {isExpanded && (
        <div className="border-t border-[#E0E0E0] p-3 space-y-2.5 bg-[#F8FAFC]">
          {steps.map((step, idx) => {
            if (step.type === 'agent_start') {
              return (
                <div
                  key={idx}
                  className="flex items-center justify-between text-[11px] font-mono text-neutral-600 py-1 border-b border-neutral-200"
                >
                  <div className="flex items-center space-x-2">
                    <Cpu className="w-3 h-3 text-[#2563EB]" />
                    <span className="text-neutral-400">ACTIVE:</span>
                    <span className="font-semibold text-[#0F172A]">{step.author}</span>
                  </div>
                  {step.isRunning && (
                    <div className="flex items-center space-x-1.5 text-[#2563EB]">
                      <Loader2 className="w-3 h-3 animate-spin" />
                      <span className="text-[10px]">Processing...</span>
                    </div>
                  )}
                </div>
              );
            }

            if (step.type === 'agent_change') {
              return (
                <div
                  key={idx}
                  className="flex items-center space-x-2 text-[11px] font-mono text-neutral-600 py-1 border-b border-neutral-200"
                >
                  <ArrowRight className="w-3 h-3 text-[#2563EB]" />
                  <span className="text-neutral-400">HANDOFF:</span>
                  <span className="font-semibold text-neutral-700">{step.from_agent}</span>
                  <span className="text-neutral-400">→</span>
                  <span className="font-bold text-[#0F172A] bg-white px-1.5 py-0.5 border border-[#E0E0E0]">
                    {step.to_agent}
                  </span>
                </div>
              );
            }

            if (step.type === 'thinking') {
              return (
                <div
                  key={idx}
                  className="border border-[#E0E0E0] bg-white p-2.5 text-xs text-neutral-600 font-sans"
                >
                  <div className="flex items-center space-x-1.5 text-[10px] font-mono text-neutral-400 uppercase mb-1">
                    <Brain className="w-3 h-3 text-[#2563EB]" />
                    <span>Agent Reasoning Thought</span>
                  </div>
                  <p className="italic leading-relaxed whitespace-pre-wrap">{step.text}</p>
                </div>
              );
            }

            if (step.type === 'tool_call' || step.type === 'tool_result') {
              const isDone = !step.isRunning;
              const hasDetails = step.args || step.response_preview;
              const isDetailsOpen = !!expandedDetails[idx];

              return (
                <div
                  key={idx}
                  className="border border-[#E0E0E0] bg-white text-xs font-mono"
                >
                  <div
                    onClick={() => hasDetails && toggleDetail(idx)}
                    className={`flex items-center justify-between p-2 ${
                      hasDetails ? 'cursor-pointer hover:bg-neutral-50' : ''
                    }`}
                  >
                    <div className="flex items-center space-x-2">
                      {step.isRunning ? (
                        <Loader2 className="w-3.5 h-3.5 text-[#2563EB] animate-spin flex-shrink-0" />
                      ) : (
                        <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600 flex-shrink-0" />
                      )}
                      <span className="font-bold text-[#0F172A]">{step.name}</span>
                      <span className="text-[10px] text-neutral-400">({step.author})</span>
                    </div>

                    <div className="flex items-center space-x-2">
                      {step.duration_ms !== undefined && (
                        <span className="text-[10px] bg-neutral-100 text-neutral-600 px-1.5 py-0.5 border border-[#E0E0E0]">
                          {step.duration_ms < 1000
                            ? `${step.duration_ms.toFixed(0)}ms`
                            : `${(step.duration_ms / 1000).toFixed(2)}s`}
                        </span>
                      )}
                      {hasDetails && (
                        <span className="text-[10px] text-neutral-400">
                          {isDetailsOpen ? '▲ Hide' : '▼ Inspect'}
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Collapsible Tool Arguments & Response Data */}
                  {isDetailsOpen && (
                    <div className="border-t border-[#E0E0E0] p-2 bg-neutral-50 space-y-2 text-[11px] overflow-x-auto">
                      {step.args && (
                        <div>
                          <span className="text-neutral-500 uppercase font-bold block mb-0.5">Input Arguments:</span>
                          <pre className="bg-white p-1.5 border border-[#E0E0E0] text-neutral-700 whitespace-pre-wrap">
                            {JSON.stringify(step.args, null, 2)}
                          </pre>
                        </div>
                      )}

                      {step.response_preview && (
                        <div>
                          <span className="text-neutral-500 uppercase font-bold block mb-0.5">Execution Result Preview:</span>
                          <pre className="bg-white p-1.5 border border-[#E0E0E0] text-neutral-700 whitespace-pre-wrap">
                            {JSON.stringify(step.response_preview, null, 2)}
                          </pre>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            }

            return null;
          })}
        </div>
      )}
    </div>
  );
}
