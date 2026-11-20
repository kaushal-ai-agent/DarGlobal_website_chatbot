import React from 'react';
import Header from '../components/Header';
import ChatWindow from '../components/ChatWindow';
import { useChat } from '../hooks/useChat';

export default function ChatPage() {
  const { messages, loading, sendMessage } = useChat();

  return (
    <div className="min-h-screen flex flex-col bg-[#F8FAFC]">
      <Header />
      <main className="flex-1 flex justify-center">
        <ChatWindow
          messages={messages}
          loading={loading}
          onSendMessage={sendMessage}
        />
      </main>
    </div>
  );
}
