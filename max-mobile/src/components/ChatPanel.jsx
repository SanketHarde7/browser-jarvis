import { useEffect, useRef } from 'react'
import { AnimatePresence, motion } from 'framer-motion'

export default function ChatPanel({
  messages = [],
  isProcessing = false,
  userName = 'Arjun',
}) {
  const scrollRef = useRef(null)

  useEffect(() => {
    if (!scrollRef.current) return
    scrollRef.current.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: 'smooth',
    })
  }, [messages, isProcessing])

  return (
    <section className="chat-layer" aria-label="Conversation">
      <div ref={scrollRef} className="chat-scroll">
        <AnimatePresence mode="popLayout">
          {messages.length === 0 && !isProcessing && (
            <motion.div
              key="greeting-card"
              className="greeting-card glass"
              initial={{ opacity: 0, y: 18, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -8, scale: 0.98 }}
              transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
            >
              <div>
                <h2>Good Morning, {userName}</h2>
                <p>How can I help you today?</p>
              </div>
              <span aria-hidden="true">›</span>
            </motion.div>
          )}

          {messages.map((msg, idx) => {
            const isUser = msg.role === 'user'
            return (
              <motion.article
                key={`${msg.role}-${idx}-${msg.content?.slice(0, 16)}`}
                className={`message-row ${isUser ? 'message-row-user' : 'message-row-ai'}`}
                initial={{ opacity: 0, y: 12, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: -8, scale: 0.98 }}
                transition={{ duration: 0.26, ease: [0.16, 1, 0.3, 1] }}
              >
                <div className={`message-bubble glass ${isUser ? 'message-user' : 'message-ai'}`}>
                  <span>{isUser ? 'You' : 'MAX'}</span>
                  <p>{msg.content}</p>
                </div>
              </motion.article>
            )
          })}

          {isProcessing && (
            <motion.article
              key="typing"
              className="message-row message-row-ai"
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
            >
              <div className="message-bubble message-ai glass typing-bubble">
                <span>MAX</span>
                <div className="typing-dots" aria-label="MAX is thinking">
                  {[0, 1, 2].map((dot) => (
                    <i key={dot} />
                  ))}
                </div>
              </div>
            </motion.article>
          )}
        </AnimatePresence>
      </div>
    </section>
  )
}
